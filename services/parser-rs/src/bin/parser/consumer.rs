//! Consume documents.fetched like services/parser's `consume`: the same group, manual commits
//! after each message, and the same skip and stop rules.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use rdkafka::consumer::{BaseConsumer, CommitMode, Consumer};
use rdkafka::error::KafkaError;
use rdkafka::types::RDKafkaErrorCode;
use rdkafka::{ClientConfig, Message as _, Offset, TopicPartitionList};

use crate::BoxError;
use crate::db::Store;
use crate::log;
use crate::metrics::Metrics;
use crate::service::{Failure, accession_no, parse_one};

pub const TOPIC: &str = "documents.fetched";
pub const GROUP_ID: &str = "parser-python"; // the Python parser's group: a swap keeps its offsets

/// The topic and group to consume: PARSER_TOPIC and PARSER_GROUP when set (the backfill parser
/// reads documents.backfill as parser-backfill), else TOPIC and GROUP_ID. Same in Python.
pub fn topic_and_group(var: impl Fn(&str) -> Option<String>) -> (String, String) {
    let get = |key: &str, default: &str| {
        var(key)
            .filter(|v| !v.is_empty())
            .unwrap_or_else(|| default.to_string())
    };
    (get("PARSER_TOPIC", TOPIC), get("PARSER_GROUP", GROUP_ID))
}

const SASL_VARS: [&str; 3] = [
    "KAFKA_SASL_MECHANISM",
    "KAFKA_SASL_USERNAME",
    "KAFKA_SASL_PASSWORD",
];
const MECHANISMS: [&str; 2] = ["SCRAM-SHA-512", "SCRAM-SHA-256"];

/// Connection and auth settings from the environment, as libs/kafka's `client_config` builds
/// them for the Python services: KAFKA_BOOTSTRAP_SERVERS (default localhost:9092),
/// KAFKA_SECURITY_PROTOCOL (PLAINTEXT, SASL_PLAINTEXT or SASL_SSL), KAFKA_SASL_MECHANISM
/// (SCRAM-SHA-512 or SCRAM-SHA-256), KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD (required with
/// SASL, rejected without it, so a half-applied rollout fails loudly), and
/// KAFKA_SSL_CA_LOCATION (SASL_SSL only).
pub fn client_config(
    var: impl Fn(&str) -> Option<String>,
) -> Result<Vec<(&'static str, String)>, String> {
    let get = |key: &str| var(key).filter(|v| !v.is_empty());
    let mut config = vec![(
        "bootstrap.servers",
        get("KAFKA_BOOTSTRAP_SERVERS").unwrap_or_else(|| "localhost:9092".to_string()),
    )];
    let protocol = get("KAFKA_SECURITY_PROTOCOL")
        .unwrap_or_else(|| "PLAINTEXT".to_string())
        .to_uppercase();
    match protocol.as_str() {
        "PLAINTEXT" => {
            let mut set = Vec::new();
            for key in SASL_VARS {
                if get(key).is_some() {
                    set.push(key);
                }
            }
            if !set.is_empty() {
                return Err(format!(
                    "{} set but KAFKA_SECURITY_PROTOCOL is PLAINTEXT",
                    set.join(", ")
                ));
            }
        }
        "SASL_PLAINTEXT" | "SASL_SSL" => {
            let mechanism = get("KAFKA_SASL_MECHANISM")
                .unwrap_or_else(|| "SCRAM-SHA-512".to_string())
                .to_uppercase();
            if !MECHANISMS.contains(&mechanism.as_str()) {
                return Err(format!(
                    "KAFKA_SASL_MECHANISM={mechanism:?}: expected one of {MECHANISMS:?}"
                ));
            }
            let (Some(username), Some(password)) =
                (get("KAFKA_SASL_USERNAME"), get("KAFKA_SASL_PASSWORD"))
            else {
                return Err(format!(
                    "{protocol} needs KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD"
                ));
            };
            let ca = get("KAFKA_SSL_CA_LOCATION").filter(|_| protocol == "SASL_SSL");
            config.push(("security.protocol", protocol.clone()));
            config.push(("sasl.mechanism", mechanism));
            config.push(("sasl.username", username));
            config.push(("sasl.password", password));
            if let Some(ca) = ca {
                config.push(("ssl.ca.location", ca));
            }
        }
        other => {
            return Err(format!(
                "KAFKA_SECURITY_PROTOCOL={other:?}: expected PLAINTEXT, SASL_PLAINTEXT or SASL_SSL"
            ));
        }
    }
    Ok(config)
}

pub struct Message {
    pub partition: i32,
    pub offset: i64,
    pub payload: Option<Vec<u8>>,
}

pub trait Source {
    /// The next message, or None when there is none within `timeout`.
    fn poll(&mut self, timeout: Duration) -> Result<Option<Message>, BoxError>;
    /// Commit past `msg`, synchronously.
    fn commit(&mut self, msg: &Message) -> Result<(), BoxError>;
}

pub fn consume(
    source: &mut impl Source,
    store: &mut impl Store,
    metrics: &Metrics,
    topic: &str,
    stop: &AtomicBool,
) -> Result<(), BoxError> {
    while !stop.load(Ordering::Relaxed) {
        let Some(msg) = source.poll(Duration::from_secs(1))? else {
            continue;
        };
        metrics.consumed.with_label_values(&[topic]).inc();
        match accession_no(msg.payload.as_deref()).and_then(|a| parse_one(store, metrics, &a)) {
            Ok(()) => {}
            Err(Failure::Skip(reason)) => {
                metrics.skipped.with_label_values(&[topic, "invalid"]).inc();
                log::error(
                    "parser",
                    &format!("skipping {}:{}: {reason}", msg.partition, msg.offset),
                    &[],
                );
            }
            Err(Failure::Fatal(e)) => return Err(e),
        }
        source.commit(&msg)?;
    }
    Ok(())
}

pub struct Kafka {
    consumer: BaseConsumer,
    topic: String,
}

impl Kafka {
    /// `settings` from `client_config`.
    pub fn subscribe(
        settings: &[(&'static str, String)],
        topic: &str,
        group: &str,
    ) -> Result<Kafka, KafkaError> {
        let mut config = ClientConfig::new();
        for (key, value) in settings {
            config.set(*key, value);
        }
        let consumer: BaseConsumer = config
            .set("group.id", group)
            .set("enable.auto.commit", "false")
            .set("auto.offset.reset", "earliest")
            .create()?;
        consumer.subscribe(&[topic])?;
        Ok(Kafka {
            consumer,
            topic: topic.to_string(),
        })
    }
}

impl Source for Kafka {
    fn poll(&mut self, timeout: Duration) -> Result<Option<Message>, BoxError> {
        match self.consumer.poll(timeout) {
            None => Ok(None),
            // the topic is created on first produce, not on subscribe
            Some(Err(e)) if is_unknown_topic(&e) => Ok(None),
            Some(Err(e)) => Err(e.into()),
            Some(Ok(m)) => Ok(Some(Message {
                partition: m.partition(),
                offset: m.offset(),
                payload: m.payload().map(<[u8]>::to_vec),
            })),
        }
    }

    fn commit(&mut self, msg: &Message) -> Result<(), BoxError> {
        let mut offsets = TopicPartitionList::new();
        offsets.add_partition_offset(&self.topic, msg.partition, Offset::Offset(msg.offset + 1))?;
        self.consumer.commit(&offsets, CommitMode::Sync)?;
        Ok(())
    }
}

fn is_unknown_topic(e: &KafkaError) -> bool {
    e.rdkafka_error_code() == Some(RDKafkaErrorCode::UnknownTopicOrPartition)
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

    use super::*;
    use crate::service::tests::{FakeStore, fixture};

    /// Yields its messages, then sets `stop`.
    struct FakeSource<'a> {
        messages: VecDeque<Option<Message>>,
        committed: Vec<i64>,
        stop: &'a AtomicBool,
    }

    impl<'a> FakeSource<'a> {
        fn new(payloads: &[Option<&[u8]>], stop: &'a AtomicBool) -> FakeSource<'a> {
            let mut offset = -1;
            let messages = payloads
                .iter()
                .map(|p| {
                    p.map(|p| {
                        offset += 1;
                        Message {
                            partition: 0,
                            offset,
                            payload: Some(p.to_vec()),
                        }
                    })
                })
                .collect();
            FakeSource {
                messages,
                committed: Vec::new(),
                stop,
            }
        }
    }

    impl Source for FakeSource<'_> {
        fn poll(&mut self, _: Duration) -> Result<Option<Message>, BoxError> {
            let next = self.messages.pop_front().flatten();
            if self.messages.is_empty() {
                self.stop.store(true, Ordering::Relaxed);
            }
            Ok(next)
        }

        fn commit(&mut self, msg: &Message) -> Result<(), BoxError> {
            self.committed.push(msg.offset);
            Ok(())
        }
    }

    const EVENT: &[u8] = br#"{"accession_no": "a", "form": "8-K"}"#;

    #[test]
    fn commits_parsed_and_skipped_messages_in_order() {
        let stop = AtomicBool::new(false);
        let missing: &[u8] = br#"{"accession_no": "b"}"#;
        let mut source = FakeSource::new(
            &[
                Some(EVENT),
                None,
                Some(&b"{"[..]),
                Some(missing),
                Some(EVENT),
            ],
            &stop,
        );
        let mut store = FakeStore::with(&[("a", "8-K", fixture("8-K"))]);
        let metrics = Metrics::new().unwrap();
        consume(&mut source, &mut store, &metrics, TOPIC, &stop).unwrap();
        assert_eq!(source.committed, [0, 1, 2, 3]);
        assert_eq!(store.writes, 2);
        assert_eq!(metrics.consumed.with_label_values(&[TOPIC]).get(), 4);
        assert_eq!(
            metrics.skipped.with_label_values(&[TOPIC, "invalid"]).get(),
            2
        );
    }

    #[test]
    fn fatal_errors_stop_without_committing() {
        let stop = AtomicBool::new(false);
        let mut source = FakeSource::new(&[Some(EVENT), Some(EVENT)], &stop);
        let mut store = FakeStore::with(&[("a", "8-K", b"".to_vec())]);
        let metrics = Metrics::new().unwrap();
        assert!(consume(&mut source, &mut store, &metrics, TOPIC, &stop).is_err());
        assert!(source.committed.is_empty());
        assert_eq!(source.messages.len(), 1);
    }

    #[test]
    fn stops_when_asked() {
        let stop = AtomicBool::new(true);
        let mut source = FakeSource::new(&[Some(EVENT)], &stop);
        let metrics = Metrics::new().unwrap();
        consume(
            &mut source,
            &mut FakeStore::default(),
            &metrics,
            TOPIC,
            &stop,
        )
        .unwrap();
        assert_eq!(source.messages.len(), 1);
    }

    #[test]
    fn topic_and_group_default_to_the_live_pipeline() {
        assert_eq!(
            topic_and_group(|_| None),
            (TOPIC.to_string(), GROUP_ID.to_string())
        );
        let backfill = |key: &str| match key {
            "PARSER_TOPIC" => Some("documents.backfill".to_string()),
            "PARSER_GROUP" => Some("parser-backfill".to_string()),
            _ => None,
        };
        assert_eq!(
            topic_and_group(backfill),
            (
                "documents.backfill".to_string(),
                "parser-backfill".to_string()
            )
        );
        assert_eq!(topic_and_group(|_| Some(String::new())).0, TOPIC);
    }

    fn env(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let pairs: Vec<(String, String)> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        move |key: &str| {
            pairs
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, v)| v.clone())
        }
    }

    const SASL: [(&str, &str); 4] = [
        ("KAFKA_BOOTSTRAP_SERVERS", "kafka.platform.svc.cluster.local:9094"),
        ("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT"),
        ("KAFKA_SASL_USERNAME", "parser"),
        ("KAFKA_SASL_PASSWORD", "secret"),
    ];

    fn without(key: &str) -> Vec<(&'static str, &'static str)> {
        SASL.iter().copied().filter(|(k, _)| *k != key).collect()
    }

    #[test]
    fn client_config_defaults_to_the_local_plaintext_stack() {
        assert_eq!(
            client_config(|_| None).unwrap(),
            vec![("bootstrap.servers", "localhost:9092".to_string())]
        );
        let plaintext = env(&[
            ("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
            ("KAFKA_SECURITY_PROTOCOL", "plaintext"),
        ]);
        assert_eq!(
            client_config(plaintext).unwrap(),
            vec![("bootstrap.servers", "kafka:9092".to_string())]
        );
    }

    #[test]
    fn client_config_sasl_scram() {
        assert_eq!(
            client_config(env(&SASL)).unwrap(),
            vec![
                (
                    "bootstrap.servers",
                    "kafka.platform.svc.cluster.local:9094".to_string()
                ),
                ("security.protocol", "SASL_PLAINTEXT".to_string()),
                ("sasl.mechanism", "SCRAM-SHA-512".to_string()),
                ("sasl.username", "parser".to_string()),
                ("sasl.password", "secret".to_string()),
            ]
        );
        let mut ssl = without("KAFKA_SECURITY_PROTOCOL");
        ssl.extend([
            ("KAFKA_SECURITY_PROTOCOL", "SASL_SSL"),
            ("KAFKA_SASL_MECHANISM", "scram-sha-256"),
            ("KAFKA_SSL_CA_LOCATION", "/ca.pem"),
        ]);
        let config = client_config(env(&ssl)).unwrap();
        assert!(config.contains(&("sasl.mechanism", "SCRAM-SHA-256".to_string())));
        assert!(config.contains(&("ssl.ca.location", "/ca.pem".to_string())));
    }

    #[test]
    fn client_config_rejects_inconsistent_env() {
        for missing in ["KAFKA_SASL_USERNAME", "KAFKA_SASL_PASSWORD"] {
            let err = client_config(env(&without(missing))).unwrap_err();
            assert!(err.contains("needs KAFKA_SASL_USERNAME and KAFKA_SASL_PASSWORD"), "{err}");
        }
        let err = client_config(env(&without("KAFKA_SECURITY_PROTOCOL"))).unwrap_err();
        assert!(
            err.contains("KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD set"),
            "{err}"
        );
        let mut plain = SASL.to_vec();
        plain.push(("KAFKA_SASL_MECHANISM", "PLAIN"));
        assert!(client_config(env(&plain)).is_err());
        let mut ssl = without("KAFKA_SECURITY_PROTOCOL");
        ssl.push(("KAFKA_SECURITY_PROTOCOL", "SSL"));
        assert!(client_config(env(&ssl)).is_err());
    }

    #[test]
    fn unknown_topic_is_not_an_error() {
        let unknown = KafkaError::MessageConsumption(RDKafkaErrorCode::UnknownTopicOrPartition);
        assert!(is_unknown_topic(&unknown));
        let other = KafkaError::MessageConsumption(RDKafkaErrorCode::BrokerTransportFailure);
        assert!(!is_unknown_topic(&other));
    }
}

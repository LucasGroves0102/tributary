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
    stop: &AtomicBool,
) -> Result<(), BoxError> {
    while !stop.load(Ordering::Relaxed) {
        let Some(msg) = source.poll(Duration::from_secs(1))? else {
            continue;
        };
        metrics.consumed.with_label_values(&[TOPIC]).inc();
        match accession_no(msg.payload.as_deref()).and_then(|a| parse_one(store, metrics, &a)) {
            Ok(()) => {}
            Err(Failure::Skip(reason)) => {
                metrics.skipped.with_label_values(&[TOPIC, "invalid"]).inc();
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

pub struct Kafka(BaseConsumer);

impl Kafka {
    pub fn subscribe(bootstrap_servers: &str) -> Result<Kafka, KafkaError> {
        let consumer: BaseConsumer = ClientConfig::new()
            .set("bootstrap.servers", bootstrap_servers)
            .set("group.id", GROUP_ID)
            .set("enable.auto.commit", "false")
            .set("auto.offset.reset", "earliest")
            .create()?;
        consumer.subscribe(&[TOPIC])?;
        Ok(Kafka(consumer))
    }
}

impl Source for Kafka {
    fn poll(&mut self, timeout: Duration) -> Result<Option<Message>, BoxError> {
        match self.0.poll(timeout) {
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
        offsets.add_partition_offset(TOPIC, msg.partition, Offset::Offset(msg.offset + 1))?;
        self.0.commit(&offsets, CommitMode::Sync)?;
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
        consume(&mut source, &mut store, &metrics, &stop).unwrap();
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
        assert!(consume(&mut source, &mut store, &metrics, &stop).is_err());
        assert!(source.committed.is_empty());
        assert_eq!(source.messages.len(), 1);
    }

    #[test]
    fn stops_when_asked() {
        let stop = AtomicBool::new(true);
        let mut source = FakeSource::new(&[Some(EVENT)], &stop);
        let metrics = Metrics::new().unwrap();
        consume(&mut source, &mut FakeStore::default(), &metrics, &stop).unwrap();
        assert_eq!(source.messages.len(), 1);
    }

    #[test]
    fn unknown_topic_is_not_an_error() {
        let unknown = KafkaError::MessageConsumption(RDKafkaErrorCode::UnknownTopicOrPartition);
        assert!(is_unknown_topic(&unknown));
        let other = KafkaError::MessageConsumption(RDKafkaErrorCode::BrokerTransportFailure);
        assert!(!is_unknown_topic(&other));
    }
}

//! Parse one stored document into sections (services/parser's `parse_one`), failing where the
//! Python service fails: `Skip` where it raises ValueError or KeyError (the consumer logs and
//! commits past the message), `Fatal` where it raises anything else (the service exits).

use std::time::Instant;

use filing_parser::{Error, Section, exhibit_section, parse};
use serde_json::Value;

use crate::BoxError;
use crate::db::Store;
use crate::log;
use crate::metrics::Metrics;

#[derive(Debug)]
pub enum Failure {
    Skip(String),
    Fatal(BoxError),
}

impl From<BoxError> for Failure {
    fn from(e: BoxError) -> Failure {
        Failure::Fatal(e)
    }
}

impl From<Error> for Failure {
    fn from(e: Error) -> Failure {
        match e {
            // ValueError in lxml or find_sections
            Error::NotXmlCompatible | Error::NotAnElement | Error::NoHeadings => {
                Failure::Skip(e.to_string())
            }
            // lxml ParserError, AssertionError
            Error::Empty | Error::DropRoot => Failure::Fatal(Box::new(e)),
        }
    }
}

impl std::fmt::Display for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Failure::Skip(reason) => f.write_str(reason),
            Failure::Fatal(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for Failure {}

pub fn parse_one(
    store: &mut impl Store,
    metrics: &Metrics,
    accession_no: &str,
) -> Result<(), Failure> {
    let Some((form, content)) = store.load_document(accession_no)? else {
        return Err(Failure::Skip(format!(
            "'no bronze document for {accession_no}'"
        )));
    };
    let t0 = Instant::now();
    let sections = match sections(store, accession_no, &form, &content) {
        Ok(s) => s,
        Err(e) => {
            metrics.parse_failures.with_label_values(&[&form]).inc();
            return Err(e);
        }
    };
    let seconds = t0.elapsed().as_secs_f64();
    store.write_sections(accession_no, &sections)?;
    metrics.parsed.with_label_values(&[&form]).inc();
    metrics
        .parse_seconds
        .with_label_values(&[&form])
        .observe(seconds);
    let ms = seconds * 1000.0;
    log::info(
        "parser",
        &format!(
            "{accession_no} {form}: {} sections in {ms:.0} ms ({} KB)",
            sections.len(),
            content.len() / 1024
        ),
        &[
            ("accession_no", Value::from(accession_no)),
            ("form", Value::from(form.as_str())),
            ("sections", Value::from(sections.len())),
            ("parse_ms", Value::from(ms.round() as i64)),
        ],
    );
    Ok(())
}

fn sections(
    store: &mut impl Store,
    accession_no: &str,
    form: &str,
    content: &[u8],
) -> Result<Vec<Section>, Failure> {
    let mut sections = parse(content, form)?;
    for e in store.load_exhibits(accession_no)? {
        sections.push(exhibit_section(
            &e.exhibit_type,
            e.description.as_deref(),
            &e.content,
        )?);
    }
    Ok(sections)
}

/// The accession number in a documents.fetched event.
pub fn accession_no(value: Option<&[u8]>) -> Result<String, Failure> {
    let fatal = |msg: &str| Failure::Fatal(msg.into());
    let value = value.ok_or_else(|| fatal("message has no value"))?;
    let event: Value = serde_json::from_slice(value).map_err(|e| Failure::Skip(e.to_string()))?;
    let Value::Object(event) = event else {
        return Err(fatal("event is not a JSON object"));
    };
    match event.get("accession_no") {
        Some(Value::String(a)) => Ok(a.clone()),
        Some(_) => Err(fatal("accession_no is not a string")),
        None => Err(Failure::Skip("'accession_no'".into())),
    }
}

#[cfg(test)]
pub mod tests {
    use std::collections::HashMap;
    use std::io::Read;
    use std::path::Path;

    use super::*;
    use crate::db::Exhibit;

    pub fn fixture(form: &str) -> Vec<u8> {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("../parser/tests/fixtures/nvda-{form}.htm.gz"));
        let mut out = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
            .read_to_end(&mut out)
            .unwrap();
        out
    }

    #[derive(Default)]
    pub struct FakeStore {
        pub documents: HashMap<String, (String, Vec<u8>)>,
        pub exhibits: HashMap<String, Vec<(String, Option<String>, Vec<u8>)>>,
        pub sections: HashMap<String, Vec<Section>>,
        pub writes: usize,
    }

    impl FakeStore {
        pub fn with(documents: &[(&str, &str, Vec<u8>)]) -> FakeStore {
            let mut store = FakeStore::default();
            for (acc, form, content) in documents {
                store
                    .documents
                    .insert(acc.to_string(), (form.to_string(), content.clone()));
            }
            store
        }
    }

    impl Store for FakeStore {
        fn load_document(&mut self, acc: &str) -> Result<Option<(String, Vec<u8>)>, BoxError> {
            Ok(self.documents.get(acc).cloned())
        }

        fn load_exhibits(&mut self, acc: &str) -> Result<Vec<Exhibit>, BoxError> {
            let mut exhibits = self.exhibits.get(acc).cloned().unwrap_or_default();
            exhibits.sort();
            Ok(exhibits
                .into_iter()
                .map(|(exhibit_type, description, content)| Exhibit {
                    exhibit_type,
                    description,
                    content,
                })
                .collect())
        }

        fn write_sections(&mut self, acc: &str, sections: &[Section]) -> Result<(), BoxError> {
            self.sections.insert(acc.to_string(), sections.to_vec());
            self.writes += 1;
            Ok(())
        }

        fn all_accessions(&mut self) -> Result<Vec<String>, BoxError> {
            let mut accs: Vec<String> = self.documents.keys().cloned().collect();
            accs.sort();
            Ok(accs)
        }
    }

    fn count(metrics: &Metrics, name: &str, label: &str) -> u64 {
        match name {
            "parsed" => metrics.parsed.with_label_values(&[label]).get(),
            "failures" => metrics.parse_failures.with_label_values(&[label]).get(),
            _ => metrics
                .parse_seconds
                .with_label_values(&[label])
                .get_sample_count(),
        }
    }

    #[test]
    fn parses_writes_and_counts() {
        let mut store = FakeStore::with(&[("a", "8-K", fixture("8-K"))]);
        store.exhibits.insert(
            "a".into(),
            vec![(
                "EX-99.1".into(),
                Some("PRESS RELEASE".into()),
                b"<p>NVIDIA announces</p>".to_vec(),
            )],
        );
        let metrics = Metrics::new().unwrap();
        for _ in 0..2 {
            parse_one(&mut store, &metrics, "a").unwrap();
        }
        let keys: Vec<&str> = store.sections["a"].iter().map(|s| s.key.as_str()).collect();
        assert_eq!(keys, ["8.01", "9.01", "EX-99.1"]);
        assert_eq!(store.sections["a"][2].title, "PRESS RELEASE");
        assert_eq!(count(&metrics, "parsed", "8-K"), 2);
        assert_eq!(count(&metrics, "seconds", "8-K"), 2);
        assert_eq!(count(&metrics, "failures", "8-K"), 0);
    }

    #[test]
    fn missing_document_is_skipped_without_a_parse_failure() {
        let metrics = Metrics::new().unwrap();
        let err = parse_one(&mut FakeStore::default(), &metrics, "x").unwrap_err();
        assert!(matches!(&err, Failure::Skip(r) if r == "'no bronze document for x'"));
        assert_eq!(metrics.parse_failures.with_label_values(&[""]).get(), 0);
    }

    #[test]
    fn value_errors_are_skipped_and_others_fatal() {
        let mut store = FakeStore::with(&[
            ("headings", "10-K", b"<p>no headings here</p>".to_vec()),
            ("empty", "10-K", b"".to_vec()),
            (
                "hidden",
                "10-K",
                b"<html style=\"display:none\"><p>x</p></html>".to_vec(),
            ),
        ]);
        let metrics = Metrics::new().unwrap();
        assert!(matches!(
            parse_one(&mut store, &metrics, "headings"),
            Err(Failure::Skip(_))
        ));
        assert!(matches!(
            parse_one(&mut store, &metrics, "empty"),
            Err(Failure::Fatal(_))
        ));
        assert!(matches!(
            parse_one(&mut store, &metrics, "hidden"),
            Err(Failure::Fatal(_))
        ));
        assert_eq!(count(&metrics, "failures", "10-K"), 3);
        assert_eq!(count(&metrics, "parsed", "10-K"), 0);
        assert_eq!(store.writes, 0);
    }

    #[test]
    fn exhibit_failures_count_against_the_document_form() {
        let mut store = FakeStore::with(&[("a", "8-K", fixture("8-K"))]);
        store
            .exhibits
            .insert("a".into(), vec![("EX-99.1".into(), None, b"".to_vec())]);
        let metrics = Metrics::new().unwrap();
        assert!(matches!(
            parse_one(&mut store, &metrics, "a"),
            Err(Failure::Fatal(_))
        ));
        assert_eq!(count(&metrics, "failures", "8-K"), 1);
        assert!(store.sections.is_empty());
    }

    #[test]
    fn reads_the_accession_number() {
        let ok = br#"{"accession_no": "0001045810-26-000078", "form": "8-K"}"#;
        assert_eq!(accession_no(Some(ok)).unwrap(), "0001045810-26-000078");
        assert!(matches!(
            accession_no(Some(b"not json")),
            Err(Failure::Skip(_))
        ));
        assert!(
            matches!(accession_no(Some(b"{\"cik\": \"1\"}")), Err(Failure::Skip(r)) if r == "'accession_no'")
        );
        assert!(matches!(accession_no(Some(b"[1]")), Err(Failure::Fatal(_))));
        assert!(matches!(accession_no(None), Err(Failure::Fatal(_))));
    }
}

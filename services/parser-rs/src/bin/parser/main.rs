//! Consume documents.fetched → parse sections → silver.filing_sections: the Rust build of the
//! `parser` service (services/parser), with the same topic, group, tables, metrics and logs.

mod consumer;
mod db;
mod log;
mod metrics;
mod service;
mod tls;

use std::process::ExitCode;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use consumer::{Kafka, consume, topic_and_group};
use db::Store;
use metrics::Metrics;
use service::parse_one;

pub type BoxError = Box<dyn std::error::Error + Send + Sync>;

const USAGE: &str = "usage: parser [-h] [--reparse-all]

Consume documents.fetched → parse sections → silver.filing_sections.

options:
  -h, --help     show this help message and exit
  --reparse-all  re-parse every stored document without Kafka, then exit";

fn main() -> ExitCode {
    let mut reparse_all = false;
    for arg in std::env::args().skip(1) {
        match arg.as_str() {
            "--reparse-all" => reparse_all = true,
            "-h" | "--help" => {
                println!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            _ => {
                eprintln!(
                    "{}\nparser: error: unrecognized arguments: {arg}",
                    USAGE.lines().next().unwrap_or_default()
                );
                return ExitCode::from(2);
            }
        }
    }
    match run(reparse_all) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("parser: {e}");
            ExitCode::FAILURE
        }
    }
}

fn run(reparse_all: bool) -> Result<(), BoxError> {
    log::configure()?;
    let url = std::env::var("DATABASE_URL").map_err(|_| "DATABASE_URL is not set")?;
    let mut db = tls::connect(&url)?;
    let metrics = Metrics::new()?;
    if reparse_all {
        for accession_no in db.all_accessions()? {
            parse_one(&mut db, &metrics, &accession_no)?;
        }
        return Ok(());
    }

    let stop = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, Arc::clone(&stop))?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, Arc::clone(&stop))?;
    let bootstrap =
        std::env::var("KAFKA_BOOTSTRAP_SERVERS").unwrap_or_else(|_| "localhost:9092".into());
    let (topic, group) = topic_and_group(|key| std::env::var(key).ok());
    let mut kafka = Kafka::subscribe(&bootstrap, &topic, &group)?;
    if let Some(port) = std::env::var("METRICS_PORT").ok().filter(|p| !p.is_empty()) {
        metrics::serve(metrics.registry.clone(), port.parse()?)?;
        log::info(
            "tributary_telemetry.metrics",
            &format!("serving metrics on port {port}"),
            &[],
        );
    }
    log::info("parser", &format!("consuming {topic}"), &[]);
    let result = consume(&mut kafka, &mut db, &metrics, &topic, &stop);
    drop(kafka); // leaves the group
    result?;
    log::info("parser", "stopped", &[]);
    Ok(())
}

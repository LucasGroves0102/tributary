//! The Python parser's Prometheus metrics (same names, help, labels and buckets), and the
//! /metrics server libs/telemetry's `serve_metrics` runs for workers.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::Duration;

use prometheus::{
    Encoder, HistogramOpts, HistogramVec, IntCounterVec, Opts, Registry, TextEncoder,
};

pub struct Metrics {
    pub registry: Registry,
    pub parsed: IntCounterVec,
    pub parse_failures: IntCounterVec,
    pub parse_seconds: HistogramVec,
    pub consumed: IntCounterVec,
    pub skipped: IntCounterVec,
}

impl Metrics {
    pub fn new() -> prometheus::Result<Metrics> {
        let registry = Registry::new();
        let counter = |name: &str, help: &str, labels: &[&str]| {
            let c = IntCounterVec::new(Opts::new(name, help), labels)?;
            registry.register(Box::new(c.clone()))?;
            Ok::<_, prometheus::Error>(c)
        };
        let parsed = counter(
            "tributary_documents_parsed_total",
            "Documents parsed into sections",
            &["form"],
        )?;
        let parse_failures = counter(
            "tributary_parse_failures_total",
            "Documents the parser raised on",
            &["form"],
        )?;
        let consumed = counter(
            "tributary_messages_consumed_total",
            "Kafka messages consumed",
            &["topic"],
        )?;
        let skipped = counter(
            "tributary_messages_skipped_total",
            "Consumed messages dropped without being processed",
            &["topic", "reason"],
        )?;
        let parse_seconds = HistogramVec::new(
            HistogramOpts::new(
                "tributary_parse_duration_seconds",
                "Time to split one document (and its exhibits) into sections",
            )
            .buckets(vec![0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]),
            &["form"],
        )?;
        registry.register(Box::new(parse_seconds.clone()))?;
        #[cfg(target_os = "linux")]
        registry.register(Box::new(
            prometheus::process_collector::ProcessCollector::for_self(),
        ))?;
        Ok(Metrics {
            registry,
            parsed,
            parse_failures,
            parse_seconds,
            consumed,
            skipped,
        })
    }

    pub fn render(&self) -> String {
        let mut out = Vec::new();
        let _ = TextEncoder::new().encode(&self.registry.gather(), &mut out);
        String::from_utf8(out).unwrap_or_default()
    }
}

/// Serve the registry on 0.0.0.0:`port` from a background thread, on any path.
pub fn serve(registry: Registry, port: u16) -> std::io::Result<()> {
    let listener = TcpListener::bind(("0.0.0.0", port))?;
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let _ = respond(stream, &registry);
        }
    });
    Ok(())
}

fn respond(mut stream: TcpStream, registry: &Registry) -> std::io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    let mut request = [0; 4096];
    let _ = stream.read(&mut request)?;
    let encoder = TextEncoder::new();
    let mut body = Vec::new();
    encoder
        .encode(&registry.gather(), &mut body)
        .map_err(std::io::Error::other)?;
    write!(
        stream,
        "HTTP/1.1 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        encoder.format_type(),
        body.len()
    )?;
    stream.write_all(&body)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exposes_the_python_names_and_labels() {
        let m = Metrics::new().unwrap();
        m.parsed.with_label_values(&["8-K"]).inc();
        m.parse_failures.with_label_values(&["10-K"]).inc();
        m.parse_seconds.with_label_values(&["8-K"]).observe(0.2);
        m.consumed.with_label_values(&["documents.fetched"]).inc();
        m.skipped
            .with_label_values(&["documents.fetched", "invalid"])
            .inc();
        let text = m.render();
        for line in [
            "# HELP tributary_documents_parsed_total Documents parsed into sections",
            "# TYPE tributary_documents_parsed_total counter",
            "tributary_documents_parsed_total{form=\"8-K\"} 1",
            "tributary_parse_failures_total{form=\"10-K\"} 1",
            "# TYPE tributary_parse_duration_seconds histogram",
            "tributary_parse_duration_seconds_bucket{form=\"8-K\",le=\"0.1\"} 0",
            "tributary_parse_duration_seconds_bucket{form=\"8-K\",le=\"0.25\"} 1",
            "tributary_parse_duration_seconds_bucket{form=\"8-K\",le=\"30\"} 1",
            "tributary_parse_duration_seconds_count{form=\"8-K\"} 1",
            "tributary_messages_consumed_total{topic=\"documents.fetched\"} 1",
            "tributary_messages_skipped_total{reason=\"invalid\",topic=\"documents.fetched\"} 1",
        ] {
            assert!(
                text.lines().any(|l| l == line),
                "missing {line:?} in\n{text}"
            );
        }
    }

    #[test]
    fn serves_metrics_over_http() {
        let m = Metrics::new().unwrap();
        m.parsed.with_label_values(&["10-Q"]).inc();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        serve(m.registry.clone(), port).unwrap();
        let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
        stream
            .write_all(b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")
            .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
        assert!(response.contains("tributary_documents_parsed_total{form=\"10-Q\"} 1\n"));
    }
}

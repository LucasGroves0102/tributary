//! Parse the documents listed on stdin, one JSON object per line:
//! `{"id", "path", "form"}` for a primary document or `{"id", "path", "exhibit_type",
//! "description"}` for an exhibit; `path` may be gzipped (`.gz`). Prints one JSON line per
//! document: its sections, or with `--bench` its size and parse time.

use std::io::{BufRead, BufWriter, Read, Write};
use std::time::Instant;

use filing_parser::{Section, exhibit_section, parse};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
struct Input {
    id: String,
    path: String,
    form: Option<String>,
    exhibit_type: Option<String>,
    description: Option<String>,
}

#[derive(Serialize)]
struct Output {
    id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    sections: Option<Vec<Section>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    seconds: Option<f64>,
}

fn read(path: &str) -> std::io::Result<Vec<u8>> {
    let raw = std::fs::read(path)?;
    if !path.ends_with(".gz") {
        return Ok(raw);
    }
    let mut out = Vec::new();
    flate2::read::GzDecoder::new(&raw[..]).read_to_end(&mut out)?;
    Ok(out)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bench = std::env::args().any(|a| a == "--bench");
    let mut out = BufWriter::new(std::io::stdout().lock());
    for line in std::io::stdin().lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let input: Input = serde_json::from_str(&line)?;
        let raw = read(&input.path)?;
        let t0 = Instant::now();
        let result = match &input.exhibit_type {
            Some(t) => exhibit_section(t, input.description.as_deref(), &raw).map(|s| vec![s]),
            None => parse(&raw, input.form.as_deref().unwrap_or("")),
        };
        let seconds = t0.elapsed().as_secs_f64();
        let (sections, error) = match result {
            Ok(s) => (Some(s), None),
            Err(e) => (None, Some(e.to_string())),
        };
        let output = if bench {
            Output {
                id: input.id,
                sections: None,
                error,
                bytes: Some(raw.len()),
                seconds: Some(seconds),
            }
        } else {
            Output {
                id: input.id,
                sections,
                error,
                bytes: None,
                seconds: None,
            }
        };
        serde_json::to_writer(&mut out, &output)?;
        out.write_all(b"\n")?;
    }
    Ok(())
}

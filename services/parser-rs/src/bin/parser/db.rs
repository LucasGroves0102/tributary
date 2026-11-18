//! Read documents from bronze, write sections to silver (services/parser's db.py).

use std::io::Read;
use std::path::{Path, PathBuf};

use filing_parser::{PARSER_VERSION, Section, Table};

use crate::BoxError;

pub const PARSER: &str = "rust";

pub struct Exhibit {
    pub exhibit_type: String,
    pub description: Option<String>,
    pub content: Vec<u8>,
}

pub trait Store {
    /// (form, uncompressed HTML), or None when the document isn't stored.
    fn load_document(&mut self, accession_no: &str) -> Result<Option<(String, Vec<u8>)>, BoxError>;
    /// The document's stored exhibits, uncompressed, by exhibit type.
    fn load_exhibits(&mut self, accession_no: &str) -> Result<Vec<Exhibit>, BoxError>;
    /// Replace the document's sections and their tables (rule 11), whichever parser wrote
    /// them, atomically. When this parser's stored sections are exactly the new ones, their
    /// rows stay (parsed_at too) and only parser_version and the tables change.
    fn write_sections(&mut self, accession_no: &str, sections: &[Section]) -> Result<(), BoxError>;
    fn all_accessions(&mut self) -> Result<Vec<String>, BoxError>;
}

/// A silver.filing_sections row as compared before a re-parse rewrites it (ordered by key first).
#[derive(PartialEq, Eq, PartialOrd, Ord)]
struct Stored {
    key: String,
    parser: String,
    part: Option<String>,
    item: String,
    title: String,
    text: String,
    start: i32,
    end: i32,
}

/// silver.filing_tables.rows (migration 067; services/parser db.py `table_rows`): spans only
/// when a cell spans more than one column, head only on a head row.
fn table_rows(t: &Table) -> String {
    let rows: Vec<serde_json::Value> = t
        .rows
        .iter()
        .map(|r| {
            let mut row = serde_json::Map::new();
            row.insert("start".into(), r.start.into());
            row.insert("end".into(), r.end.into());
            let cells: Vec<String> = r.cells.iter().map(|c| c.text.clone()).collect();
            row.insert("cells".into(), cells.into());
            if r.cells.iter().any(|c| c.span != 1) {
                let spans: Vec<u32> = r.cells.iter().map(|c| c.span).collect();
                row.insert("spans".into(), spans.into());
            }
            if r.head {
                row.insert("head".into(), true.into());
            }
            serde_json::Value::Object(row)
        })
        .collect();
    serde_json::Value::Array(rows).to_string()
}

fn gunzip(raw: &[u8]) -> std::io::Result<Vec<u8>> {
    let mut out = Vec::new();
    flate2::read::MultiGzDecoder::new(raw).read_to_end(&mut out)?;
    Ok(out)
}

/// A body that is a file (migration 150) missing from the body store, or no BODIES_DIR:
/// libs/bodies' MissingBody, a KeyError the Python service skips (so `Failure::Skip` here).
#[derive(Debug)]
pub struct MissingBody(pub String);

impl std::fmt::Display for MissingBody {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "'{}'", self.0) // as Python prints a KeyError
    }
}

impl std::error::Error for MissingBody {}

/// The body store's root (BODIES_DIR), if set.
pub fn bodies_dir() -> Option<PathBuf> {
    let dir = std::env::var("BODIES_DIR").ok()?;
    let dir = dir.trim();
    if dir.is_empty() {
        return None;
    }
    Some(PathBuf::from(dir))
}

fn is_hex(b: u8) -> bool {
    b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
}

/// `<dir>/<sha256[..2]>/<sha256>.zst` (libs/bodies `Store.path`), for a SHA-256 only.
fn body_path(dir: &Path, sha256: &str) -> Option<PathBuf> {
    if sha256.len() != 64 || !sha256.bytes().all(is_hex) {
        return None;
    }
    Some(dir.join(&sha256[..2]).join(format!("{sha256}.zst")))
}

/// A row's uncompressed body (libs/bodies `read_body`): content_gzip while the row has it, else
/// its zstd file in the body store.
pub fn read_body(
    dir: Option<&Path>,
    content_gzip: Option<&[u8]>,
    sha256: &str,
) -> Result<Vec<u8>, BoxError> {
    if let Some(gz) = content_gzip {
        return Ok(gunzip(gz)?);
    }
    let Some(dir) = dir else {
        let msg = format!("body {sha256} is a file and BODIES_DIR is not set");
        return Err(Box::new(MissingBody(msg)));
    };
    let Some(path) = body_path(dir, sha256) else {
        return Err(format!("not a SHA-256: {sha256:?}").into());
    };
    let data = match std::fs::read(path) {
        Ok(data) => data,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let msg = format!("no body file {sha256}");
            return Err(Box::new(MissingBody(msg)));
        }
        Err(e) => return Err(e.into()),
    };
    Ok(zstd::stream::decode_all(&data[..])?)
}

impl Store for postgres::Client {
    fn load_document(&mut self, accession_no: &str) -> Result<Option<(String, Vec<u8>)>, BoxError> {
        let row = self.query_opt(
            "SELECT form, content_gzip, content_sha256 FROM bronze.filing_documents \
             WHERE accession_no = $1",
            &[&accession_no],
        )?;
        Ok(match row {
            Some(r) => {
                let body = read_body(bodies_dir().as_deref(), r.get(1), r.get(2))?;
                Some((r.get(0), body))
            }
            None => None,
        })
    }

    fn load_exhibits(&mut self, accession_no: &str) -> Result<Vec<Exhibit>, BoxError> {
        let rows = self.query(
            "SELECT exhibit_type, description, content_gzip, content_sha256 \
             FROM bronze.filing_exhibits WHERE accession_no = $1 ORDER BY exhibit_type",
            &[&accession_no],
        )?;
        let dir = bodies_dir();
        rows.iter()
            .map(|r| {
                Ok(Exhibit {
                    exhibit_type: r.get(0),
                    description: r.get(1),
                    content: read_body(dir.as_deref(), r.get(2), r.get(3))?,
                })
            })
            .collect()
    }

    fn write_sections(&mut self, accession_no: &str, sections: &[Section]) -> Result<(), BoxError> {
        let mut new = Vec::with_capacity(sections.len());
        for s in sections {
            new.push(Stored {
                key: s.key.clone(),
                parser: PARSER.to_string(),
                part: s.part.clone(),
                item: s.item.clone(),
                title: s.title.clone(),
                text: s.text.clone(),
                start: i32::try_from(s.char_start)?,
                end: i32::try_from(s.char_end)?,
            });
        }
        new.sort();
        let mut tx = self.transaction()?;
        let mut old: Vec<Stored> = tx
            .query(
                "SELECT section_key, parser, part, item, title, text, char_start, char_end \
                 FROM silver.filing_sections WHERE accession_no = $1",
                &[&accession_no],
            )?
            .iter()
            .map(|r| Stored {
                key: r.get(0),
                parser: r.get(1),
                part: r.get(2),
                item: r.get(3),
                title: r.get(4),
                text: r.get(5),
                start: r.get(6),
                end: r.get(7),
            })
            .collect();
        old.sort();
        if old == new {
            tx.execute(
                "UPDATE silver.filing_sections SET parser_version = $1 \
                 WHERE accession_no = $2 AND parser_version <> $1",
                &[&PARSER_VERSION, &accession_no],
            )?;
            tx.execute(
                "DELETE FROM silver.filing_tables WHERE accession_no = $1",
                &[&accession_no],
            )?;
        } else {
            // the tables go with their sections (the foreign key cascades)
            tx.execute(
                "DELETE FROM silver.filing_sections WHERE accession_no = $1",
                &[&accession_no],
            )?;
            let insert = tx.prepare(
                "INSERT INTO silver.filing_sections \
                     (accession_no, section_key, parser, parser_version, part, item, title, \
                      text, char_start, char_end) \
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            )?;
            for s in &new {
                tx.execute(
                    &insert,
                    &[
                        &accession_no,
                        &s.key,
                        &PARSER,
                        &PARSER_VERSION,
                        &s.part,
                        &s.item,
                        &s.title,
                        &s.text,
                        &s.start,
                        &s.end,
                    ],
                )?;
            }
        }
        let insert = tx.prepare(
            "INSERT INTO silver.filing_tables \
                 (accession_no, section_key, parser, table_no, char_start, char_end, rows) \
             VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb)",
        )?;
        for s in sections {
            for (n, t) in s.tables.iter().enumerate() {
                let (n, start, end) = (
                    i32::try_from(n)?,
                    i32::try_from(t.start)?,
                    i32::try_from(t.end)?,
                );
                tx.execute(
                    &insert,
                    &[
                        &accession_no,
                        &s.key,
                        &PARSER,
                        &n,
                        &start,
                        &end,
                        &table_rows(t),
                    ],
                )?;
            }
        }
        tx.commit()?;
        Ok(())
    }

    fn all_accessions(&mut self) -> Result<Vec<String>, BoxError> {
        let rows = self.query(
            "SELECT accession_no FROM bronze.filing_documents ORDER BY filed",
            &[],
        )?;
        Ok(rows.iter().map(|r| r.get(0)).collect())
    }
}

#[cfg(test)]
mod tests {
    use std::time::Instant;

    use super::*;

    const ONE_SHOT: &str = "bb5aed90508491850444ef326207d0a2650c954e6223271d2373d0c0b98b5c4d";
    const STREAMED: &str = "72da9e835d7f48dfb6e90dad404e89178f850455f1e1669bc7c1d3d97bff37db";

    fn fixtures() -> PathBuf {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        root.join("../parser/tests/fixtures")
    }

    fn gz(name: &str) -> Vec<u8> {
        std::fs::read(fixtures().join(name)).unwrap()
    }

    /// Bodies libs/bodies (Python) wrote: one with its content size, one streamed without.
    #[test]
    fn reads_bodies_python_wrote() {
        let dir = fixtures().join("bodies");
        let html = read_body(Some(dir.as_path()), None, ONE_SHOT).unwrap();
        assert_eq!(html, gunzip(&gz("nvda-8-K.htm.gz")).unwrap());
        let html = read_body(Some(dir.as_path()), None, STREAMED).unwrap();
        assert_eq!(html, gunzip(&gz("cvr-8-K.htm.gz")).unwrap());
    }

    #[test]
    fn reads_the_column_while_the_row_has_it() {
        let column = gz("nvda-8-K.htm.gz");
        let html = read_body(None, Some(&column[..]), ONE_SHOT).unwrap();
        assert_eq!(html, gunzip(&column).unwrap());
    }

    #[test]
    fn a_missing_body_is_a_missing_body() {
        let empty = std::env::temp_dir();
        let sha = "0".repeat(64);
        for dir in [None, Some(empty.as_path())] {
            let err = read_body(dir, None, &sha).unwrap_err();
            assert!(err.downcast_ref::<MissingBody>().is_some(), "{err}");
        }
        let bad = read_body(Some(empty.as_path()), None, "../x").unwrap_err();
        assert!(bad.downcast_ref::<MissingBody>().is_none());
    }

    /// The read path before (gzip from the column, flate2) and after (zstd from a file): CI's
    /// rust job runs `cargo test --release read_path_timing -- --ignored --nocapture`.
    #[test]
    #[ignore]
    fn read_path_timing() {
        let name = format!("bodies-timing-{}", std::process::id());
        let dir = std::env::temp_dir().join(name);
        let names = [
            "nvda-10-K.htm.gz",
            "nvda-10-Q.htm.gz",
            "eix-10-Q.htm.gz",
            "nvda-8-K.htm.gz",
        ];
        let (mut before_total, mut after_total, mut raw_total) = (0.0, 0.0, 0usize);
        for name in names {
            let column = gz(name);
            let raw = gunzip(&column).unwrap();
            let sha = format!("{:064x}", raw.len()); // a name only: nothing checks the hash
            let path = body_path(&dir, &sha).unwrap();
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            let data = zstd::bulk::compress(&raw, 12).unwrap();
            std::fs::write(path, data).unwrap();
            let runs = 50;
            let t = Instant::now();
            for _ in 0..runs {
                assert_eq!(read_body(None, Some(&column[..]), &sha).unwrap(), raw);
            }
            let before = t.elapsed().as_secs_f64() / f64::from(runs);
            let t = Instant::now();
            for _ in 0..runs {
                assert_eq!(read_body(Some(dir.as_path()), None, &sha).unwrap(), raw);
            }
            let after = t.elapsed().as_secs_f64() / f64::from(runs);
            println!(
                "{name}: {} KB raw, gzip column {:.3} ms, zstd file {:.3} ms",
                raw.len() / 1024,
                before * 1000.0,
                after * 1000.0
            );
            before_total += before;
            after_total += after;
            raw_total += raw.len();
        }
        let mb = raw_total as f64 / 1e6;
        println!(
            "read path: gzip {:.0} MB/s, zstd file {:.0} MB/s",
            mb / before_total,
            mb / after_total
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
}

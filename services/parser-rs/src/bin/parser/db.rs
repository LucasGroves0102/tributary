//! Read documents from bronze, write sections to silver (services/parser's db.py).

use std::io::Read;

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

/// A silver.filing_sections row as compared before a re-parse rewrites it.
#[derive(PartialEq)]
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

impl Store for postgres::Client {
    fn load_document(&mut self, accession_no: &str) -> Result<Option<(String, Vec<u8>)>, BoxError> {
        let row = self.query_opt(
            "SELECT form, content_gzip FROM bronze.filing_documents WHERE accession_no = $1",
            &[&accession_no],
        )?;
        Ok(match row {
            Some(r) => Some((r.get(0), gunzip(r.get(1))?)),
            None => None,
        })
    }

    fn load_exhibits(&mut self, accession_no: &str) -> Result<Vec<Exhibit>, BoxError> {
        let rows = self.query(
            "SELECT exhibit_type, description, content_gzip FROM bronze.filing_exhibits \
             WHERE accession_no = $1 ORDER BY exhibit_type",
            &[&accession_no],
        )?;
        rows.iter()
            .map(|r| {
                Ok(Exhibit {
                    exhibit_type: r.get(0),
                    description: r.get(1),
                    content: gunzip(r.get(2))?,
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
        new.sort_by(|a, b| a.key.cmp(&b.key));
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
        old.sort_by(|a, b| a.key.cmp(&b.key));
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

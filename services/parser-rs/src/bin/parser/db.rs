//! Read documents from bronze, write sections to silver (services/parser's db.py).

use std::io::Read;

use filing_parser::{PARSER_VERSION, Section};

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
    /// Replace the document's sections, whichever parser wrote them, atomically.
    fn write_sections(&mut self, accession_no: &str, sections: &[Section]) -> Result<(), BoxError>;
    fn all_accessions(&mut self) -> Result<Vec<String>, BoxError>;
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
        let mut tx = self.transaction()?;
        tx.execute(
            "DELETE FROM silver.filing_sections WHERE accession_no = $1",
            &[&accession_no],
        )?;
        let insert = tx.prepare(
            "INSERT INTO silver.filing_sections \
                 (accession_no, section_key, parser, parser_version, part, item, title, text, \
                  char_start, char_end) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        )?;
        for s in sections {
            let (start, end) = (i32::try_from(s.char_start)?, i32::try_from(s.char_end)?);
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
                    &start,
                    &end,
                ],
            )?;
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

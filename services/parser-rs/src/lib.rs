//! FR3 filing parser in Rust: the algorithm specified in the docstring of
//! services/parser/src/filing_parser/sections.py, with identical output.

pub mod html;
mod pystr;
pub mod sections;
mod tables;

pub use html::html_to_text;
pub use sections::{PARSER_VERSION, Section, exhibit_section, find_sections, parse};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// lxml raises `ParserError: Document is empty`.
    Empty,
    /// lxml raises `ValueError: All strings must be XML compatible` when setting a text or
    /// tail with control characters, U+FFFE or U+FFFF.
    NotXmlCompatible,
    /// lxml's `drop_tree` asserts the element has a parent: a hidden document element.
    DropRoot,
    /// lxml's `xpath()` rejects the comment `fromstring` returns for a body holding only one.
    NotAnElement,
    /// `find_sections` raises `ValueError` from `zip(..., strict=True)` when the text has no
    /// Part or Item heading.
    NoHeadings,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Error::Empty => f.write_str("document is empty"),
            Error::NotXmlCompatible => f.write_str("text is not XML compatible"),
            Error::DropRoot => f.write_str("cannot drop the document element"),
            Error::NotAnElement => f.write_str("the document is a comment"),
            Error::NoHeadings => f.write_str("no Part or Item headings"),
        }
    }
}

impl std::error::Error for Error {}

//! Python `str` and `re` semantics the spec relies on: `str.strip()`, `str.isspace()`, and the
//! `\w`, `\d`, `\s` classes and IGNORECASE letter matching of `re` on str patterns.

use crate::tables;

fn in_table(table: &[(u32, u32)], c: char) -> bool {
    let c = c as u32;
    table
        .binary_search_by(|&(a, b)| {
            if b < c {
                std::cmp::Ordering::Less
            } else if a > c {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// `str.isspace()`, `re`'s `\s`.
pub fn is_space(c: char) -> bool {
    if c.is_ascii() {
        matches!(c, '\t'..='\r' | '\x1c'..=' ')
    } else {
        in_table(tables::SPACE, c)
    }
}

/// `re`'s `\w`.
pub fn is_word(c: char) -> bool {
    if c.is_ascii() {
        c.is_ascii_alphanumeric() || c == '_'
    } else {
        in_table(tables::ALNUM, c)
    }
}

/// `re`'s `\d`.
pub fn is_decimal(c: char) -> bool {
    if c.is_ascii() {
        c.is_ascii_digit()
    } else {
        in_table(tables::DECIMAL, c)
    }
}

/// The ASCII letter `c` matches under `re.IGNORECASE`, if any: ASCII letters plus
/// U+0130/U+0131 (i), U+017F (s) and U+212A (k).
pub fn fold_letter(c: char) -> Option<char> {
    match c {
        'a'..='z' => Some(c),
        'A'..='Z' => Some(c.to_ascii_lowercase()),
        '\u{130}' | '\u{131}' => Some('i'),
        '\u{17f}' => Some('s'),
        '\u{212a}' => Some('k'),
        _ => None,
    }
}

/// `str.upper()` of text made of `\d`, '.' and `fold_letter` letters.
pub fn upper(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            '\u{131}' => 'I',
            '\u{17f}' => 'S',
            _ => c.to_ascii_uppercase(),
        })
        .collect()
}

/// `str.strip()`.
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_space)
}

/// `_SPACES.sub(" ", s)`: runs of space, tab, U+00A0, U+200B, U+2009 and U+202F.
pub fn collapse_spaces(s: &str) -> std::borrow::Cow<'_, str> {
    let collapsible = |c: char| {
        matches!(
            c,
            ' ' | '\t' | '\u{a0}' | '\u{200b}' | '\u{2009}' | '\u{202f}'
        )
    };
    if !s.contains(|c: char| collapsible(c) && c != ' ') && !s.contains("  ") {
        return s.into();
    }
    let mut out = String::with_capacity(s.len());
    let mut in_run = false;
    for c in s.chars() {
        if collapsible(c) {
            if !in_run {
                out.push(' ');
            }
            in_run = true;
        } else {
            out.push(c);
            in_run = false;
        }
    }
    out.into()
}

/// Character offsets of sorted byte offsets into `s` (Python indexes str by code point).
pub struct CharIndex<'a> {
    s: &'a str,
    ascii: bool,
    byte: usize,
    chars: usize,
}

impl<'a> CharIndex<'a> {
    pub fn new(s: &'a str) -> Self {
        CharIndex {
            s,
            ascii: s.is_ascii(),
            byte: 0,
            chars: 0,
        }
    }

    /// The character offset of byte offset `at`; calls must not go backwards.
    pub fn at(&mut self, at: usize) -> usize {
        if self.ascii {
            return at;
        }
        debug_assert!(at >= self.byte);
        self.chars += self.s[self.byte..at].chars().count();
        self.byte = at;
        self.chars
    }
}

/// Number of characters in `s`.
pub fn char_len(s: &str) -> usize {
    if s.is_ascii() {
        s.len()
    } else {
        s.chars().count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classes() {
        assert!(
            is_space('\u{85}') && is_space('\u{3000}') && is_space('\x1f') && !is_space('\u{200b}')
        );
        assert!(is_word('é') && is_word('_') && !is_word('-'));
        assert!(is_decimal('\u{0663}') && !is_decimal('\u{00b2}'));
        assert_eq!(py_strip("\u{a0} a b\u{2028}"), "a b");
        assert_eq!(collapse_spaces("a \u{a0}\tb  c"), "a b c");
        assert_eq!(upper("1a\u{131}"), "1AI");
    }
}

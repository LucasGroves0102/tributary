//! JSON log lines on stdout in libs/telemetry's format: ts, level, logger, message, then extra
//! fields, written as Python's `json.dumps` writes them.

use std::io::Write;
use std::sync::atomic::{AtomicU8, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Debug = 10,
    Info = 20,
    Warning = 30,
    Error = 40,
    Critical = 50,
}

impl Level {
    pub fn parse(name: &str) -> Option<Level> {
        match name.to_ascii_uppercase().as_str() {
            "DEBUG" => Some(Level::Debug),
            "INFO" => Some(Level::Info),
            "WARNING" | "WARN" => Some(Level::Warning),
            "ERROR" => Some(Level::Error),
            "CRITICAL" | "FATAL" => Some(Level::Critical),
            _ => None,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Level::Debug => "DEBUG",
            Level::Info => "INFO",
            Level::Warning => "WARNING",
            Level::Error => "ERROR",
            Level::Critical => "CRITICAL",
        }
    }
}

static MIN_LEVEL: AtomicU8 = AtomicU8::new(Level::Info as u8);

/// Log at $LOG_LEVEL (default INFO).
pub fn configure() -> Result<(), String> {
    let name = std::env::var("LOG_LEVEL").unwrap_or_else(|_| "INFO".into());
    let level = Level::parse(&name).ok_or_else(|| format!("Unknown level: '{name}'"))?;
    MIN_LEVEL.store(level as u8, Ordering::Relaxed);
    Ok(())
}

pub fn info(logger: &str, message: &str, fields: &[(&str, Value)]) {
    log(Level::Info, logger, message, fields);
}

pub fn error(logger: &str, message: &str, fields: &[(&str, Value)]) {
    log(Level::Error, logger, message, fields);
}

fn log(level: Level, logger: &str, message: &str, fields: &[(&str, Value)]) {
    if (level as u8) < MIN_LEVEL.load(Ordering::Relaxed) {
        return;
    }
    let mut line = format_line(SystemTime::now(), level, logger, message, fields);
    line.push('\n');
    let _ = std::io::stdout().lock().write_all(line.as_bytes());
}

pub fn format_line(
    at: SystemTime,
    level: Level,
    logger: &str,
    message: &str,
    fields: &[(&str, Value)],
) -> String {
    let head = [
        ("ts", Value::from(timestamp(at))),
        ("level", Value::from(level.name())),
        ("logger", Value::from(logger)),
        ("message", Value::from(message)),
    ];
    let parts: Vec<String> = head
        .iter()
        .chain(fields)
        .map(|(k, v)| format!("{}: {}", dumps(&Value::from(*k)), dumps(v)))
        .collect();
    format!("{{{}}}", parts.join(", "))
}

/// `json.dumps(value)`: `", "` and `": "` separators, non-ASCII escaped.
fn dumps(value: &Value) -> String {
    let compact = match value {
        Value::Array(items) => {
            let items: Vec<String> = items.iter().map(dumps).collect();
            return format!("[{}]", items.join(", "));
        }
        Value::Object(map) => {
            let items: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", dumps(&Value::from(k.as_str())), dumps(v)))
                .collect();
            return format!("{{{}}}", items.join(", "));
        }
        other => other.to_string(),
    };
    let mut out = String::with_capacity(compact.len());
    for c in compact.chars() {
        if c.is_ascii() {
            out.push(c);
        } else {
            for unit in c.encode_utf16(&mut [0; 2]) {
                out.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    out
}

/// `datetime.isoformat(timespec="milliseconds")` in UTC: `2026-10-19T14:03:07.123+00:00`.
fn timestamp(at: SystemTime) -> String {
    let since = at.duration_since(UNIX_EPOCH).unwrap_or_default();
    let secs = since.as_secs();
    let (days, rem) = ((secs / 86_400) as i64, secs % 86_400);
    let (y, m, d) = civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}.{:03}+00:00",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60,
        since.subsec_millis()
    )
}

/// Days since 1970-01-01 → (year, month, day), proleptic Gregorian.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn formats_like_the_python_json_formatter() {
        let at = UNIX_EPOCH + Duration::from_millis(1_789_826_587_123);
        let line = format_line(
            at,
            Level::Info,
            "parser",
            "0001045810-26-000078 8-K: 2 sections in 3 ms (41 KB)",
            &[("sections", Value::from(2)), ("form", Value::from("8-K"))],
        );
        assert_eq!(
            line,
            r#"{"ts": "2026-09-19T14:03:07.123+00:00", "level": "INFO", "logger": "parser", "message": "0001045810-26-000078 8-K: 2 sections in 3 ms (41 KB)", "sections": 2, "form": "8-K"}"#
        );
    }

    #[test]
    fn escapes_non_ascii_like_json_dumps() {
        assert_eq!(
            dumps(&Value::from("Caf\u{e9} \u{1F600}\n")),
            r#""Café 😀\n""#
        );
    }

    #[test]
    fn dates() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(11_016), (2000, 2, 29));
        assert_eq!(civil_from_days(20_745), (2026, 10, 19));
    }

    #[test]
    fn levels() {
        assert_eq!(Level::parse("info"), Some(Level::Info));
        assert_eq!(Level::parse("WARNING"), Some(Level::Warning));
        assert_eq!(Level::parse("verbose"), None);
        assert!(Level::Debug < Level::Info);
    }
}

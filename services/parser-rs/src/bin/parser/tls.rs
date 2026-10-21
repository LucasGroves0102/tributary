//! Connect to Postgres with libpq's `sslmode` semantics, so the Rust service behaves like the
//! Python ones (psycopg/libpq) on the same `DATABASE_URL`.
//!
//! | sslmode | TLS | certificate check |
//! |---|---|---|
//! | `disable` | never | - |
//! | `prefer` (default) | if the server offers it, else plaintext | none, or the chain if a root certificate is configured |
//! | `require` | always | none, or the chain if a root certificate is configured |
//! | `verify-ca` | always | chain against the root certificate |
//! | `verify-full` | always | chain and host name |
//!
//! `sslmode` and `sslrootcert` come from the connection string (URL query or `key=value`),
//! else `PGSSLMODE` / `PGSSLROOTCERT`. The root certificate defaults to
//! `~/.postgresql/root.crt`; `sslrootcert=system` uses the system's trusted roots. tokio-postgres
//! only knows disable/prefer/require, so both options are removed from the string before it is
//! parsed and applied here. TLS 1.2 is the minimum, libpq's default. One difference: when
//! `prefer` verifies a chain and it fails, libpq retries in plaintext; this fails closed.

use std::path::{Path, PathBuf};

use postgres::config::SslMode as PgSslMode;
use postgres_native_tls::MakeTlsConnector;

use crate::BoxError;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SslMode {
    Disable,
    Prefer,
    Require,
    VerifyCa,
    VerifyFull,
}

impl SslMode {
    fn parse(value: &str) -> Result<Self, BoxError> {
        Ok(match value {
            "disable" => Self::Disable,
            "prefer" => Self::Prefer,
            "require" => Self::Require,
            "verify-ca" => Self::VerifyCa,
            "verify-full" => Self::VerifyFull,
            other => {
                return Err(format!(
                    "invalid sslmode \"{other}\": expected disable, prefer, require, \
                     verify-ca or verify-full"
                )
                .into());
            }
        })
    }
}

/// Where trusted roots come from when the certificate is verified.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Roots {
    System,
    File(PathBuf),
}

/// What the connector does: no TLS, or TLS with (optionally) a verified chain and host name.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Plan {
    Plain,
    Tls {
        /// false: TLS is used when the server supports it (prefer), true: required.
        required: bool,
        /// None: the certificate isn't checked.
        roots: Option<Roots>,
        verify_host: bool,
    },
}

/// The connection string without `sslmode`/`sslrootcert`, plus those two options.
#[derive(Debug, PartialEq, Eq)]
pub struct Options {
    pub rest: String,
    pub sslmode: Option<String>,
    pub sslrootcert: Option<String>,
}

const NO_ROOT_CERT: &str = "sslmode verify-ca/verify-full needs a root certificate: set \
     sslrootcert to a file, or sslrootcert=system for the system's trusted roots (the default, \
     ~/.postgresql/root.crt, does not exist)";

pub fn split_options(conn: &str) -> Result<Options, BoxError> {
    if conn.starts_with("postgres://") || conn.starts_with("postgresql://") {
        split_url(conn)
    } else {
        split_key_values(conn)
    }
}

fn split_url(url: &str) -> Result<Options, BoxError> {
    let mut options = Options {
        rest: url.to_string(),
        sslmode: None,
        sslrootcert: None,
    };
    let Some((base, query)) = url.split_once('?') else {
        return Ok(options);
    };
    let mut kept = Vec::new();
    for pair in query.split('&').filter(|p| !p.is_empty()) {
        let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
        match percent_decode(key)?.as_str() {
            "sslmode" => options.sslmode = Some(percent_decode(value)?),
            "sslrootcert" => options.sslrootcert = Some(percent_decode(value)?),
            _ => kept.push(pair),
        }
    }
    options.rest = if kept.is_empty() {
        base.to_string()
    } else {
        format!("{base}?{}", kept.join("&"))
    };
    Ok(options)
}

fn percent_decode(s: &str) -> Result<String, BoxError> {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            let hex = s
                .get(i + 1..i + 3)
                .and_then(|h| u8::from_str_radix(h, 16).ok())
                .ok_or_else(|| format!("invalid percent-encoding in connection URL: {s}"))?;
            out.push(hex);
            i += 3;
        } else {
            out.push(bytes[i]);
            i += 1;
        }
    }
    Ok(String::from_utf8(out)?)
}

/// libpq's `key = value` form: values may be single-quoted, `\` escapes the next character.
fn split_key_values(conn: &str) -> Result<Options, BoxError> {
    let mut options = Options {
        rest: String::new(),
        sslmode: None,
        sslrootcert: None,
    };
    let mut kept: Vec<String> = Vec::new();
    let mut chars = conn.chars().peekable();
    loop {
        while chars.next_if(|c| c.is_whitespace()).is_some() {}
        if chars.peek().is_none() {
            break;
        }
        let mut key = String::new();
        while let Some(c) = chars.next_if(|c| *c != '=' && !c.is_whitespace()) {
            key.push(c);
        }
        while chars.next_if(|c| c.is_whitespace()).is_some() {}
        if chars.next() != Some('=') {
            return Err(format!("missing \"=\" after \"{key}\" in connection string").into());
        }
        while chars.next_if(|c| c.is_whitespace()).is_some() {}
        let mut value = String::new();
        if chars.next_if_eq(&'\'').is_some() {
            loop {
                match chars.next() {
                    Some('\'') => break,
                    Some('\\') => value.extend(chars.next()),
                    Some(c) => value.push(c),
                    None => return Err("unterminated quoted string in connection string".into()),
                }
            }
        } else {
            while let Some(c) = chars.next_if(|c| !c.is_whitespace()) {
                if c == '\\' {
                    value.extend(chars.next());
                } else {
                    value.push(c);
                }
            }
        }
        match key.as_str() {
            "sslmode" => options.sslmode = Some(value),
            "sslrootcert" => options.sslrootcert = Some(value),
            _ => kept.push(format!(
                "{key}='{}'",
                value.replace('\\', "\\\\").replace('\'', "\\'")
            )),
        }
    }
    options.rest = kept.join(" ");
    Ok(options)
}

/// Decide the plan from the options, environment and the default root certificate (if it exists).
pub fn plan(
    options: &Options,
    env: impl Fn(&str) -> Option<String>,
    default_root: Option<PathBuf>,
) -> Result<Plan, BoxError> {
    let mode = match options.sslmode.clone().or_else(|| env("PGSSLMODE")) {
        Some(m) => SslMode::parse(&m)?,
        None => SslMode::Prefer,
    };
    let configured = options
        .sslrootcert
        .clone()
        .or_else(|| env("PGSSLROOTCERT"))
        .filter(|r| !r.is_empty());
    let roots = match configured {
        Some(r) if r == "system" => Some(Roots::System),
        Some(r) => Some(Roots::File(PathBuf::from(r))),
        None => default_root.map(Roots::File),
    };
    Ok(match mode {
        SslMode::Disable => Plan::Plain,
        // libpq: a root certificate, when there is one, is checked in these modes too.
        SslMode::Prefer => Plan::Tls {
            required: false,
            roots,
            verify_host: false,
        },
        SslMode::Require => Plan::Tls {
            required: true,
            roots,
            verify_host: false,
        },
        SslMode::VerifyCa | SslMode::VerifyFull => Plan::Tls {
            required: true,
            roots: Some(roots.ok_or(NO_ROOT_CERT)?),
            verify_host: mode == SslMode::VerifyFull,
        },
    })
}

/// libpq's default root certificate, if the file exists.
fn default_root_cert() -> Option<PathBuf> {
    let path = Path::new(&std::env::var_os("HOME")?).join(".postgresql/root.crt");
    path.is_file().then_some(path)
}

/// The PEM certificates in a bundle (native-tls parses one at a time).
pub fn pem_blocks(pem: &str) -> Vec<&str> {
    const END: &str = "-----END CERTIFICATE-----";
    let mut blocks = Vec::new();
    let mut rest = pem;
    while let Some(start) = rest.find("-----BEGIN CERTIFICATE-----") {
        let Some(len) = rest[start..].find(END) else {
            break;
        };
        let end = start + len + END.len();
        blocks.push(&rest[start..end]);
        rest = &rest[end..];
    }
    blocks
}

fn connector(
    roots: Option<&Roots>,
    verify_host: bool,
) -> Result<native_tls::TlsConnector, BoxError> {
    let mut builder = native_tls::TlsConnector::builder();
    builder.min_protocol_version(Some(native_tls::Protocol::Tlsv12));
    match roots {
        None => {
            builder.danger_accept_invalid_certs(true);
        }
        Some(Roots::System) => {}
        Some(Roots::File(path)) => {
            let pem = std::fs::read_to_string(path).map_err(|e| {
                format!("could not read root certificate file \"{}\": {e}", path.display())
            })?;
            let blocks = pem_blocks(&pem);
            if blocks.is_empty() {
                return Err(format!("no certificates in \"{}\"", path.display()).into());
            }
            builder.disable_built_in_roots(true);
            for block in blocks {
                let cert = native_tls::Certificate::from_pem(block.as_bytes())?;
                builder.add_root_certificate(cert);
            }
        }
    }
    if roots.is_some() && !verify_host {
        builder.danger_accept_invalid_hostnames(true);
    }
    Ok(builder.build()?)
}

/// `postgres::Client::connect` with libpq's TLS behaviour (module docs).
pub fn connect(conn: &str) -> Result<postgres::Client, BoxError> {
    let options = split_options(conn)?;
    let plan = plan(&options, |k| std::env::var(k).ok(), default_root_cert())?;
    let mut config: postgres::Config = options.rest.parse()?;
    match plan {
        Plan::Plain => {
            config.ssl_mode(PgSslMode::Disable);
            Ok(config.connect(postgres::NoTls)?)
        }
        Plan::Tls {
            required,
            roots,
            verify_host,
        } => {
            let mode = if required {
                PgSslMode::Require
            } else {
                PgSslMode::Prefer
            };
            config.ssl_mode(mode);
            let tls = MakeTlsConnector::new(connector(roots.as_ref(), verify_host)?);
            Ok(config.connect(tls)?)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opts(conn: &str) -> Options {
        split_options(conn).unwrap()
    }

    fn no_env(_: &str) -> Option<String> {
        None
    }

    fn plan_of(conn: &str) -> Plan {
        plan(&opts(conn), no_env, None).unwrap()
    }

    #[test]
    fn url_without_tls_options_is_unchanged() {
        let o = opts("postgresql://u:p@db:5432/tributary?application_name=parser");
        assert_eq!(o.rest, "postgresql://u:p@db:5432/tributary?application_name=parser");
        assert_eq!((o.sslmode, o.sslrootcert), (None, None));
    }

    #[test]
    fn url_tls_options_are_removed_and_decoded() {
        let o = opts(
            "postgres://u:p@db/t?sslmode=verify-full&connect_timeout=5&sslrootcert=%2Fetc%2Fca.pem",
        );
        assert_eq!(o.rest, "postgres://u:p@db/t?connect_timeout=5");
        assert_eq!(o.sslmode.as_deref(), Some("verify-full"));
        assert_eq!(o.sslrootcert.as_deref(), Some("/etc/ca.pem"));
        assert_eq!(opts("postgres://db/t?sslmode=require").rest, "postgres://db/t");
    }

    #[test]
    fn bad_percent_encoding_is_an_error() {
        assert!(split_options("postgres://db/t?sslrootcert=%zz").is_err());
    }

    #[test]
    fn key_value_form() {
        let o = opts("host=db  user = u password='p w\\'x' sslmode=require sslrootcert='/a b'");
        assert_eq!(o.rest, "host='db' user='u' password='p w\\'x'");
        assert_eq!(o.sslmode.as_deref(), Some("require"));
        assert_eq!(o.sslrootcert.as_deref(), Some("/a b"));
        assert!(split_options("host").is_err());
        assert!(split_options("password='open").is_err());
    }

    #[test]
    fn rest_parses_as_postgres_config() {
        for conn in [
            "postgresql://u:p@db:5432/t?sslmode=verify-ca&sslrootcert=x",
            "host=db user=u password='a b' sslmode=disable",
        ] {
            let config: postgres::Config = opts(conn).rest.parse().unwrap();
            assert_eq!(config.get_user(), Some("u"));
        }
    }

    #[test]
    fn prefer_is_the_default() {
        let prefer = Plan::Tls {
            required: false,
            roots: None,
            verify_host: false,
        };
        assert_eq!(plan_of("postgresql://db/t"), prefer);
        assert_eq!(plan_of("postgresql://db/t?sslmode=prefer"), prefer);
        // with a root certificate, prefer checks the chain (libpq)
        let o = opts("postgresql://db/t?sslrootcert=/ca.pem");
        assert_eq!(
            plan(&o, no_env, None).unwrap(),
            Plan::Tls {
                required: false,
                roots: Some(Roots::File("/ca.pem".into())),
                verify_host: false,
            }
        );
    }

    #[test]
    fn each_mode() {
        assert_eq!(plan_of("postgresql://db/t?sslmode=disable"), Plan::Plain);
        assert_eq!(
            plan_of("postgresql://db/t?sslmode=require"),
            Plan::Tls {
                required: true,
                roots: None,
                verify_host: false,
            }
        );
        assert_eq!(
            plan_of("postgresql://db/t?sslmode=verify-ca&sslrootcert=/ca.pem"),
            Plan::Tls {
                required: true,
                roots: Some(Roots::File("/ca.pem".into())),
                verify_host: false,
            }
        );
        assert_eq!(
            plan_of("postgresql://db/t?sslmode=verify-full&sslrootcert=system"),
            Plan::Tls {
                required: true,
                roots: Some(Roots::System),
                verify_host: true,
            }
        );
        assert!(plan(&opts("postgresql://db/t?sslmode=allow"), no_env, None).is_err());
    }

    #[test]
    fn require_with_a_root_certificate_verifies_the_chain() {
        let o = opts("postgresql://db/t?sslmode=require");
        assert_eq!(
            plan(&o, no_env, Some("/home/x/.postgresql/root.crt".into())).unwrap(),
            Plan::Tls {
                required: true,
                roots: Some(Roots::File("/home/x/.postgresql/root.crt".into())),
                verify_host: false,
            }
        );
    }

    #[test]
    fn verify_needs_a_root_certificate() {
        let o = opts("postgresql://db/t?sslmode=verify-full");
        assert!(plan(&o, no_env, None).is_err());
        let default = plan(&o, no_env, Some("/root.crt".into())).unwrap();
        assert_eq!(
            default,
            Plan::Tls {
                required: true,
                roots: Some(Roots::File("/root.crt".into())),
                verify_host: true,
            }
        );
    }

    #[test]
    fn environment_is_the_fallback() {
        let env = |k: &str| match k {
            "PGSSLMODE" => Some("verify-ca".to_string()),
            "PGSSLROOTCERT" => Some("/env.pem".to_string()),
            _ => None,
        };
        assert_eq!(
            plan(&opts("postgresql://db/t"), env, None).unwrap(),
            Plan::Tls {
                required: true,
                roots: Some(Roots::File("/env.pem".into())),
                verify_host: false,
            }
        );
        // the connection string wins
        assert_eq!(
            plan(&opts("postgresql://db/t?sslmode=disable"), env, None).unwrap(),
            Plan::Plain
        );
    }

    #[test]
    fn pem_bundle_is_split() {
        let pem = "junk\n-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----\n\
                   -----BEGIN CERTIFICATE-----\nBBB\n-----END CERTIFICATE-----\n";
        let blocks = pem_blocks(pem);
        assert_eq!(blocks.len(), 2);
        assert!(blocks[1].contains("BBB") && blocks[1].ends_with("-----END CERTIFICATE-----"));
        assert!(pem_blocks("nothing").is_empty());
    }

    #[test]
    fn connectors_build() {
        connector(None, false).unwrap();
        connector(Some(&Roots::System), true).unwrap();
        assert!(connector(Some(&Roots::File("/nonexistent.pem".into())), true).is_err());
    }
}

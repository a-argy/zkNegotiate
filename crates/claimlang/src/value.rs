//! Values that claims are evaluated over: the parsed `data` of signed
//! documents, plus a few bindings (`now`, `_signer`, ...).

use std::collections::BTreeMap;
use std::fmt;

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    Num(f64),
    Str(String),
    List(Vec<Value>),
    Map(BTreeMap<String, Value>),
}

impl Value {
    pub fn from_json(v: &serde_json::Value) -> Value {
        match v {
            serde_json::Value::Null => Value::Null,
            serde_json::Value::Bool(b) => Value::Bool(*b),
            serde_json::Value::Number(n) => Value::Num(n.as_f64().unwrap_or(f64::NAN)),
            serde_json::Value::String(s) => Value::Str(s.clone()),
            serde_json::Value::Array(a) => Value::List(a.iter().map(Value::from_json).collect()),
            serde_json::Value::Object(o) => {
                Value::Map(o.iter().map(|(k, v)| (k.clone(), Value::from_json(v))).collect())
            }
        }
    }

    pub fn type_name(&self) -> &'static str {
        match self {
            Value::Null => "null",
            Value::Bool(_) => "bool",
            Value::Num(_) => "number",
            Value::Str(_) => "string",
            Value::List(_) => "list",
            Value::Map(_) => "object",
        }
    }
}

/// A claim that failed to parse or evaluate. Distinct from a claim that
/// evaluated to `false`.
#[derive(Clone, Debug, PartialEq)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

macro_rules! bail {
    ($($arg:tt)*) => { return Err($crate::value::Error(format!($($arg)*))) };
}
pub(crate) use bail;

/// Days since 1970-01-01 for a string starting with `YYYY-MM-DD`.
pub fn parse_date(s: &str) -> Result<i64, Error> {
    let b = s.as_bytes();
    let digits = |r: std::ops::Range<usize>| -> Option<i64> {
        let part = s.get(r)?;
        if part.bytes().all(|c| c.is_ascii_digit()) { part.parse().ok() } else { None }
    };
    let (Some(y), Some(m), Some(d)) = (digits(0..4), digits(5..7), digits(8..10)) else {
        bail!("not a date (expected YYYY-MM-DD): {s:?}");
    };
    if b.get(4) != Some(&b'-') || b.get(7) != Some(&b'-') || !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        bail!("not a date (expected YYYY-MM-DD): {s:?}");
    }
    // Howard Hinnant's days_from_civil.
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Ok(era * 146_097 + doe - 719_468)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dates() {
        assert_eq!(parse_date("1970-01-01").unwrap(), 0);
        assert_eq!(parse_date("2025-04-11T22:46:03Z").unwrap() - parse_date("2025-03-12").unwrap(), 30);
        assert_eq!(parse_date("2024-03-01").unwrap() - parse_date("2024-02-28").unwrap(), 2);
        assert!(parse_date("2025-13-01").is_err());
        assert!(parse_date("April 1").is_err());
    }
}

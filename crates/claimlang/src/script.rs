//! Tier 3, "script": a Rhai program (https://rhai.rs), the most expressive
//! tier. Loops and helper functions are allowed; modules, `eval`, printing and
//! clocks are not, and execution is capped by an operation budget.

use rhai::{Dynamic, Engine, EvalAltResult, ImmutableString, Map, Scope};

use crate::value::{parse_date, Error, Value};

fn engine() -> Engine {
    let mut engine = Engine::new();
    engine
        .set_max_operations(1_000_000)
        .set_max_call_levels(16)
        .set_max_expr_depths(64, 32)
        .set_max_string_size(10_000)
        .set_max_array_size(10_000)
        .set_max_map_size(10_000);
    engine.on_print(|_| {});
    engine.on_debug(|_, _, _| {});
    engine.disable_symbol("eval");
    engine.register_fn(
        "days_between",
        |a: ImmutableString, b: ImmutableString| -> Result<i64, Box<EvalAltResult>> {
            let a = parse_date(&a).map_err(|e| e.0)?;
            let b = parse_date(&b).map_err(|e| e.0)?;
            Ok(b - a)
        },
    );
    engine
}

fn to_dynamic(v: &Value) -> Dynamic {
    match v {
        Value::Null => Dynamic::UNIT,
        Value::Bool(b) => (*b).into(),
        // Whole numbers become integers so `x == 30` and counting behave as
        // a script author expects; Rhai compares ints and floats freely.
        Value::Num(n) if n.fract() == 0.0 && n.abs() < 9.0e15 => (*n as i64).into(),
        Value::Num(n) => (*n).into(),
        Value::Str(s) => s.clone().into(),
        Value::List(l) => Dynamic::from_array(l.iter().map(to_dynamic).collect()),
        Value::Map(m) => {
            let map: Map = m.iter().map(|(k, v)| (k.as_str().into(), to_dynamic(v))).collect();
            Dynamic::from_map(map)
        }
    }
}

/// Checks a script compiles, without running it.
pub fn compile(src: &str) -> Result<(), Error> {
    engine().compile(src).map(|_| ()).map_err(|e| Error(format!("script error: {e}")))
}

pub fn evaluate(src: &str, bindings: &[(String, Value)]) -> Result<bool, Error> {
    let engine = engine();
    let mut scope = Scope::new();
    for (k, v) in bindings {
        scope.push_constant_dynamic(k.as_str(), to_dynamic(v));
    }
    let out: Dynamic = engine.eval_with_scope(&mut scope, src).map_err(|e| {
        let mut msg = format!("script error: {e}");
        if msg.contains("Variable not found") {
            let names: Vec<&str> = bindings.iter().map(|(k, _)| k.as_str()).collect();
            msg += &format!(" (available: {})", names.join(", "));
        }
        Error(msg)
    })?;
    out.as_bool().map_err(|t| Error(format!("a claim must be true/false, but this script produces a {t}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn docs() -> Vec<(String, Value)> {
        let book = serde_json::json!({
            "offers": [{"amount": 900_000}, {"amount": 1_200_000}, {"amount": 1_350_000.5}],
            "signed_at": "2025-04-11",
        });
        vec![("book".into(), Value::from_json(&book)), ("now".into(), Value::Str("2025-04-20".into()))]
    }

    #[test]
    fn scripts() {
        let d = docs();
        assert!(evaluate("book.offers.len() == 3", &d).unwrap());
        assert!(evaluate(
            "let n = 0; for o in book.offers { if o.amount > 1000000 { n += 1; } } n >= 2",
            &d
        )
        .unwrap());
        assert!(evaluate("book.offers.filter(|o| o.amount > 1.3e6).len() == 1", &d).unwrap());
        assert!(evaluate("days_between(book.signed_at, now) == 9", &d).unwrap());
        assert!(!evaluate("book.offers[0].amount >= 1000000", &d).unwrap());
    }

    #[test]
    fn sandbox() {
        let d = docs();
        assert!(evaluate("book.offers.len()", &d).is_err(), "non-boolean");
        assert!(evaluate("eval(\"true\")", &d).is_err());
        assert!(evaluate("import \"x\" as y; true", &d).is_err());
        assert!(evaluate("loop { }", &d).is_err(), "operation budget");
        assert!(evaluate("book = 1; true", &d).is_err(), "documents are constants");
    }
}

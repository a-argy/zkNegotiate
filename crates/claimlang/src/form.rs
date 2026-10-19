//! Tier 1, "form": a flat list of field comparisons joined by all/any.
//!
//! `{"combine": "all", "conditions": [{"field": "offer.amount", "op": ">", "value": 1300000}]}`
//!
//! Deliberately inexpressive: no arithmetic, nesting or lists. Each claim
//! reads as one sentence, and a model can fill it in under a JSON schema.

use serde::{Deserialize, Serialize};

use crate::formula::{BinOp, Expr};
use crate::value::{bail, Error, Value};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Form {
    pub combine: Combine,
    pub conditions: Vec<Condition>,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Combine {
    All,
    Any,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Condition {
    pub field: String,
    pub op: String,
    pub value: serde_json::Value,
}

pub fn parse(src: &str) -> Result<Form, Error> {
    let form: Form = serde_json::from_str(src).map_err(|e| Error(format!("malformed form: {e}")))?;
    if form.conditions.is_empty() {
        bail!("a form needs at least one condition");
    }
    Ok(form)
}

/// The exact text committed as the claim: re-serialized, so formatting
/// differences in the model's output do not change the public values.
pub fn canonical(form: &Form) -> String {
    serde_json::to_string(form).expect("form serializes")
}

pub fn to_expr(form: &Form) -> Result<Expr, Error> {
    let mut parts = Vec::new();
    for c in &form.conditions {
        let mut segments = c.field.split('.');
        let root = segments.next().unwrap_or_default();
        if root.is_empty() {
            bail!("empty field name");
        }
        let mut e = Expr::Var(root.to_string());
        for seg in segments {
            if seg.is_empty() {
                bail!("bad field name {:?}", c.field);
            }
            e = Expr::Field(Box::new(e), seg.to_string());
        }
        let op = match c.op.as_str() {
            "==" => BinOp::Eq,
            "!=" => BinOp::Ne,
            "<" => BinOp::Lt,
            "<=" => BinOp::Le,
            ">" => BinOp::Gt,
            ">=" => BinOp::Ge,
            other => bail!("unknown comparison {other:?}"),
        };
        let value = match &c.value {
            v @ (serde_json::Value::Number(_) | serde_json::Value::String(_) | serde_json::Value::Bool(_)) => {
                Value::from_json(v)
            }
            v => bail!("condition value must be a number, string or boolean, got {v}"),
        };
        parts.push(Expr::Binary(op, Box::new(e), Box::new(Expr::Lit(value))));
    }
    let op = if form.combine == Combine::All { BinOp::And } else { BinOp::Or };
    let mut it = parts.into_iter();
    let first = it.next().expect("non-empty");
    Ok(it.fold(first, |acc, p| Expr::Binary(op, Box::new(acc), Box::new(p))))
}

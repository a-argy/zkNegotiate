//! Native claim checker for the Python side and the experiments.
//!
//! `claimcheck eval`   JSON lines in, JSON lines out. Each request:
//!   {"tier": "formula", "claim": "...", "now": "2025-04-20",
//!    "docs": {"offer": {"signer": "...", "kind": "...", "signed_at": "...", "data": {...}}}}
//!   or, to evaluate one claim over many document sets, "docs_list": [{...}, ...].
//!   Response: {"ok": true, "canonical": "...", "result": true}
//!          or {"ok": true, "canonical": "...", "results": [true, false, null, ...], "errors": [...]}
//!          or {"ok": false, "error": "..."}
//!
//! `claimcheck bundle` One bundle (claimlang::BundleJson) on stdin; prints its
//!   public values, exactly as the zkVM guest would commit them.

use std::collections::BTreeMap;
use std::io::{BufRead, Write};

use claimlang::{bindings, canonical_claim, check_bundle, evaluate, BundleJson, DocumentPayload, Tier};
use serde::Deserialize;
use serde_json::{json, Value as Json};

type Docs = BTreeMap<String, DocumentPayload>;

#[derive(Deserialize)]
struct EvalRequest {
    tier: Tier,
    claim: String,
    now: String,
    #[serde(default)]
    docs: Option<Docs>,
    #[serde(default)]
    docs_list: Option<Vec<Docs>>,
}

fn eval_one(tier: Tier, claim: &str, docs: &Docs, now: &str) -> Result<bool, claimlang::Error> {
    let docs: Vec<(String, DocumentPayload)> = docs.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
    evaluate(tier, claim, &bindings(&docs, now)?)
}

fn handle(line: &str) -> Json {
    let req: EvalRequest = match serde_json::from_str(line) {
        Ok(r) => r,
        Err(e) => return json!({"ok": false, "error": format!("bad request: {e}")}),
    };
    let canonical = match canonical_claim(req.tier, &req.claim) {
        Ok(c) => c,
        Err(e) => return json!({"ok": false, "error": e.0}),
    };
    match (req.docs, req.docs_list) {
        (Some(docs), None) => match eval_one(req.tier, &canonical, &docs, &req.now) {
            Ok(r) => json!({"ok": true, "canonical": canonical, "result": r}),
            Err(e) => json!({"ok": false, "canonical": canonical, "error": e.0}),
        },
        (None, Some(list)) => {
            let mut results = Vec::with_capacity(list.len());
            let mut errors = Vec::new();
            for docs in &list {
                match eval_one(req.tier, &canonical, docs, &req.now) {
                    Ok(r) => results.push(json!(r)),
                    Err(e) => {
                        results.push(Json::Null);
                        if errors.len() < 5 {
                            errors.push(e.0);
                        }
                    }
                }
            }
            json!({"ok": true, "canonical": canonical, "results": results, "errors": errors})
        }
        _ => json!({"ok": false, "error": "give exactly one of docs / docs_list"}),
    }
}

fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    let stdin = std::io::stdin();
    let mut out = std::io::stdout().lock();
    match mode.as_str() {
        "eval" => {
            for line in stdin.lock().lines() {
                let line = line.expect("stdin");
                if line.trim().is_empty() {
                    continue;
                }
                writeln!(out, "{}", handle(&line)).unwrap();
                out.flush().unwrap();
            }
        }
        "bundle" => {
            let b: BundleJson = serde_json::from_reader(stdin.lock()).expect("bundle JSON");
            let r = b.into_bundle().and_then(|b| check_bundle(&b));
            let v = match r {
                Ok(pv) => json!({"ok": true, "public_values": pv.to_json()}),
                Err(e) => json!({"ok": false, "error": e.0}),
            };
            writeln!(out, "{v}").unwrap();
        }
        _ => {
            eprintln!("usage: claimcheck eval|bundle  (JSON on stdin)");
            std::process::exit(2);
        }
    }
}

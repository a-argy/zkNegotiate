"""Turning a natural-language claim into a checkable one, in three languages
("tiers") of increasing expressiveness:

  form     a flat list of field comparisons joined by all/any, filled in
           under a JSON schema whose field names are the documents' fields
  formula  one JavaScript-like boolean expression (arithmetic, list
           functions with lambdas, dates)
  script   a Rhai program

The model may also answer "unsupported" when the claim cannot be expressed
exactly (vague, missing data, beyond the language). Each answer is checked by
`claimcheck` on sample documents; errors go back to the model for a retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import llm
from .checker import Checker

TIERS = ["form", "formula", "script"]
MAX_ATTEMPTS = 3


@dataclass
class Field:
    name: str
    type: str  # number | string | bool | date | list
    description: str = ""
    item_fields: list["Field"] = field(default_factory=list)
    enum: list[str] | None = None


@dataclass
class DocSpec:
    label: str
    kind: str
    signer: str
    description: str
    fields: list[Field]


def render_catalog(docs: list[DocSpec]) -> str:
    lines = ["Signed documents available (field: type - meaning):"]
    for d in docs:
        lines.append(f"\n`{d.label}` - {d.description}, signed by {d.signer}")
        for f in d.fields:
            if f.type == "list":
                items = ", ".join(f"{i.name} ({i.type}{' - ' + i.description if i.description else ''})" for i in f.item_fields)
                lines.append(f"  {d.label}.{f.name}: list of objects, each with: {items}")
            else:
                extra = f" (one of: {', '.join(repr(e) for e in f.enum)})" if f.enum else ""
                lines.append(f"  {d.label}.{f.name}: {f.type}{extra} - {f.description}")
        lines.append(f"  {d.label}._signer: string - who signed it;  {d.label}._signed_at: date - when it was signed")
    lines.append('\nDates are strings "YYYY-MM-DD". `now` is today\'s date (a date string).')
    return "\n".join(lines)


def scalar_paths(docs: list[DocSpec]) -> list[str]:
    paths = []
    for d in docs:
        paths += [f"{d.label}.{f.name}" for f in d.fields if f.type != "list"]
        paths += [f"{d.label}._signer", f"{d.label}._signed_at"]
    return paths


COMMON = """You translate a negotiator's factual claim into a formal check over the signed documents below. The check is run on the real documents and proven in zero knowledge; the counterparty sees only your check and whether it holds, so they rely on it meaning exactly what the claim says.

Rules:
- The check must be true exactly when the claim is true: never weaker (passing when the claim is false) and never stronger.
- Use exactly the thresholds, units and conditions stated. Convert units: $1.3M = 1300000, 15k = 15000, two weeks = 14 days, 2% = 2 when the field is a percentage.
- If the claim cannot be expressed exactly with this language and these documents - because it is vague or subjective, needs information the documents do not contain, or needs features the language lacks - set "supported" to false. Do not approximate."""

LANGUAGES = {
    "form": """Language: a list of conditions, each `field op value`, combined with "all" (AND) or "any" (OR).
- op is one of == != < <= > >=; value is a number, string or boolean.
- Dates are compared as text ("2025-06-01"); there is no `now`, no arithmetic, no lists, no nesting.""",
    "formula": """Language: one JavaScript-like boolean expression.
- Fields: offer.amount, book.bids[0].amount. Literals: 1300000, 1_300_000, 1.3e6 (no $ or M), "text", true, false.
- Operators: + - * / %, == != < <= > >=, && || !, parentheses.
- Functions: len(list), sum(list, x => expr), avg(list, x => expr), min(list, x => expr), max(...), min(a, b), count(list, x => cond), all(list, x => cond), any(list, x => cond), filter(list, x => cond), map(list, x => expr), abs, round, floor, ceil, contains(list_or_text, value), lower(text), days_between(date1, date2) = days from date1 to date2.
- Methods work too: list.filter(x => cond).length, list.some(x => cond), list.every(x => cond).
- No variables, statements or loops.
Illustrative example (made-up documents; use only the documents listed below):
offer.amount > 1300000 && count(book.bids, b => b.amount >= 1e6) >= 2 && days_between(now, offer.expires_on) >= 7""",
    "script": """Language: a Rhai script (Rust-like scripting language).
- Documents are constants: offer.amount, book.bids[0].amount, book.bids.len().
- You may use let, if/else, for loops, arrays (.len(), .filter(|x| cond), .map(|x| expr), .reduce(|sum, x| sum + x, 0)), and the helper days_between(date1, date2) = days from date1 to date2.
- Integers and floats compare freely. Strings use double quotes.
- The script's final expression must be true or false.
Illustrative example (made-up documents; use only the documents listed below):
let n = 0;
for b in book.bids { if b.amount >= 1000000 { n += 1; } }
offer.amount > 1300000 && n >= 2 && days_between(now, offer.expires_on) >= 7""",
}


def schema(tier: str, docs: list[DocSpec]) -> dict:
    if tier == "form":
        return {
            "type": "object",
            "properties": {
                "supported": {"type": "boolean"},
                "combine": {"type": "string", "enum": ["all", "any"]},
                "conditions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": scalar_paths(docs)},
                            "op": {"type": "string", "enum": ["==", "!=", "<", "<=", ">", ">="]},
                            "value": {"type": ["number", "string", "boolean"]},
                        },
                        "required": ["field", "op", "value"],
                    },
                },
            },
            "required": ["supported", "combine", "conditions"],
        }
    key = "formula" if tier == "formula" else "script"
    return {
        "type": "object",
        "properties": {"supported": {"type": "boolean"}, key: {"type": "string"}},
        "required": ["supported", key],
    }


def system_prompt(tier: str, docs: list[DocSpec], extra: str = "") -> str:
    return "\n\n".join(p for p in [COMMON, LANGUAGES[tier], render_catalog(docs), extra] if p)


def claim_text(tier: str, answer: dict) -> str:
    if tier == "form":
        return json.dumps({"combine": answer.get("combine", "all"), "conditions": answer.get("conditions", [])})
    return (answer.get("formula") if tier == "formula" else answer.get("script")) or ""


@dataclass
class Formalization:
    tier: str
    supported: bool
    claim: str = ""  # submitted claim text (empty if unsupported)
    canonical: str = ""
    valid: bool = False  # parsed and evaluated on the sample documents
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


def formalize(
    checker: Checker,
    tier: str,
    nl_claim: str,
    docs: list[DocSpec],
    sample_docs: dict,
    now: str,
    system_extra: str = "",
    user_prefix: str = "Claim",
    seed: int = 0,
    temperature: float = 0.0,
) -> Formalization:
    """Asks the model for a formal version of `nl_claim`; retries with the
    checker's error message (up to MAX_ATTEMPTS)."""
    messages = [
        {"role": "system", "content": system_prompt(tier, docs, system_extra)},
        {"role": "user", "content": f'{user_prefix}: "{nl_claim}"'},
    ]
    out = Formalization(tier=tier, supported=False)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        out.attempts = attempt
        answer = llm.chat_json(messages, schema(tier, docs), seed=seed, temperature=temperature)
        if "_unparseable" in answer:
            out.errors.append("unparseable model output")
            messages += [
                {"role": "assistant", "content": answer["_unparseable"][:2000]},
                {"role": "user", "content": "That was not valid JSON. Answer again, briefly."},
            ]
            continue
        out.supported = bool(answer.get("supported"))
        if not out.supported:
            out.claim = ""
            return out
        out.claim = claim_text(tier, answer)
        r = checker.evaluate(tier, out.claim, sample_docs, now)
        if r.get("ok"):
            out.valid = True
            out.canonical = r["canonical"]
            return out
        out.errors.append(r["error"])
        messages += [
            {"role": "assistant", "content": json.dumps(answer)},
            {"role": "user", "content": f"Running that check failed: {r['error']}\nFix it and answer again."},
        ]
    return out


def describe_form(claim: str) -> str:
    """Plain-English rendering of a form claim."""
    f = json.loads(claim)
    joiner = " AND " if f["combine"] == "all" else " OR "
    def value(v):
        return f"{v:,}" if isinstance(v, (int, float)) and not isinstance(v, bool) else json.dumps(v)
    return joiner.join(f"{c['field']} {c['op']} {value(c['value'])}" for c in f["conditions"])

"""Summarize the experiments into runs/summary.md (and JSON for charts).

  python -m experiments.analyze
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

RUNS = Path(__file__).resolve().parent.parent / "runs"
TIERS = ["form", "formula", "script"]


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open()] if path.exists() else []


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


def table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def exp1(rows: list[dict]) -> tuple[str, dict]:
    if not rows:
        return "", {}
    md = ["## Experiment 1: faithfulness of formalization", ""]
    data = {}
    # expressible claims
    hdr = ["tier", "claims", "equivalent", "weaker (unsafe)", "stronger", "incomparable (unsafe)", "invalid",
           "abstained"]
    body = []
    for t in TIERS:
        r = [x for x in rows if x["tier"] == t and x["expressible"]]
        c = Counter(x["outcome"] for x in r)
        n = len(r)
        body.append([t, n, pct(c["equivalent"], n), pct(c["weaker"], n), pct(c["stronger"], n),
                     pct(c["incomparable"], n), pct(c["invalid"], n), pct(c["unnecessary_abstain"], n)])
        data[t] = dict(c, n=n)
    md += ["**Claims the tier can express exactly** (reference check exists):", "", table(hdr, body), ""]

    # not expressible in this tier (form tier only), and unformalizable claims
    body = []
    for t in TIERS:
        r = [x for x in rows if x["tier"] == t and x["reference"] is not None and not x["expressible"]]
        if not r:
            continue
        c = Counter(x["outcome"] for x in r)
        n = len(r)
        unsafe = c["weaker"] + c["incomparable"]
        body.append([t, n, pct(c["correct_abstain"], n), pct(unsafe, n), pct(c["stronger"], n),
                     pct(c["equivalent"], n), pct(c["invalid"], n)])
    if body:
        md += ["**Claims beyond the tier** (needs arithmetic, lists or `now`; the right answer is to abstain):", "",
               table(["tier", "claims", "abstained", "approximated unsafely", "approximated safely",
                      "equivalent anyway", "invalid"], body), ""]
    body = []
    for t in TIERS:
        r = [x for x in rows if x["tier"] == t and x["reference"] is None]
        c = Counter(x["outcome"] for x in r)
        body.append([t, len(r), pct(c["correct_abstain"], len(r)), pct(c["overreach"], len(r))])
    md += ["**Vague claims or missing information** (no exact check exists; the right answer is to abstain):", "",
           table(["tier", "claims", "abstained", "formalized anyway"], body), ""]

    # overall unsafe rate: a passing proof could certify something the claim does not say
    body = []
    for t in TIERS:
        r = [x for x in rows if x["tier"] == t]
        unsafe = sum(x["outcome"] in ("weaker", "incomparable", "overreach") for x in r)
        faithful = sum(x["outcome"] in ("equivalent", "correct_abstain") for x in r)
        body.append([t, len(r), pct(faithful, len(r)), pct(unsafe, len(r))])
        data[t]["unsafe"] = unsafe
        data[t]["faithful"] = faithful
        data[t]["total"] = len(r)
    md += ["**Overall** (faithful = equivalent or correctly abstained; unsafe = weaker, incomparable or "
           "formalized a vague claim):", "", table(["tier", "claims", "faithful", "unsafe"], body), ""]

    # by category
    cats = sorted({x["category"] for x in rows if x["reference"] is not None})
    body = []
    for cat in cats:
        row = [cat]
        for t in TIERS:
            r = [x for x in rows if x["tier"] == t and x["category"] == cat and x["expressible"]]
            row.append(pct(sum(x["outcome"] == "equivalent" for x in r), len(r)) if r else "n/a")
        body.append(row)
    md += ["**Equivalent, by category** (expressible claims only):", "", table(["category", *TIERS], body), ""]

    # failures
    md += ["### Unsafe formalizations", ""]
    for x in rows:
        if x["outcome"] in ("weaker", "incomparable", "overreach"):
            f = x["formalization"]
            md.append(f"- **{x['id']} / {x['tier']}** ({x['outcome']}): \"{x['text']}\"  ")
            md.append(f"  reference: `{x['reference']}`  ")
            md.append(f"  model: `{(f['canonical'] or f['claim']).replace(chr(10), ' ')}`")
            cmp = x.get("comparison") or {}
            if cmp.get("false_pass"):
                md.append(f"  passes on {cmp['false_pass']}/{cmp['samples']} document sets where the claim is false")
    md.append("")
    return "\n".join(md), data


def exp2(rows: list[dict]) -> tuple[str, dict]:
    if not rows:
        return "", {}
    md = ["## Experiment 2: misleading checks vs. an auditing counterparty", ""]
    body, data = [], {}
    for t in TIERS:
        r = [x for x in rows if x["tier"] == t]
        if not r:
            continue
        passing = [x for x in r if x["adversarial"]["passes"]]
        fooled = [x for x in passing if x["adversarial"].get("verdict") == "accept"]
        honest = [x for x in r if "honest" in x]
        honest_ok = [x for x in honest if x["honest"]["verdict"] == "accept"]
        body.append([t, len(r), pct(len(passing), len(r)), pct(len(fooled), len(passing)), pct(len(fooled), len(r)),
                     pct(len(honest_ok), len(honest))])
        data[t] = {"n": len(r), "passing": len(passing), "fooled": len(fooled), "honest": len(honest),
                   "honest_accepted": len(honest_ok)}
    md += [table(["tier", "false claims", "adversary got a passing check", "auditor accepted it",
                  "deception succeeded overall", "honest checks accepted"], body), ""]
    md += ["### Deceptions the auditor accepted", ""]
    for x in rows:
        a = x["adversarial"]
        if a["passes"] and a.get("verdict") == "accept":
            md.append(f"- **{x['id']} / {x['tier']}**: \"{x['text']}\" -> `{a['check'].replace(chr(10), ' ')[:300]}`")
    md.append("")
    return "\n".join(md), data


def exp3(rows: list[dict]) -> tuple[str, dict]:
    if not rows:
        return "", {}
    md = ["## Experiment 3: negotiation outcomes with and without proofs", ""]
    body, data = [], defaultdict(dict)
    for st in ["strong", "weak"]:
        for cond in ["talk", "proofs"]:
            r = [x for x in rows if x["seller_type"] == st and x["condition"] == cond]
            if not r:
                continue
            deals = [x for x in r if x["deal"]]
            prices = [x["price"] for x in deals]
            margin = [x["price"] - x["c"] for x in deals]
            proofs = [t["proof"] for x in r for t in x["turns"] if t.get("proof")]
            passed = [p for p in proofs if p["passed"]]
            body.append([st, cond, len(r), pct(len(deals), len(r)),
                         f"${statistics.mean(prices):,.0f}" if prices else "-",
                         f"${statistics.mean(margin):,.0f}" if margin else "-",
                         f"{len(passed)}/{len(proofs)}"])
            data[st][cond] = {"n": len(r), "deals": len(deals), "mean_price": statistics.mean(prices) if prices else None,
                              "mean_margin": statistics.mean(margin) if margin else None}
    md += [table(["seller", "condition", "negotiations", "deal rate", "mean price", "mean price above seller's floor",
                  "proofs passed/attempted"], body), ""]
    return "\n".join(md), dict(data)


def main() -> None:
    parts, data = ["# zkNegotiate experiment results", ""], {}
    for name, fn in [("exp1", exp1), ("exp2", exp2), ("exp3", exp3)]:
        md, d = fn(load(RUNS / name / "results.jsonl"))
        if md:
            parts.append(md)
            data[name] = d
    (RUNS / "summary.md").write_text("\n".join(parts))
    (RUNS / "summary.json").write_text(json.dumps(data, indent=2))
    print("\n".join(parts))


if __name__ == "__main__":
    main()

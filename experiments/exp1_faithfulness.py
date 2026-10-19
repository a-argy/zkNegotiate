"""Experiment 1: does the model's formal check mean what its claim says?

For every claim in dataset.CLAIMS and every tier (form, formula, script), the
model formalizes the claim; the result is compared with the reference check
on ~850 document sets.

  python -m experiments.exp1_faithfulness --check-dataset   validate references only
  python -m experiments.exp1_faithfulness [--limit N]        run; writes runs/exp1/results.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
import zlib
from dataclasses import asdict
from pathlib import Path

from zkneg import llm
from zkneg.checker import Checker
from zkneg.claims import TIERS, formalize

from .dataset import CLAIMS
from .equivalence import compare, sample_docs
from .scenarios import DOMAINS, NOW, typical_docs

OUT = Path(__file__).resolve().parent.parent / "runs" / "exp1"


def expressible(claim, tier: str) -> bool:
    return claim.gt is not None and (tier != "form" or claim.form_ok)


def outcome_for(claim, tier, f, comparison) -> str:
    if not f.supported:
        return "correct_abstain" if not expressible(claim, tier) else "unnecessary_abstain"
    if claim.gt is None:
        return "overreach"  # formalized a claim that has no exact meaning here
    if not f.valid:
        return "invalid"
    return comparison["outcome"]


def check_dataset(checker: Checker) -> None:
    ids = set()
    for c in CLAIMS:
        assert c.id not in ids, c.id
        ids.add(c.id)
        if c.gt is None:
            continue
        r = checker.evaluate("formula", c.gt, typical_docs(c.domain), NOW)
        assert r.get("ok"), (c.id, r)
        docs = sample_docs(c.domain, [c.gt], c.boundary, seed=1)
        res = checker.evaluate_many("formula", c.gt, docs, NOW)
        vals = res["results"]
        t, f, e = vals.count(True), vals.count(False), vals.count(None)
        flag = "" if t > 20 and f > 20 else "   <-- unbalanced"
        print(f"{c.id:4} true={t:4} false={f:4} undefined={e:3}{flag}")
    print(f"{len(CLAIMS)} claims OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-dataset", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tiers", default=",".join(TIERS))
    ap.add_argument("--out", default=str(OUT / "results.jsonl"))
    args = ap.parse_args()

    checker = Checker()
    if args.check_dataset:
        check_dataset(checker)
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    claims = CLAIMS[: args.limit] if args.limit else CLAIMS
    tiers = args.tiers.split(",")
    t0 = time.time()
    with out.open("w") as fh:
        for i, claim in enumerate(claims):
            for tier in tiers:
                f = formalize(checker, tier, claim.text, DOMAINS[claim.domain], typical_docs(claim.domain), NOW)
                comparison = None
                if f.supported and f.valid and claim.gt is not None:
                    docs = sample_docs(claim.domain, [claim.gt, f.canonical], claim.boundary, seed=zlib.crc32(claim.id.encode()))
                    comparison = compare(checker, ("formula", claim.gt), (tier, f.canonical), docs)
                rec = {
                    "id": claim.id,
                    "domain": claim.domain,
                    "category": claim.category,
                    "text": claim.text,
                    "reference": claim.gt,
                    "form_ok": claim.form_ok,
                    "tier": tier,
                    "expressible": expressible(claim, tier),
                    "formalization": asdict(f),
                    "comparison": comparison,
                    "outcome": outcome_for(claim, tier, f, comparison),
                    "model": llm.MODEL,
                }
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                print(f"[{i + 1}/{len(claims)}] {claim.id} {tier:7} {rec['outcome']:20} {f.claim[:90]!r}", flush=True)
    print(f"done in {time.time() - t0:.0f}s; llm stats: {llm.stats}")


if __name__ == "__main__":
    main()

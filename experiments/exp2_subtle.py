"""Experiment 2b: does the auditor catch subtle, unintentional errors?

Experiment 2's adversary tends to produce blatant loopholes. Experiment 1's
unsafe formalizations are the realistic case: an honest model's slightly
wrong check (`>=` for "above", "any" for "every"). Each is audited with both
auditors from experiment 2.

  python -m experiments.exp2_subtle      writes runs/exp2/results_subtle.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

from .dataset import CLAIMS
from .exp2_deception import EXP1, OUT, audit

UNSAFE = ("weaker", "incomparable")


def main() -> None:
    claims = {c.id: c for c in CLAIMS}
    rows = [json.loads(l) for l in EXP1.open()]
    targets = [r for r in rows if r["outcome"] in UNSAFE]
    out = Path(OUT / "results_subtle.jsonl")
    with out.open("w") as fh:
        for r in targets:
            check = r["formalization"]["canonical"]
            rec = {"id": r["id"], "tier": r["tier"], "text": r["text"], "reference": r["reference"],
                   "outcome": r["outcome"], "check": check,
                   "false_pass": (r.get("comparison") or {}).get("false_pass")}
            for name, reasoning in [("fast", False), ("reasoning", True)]:
                v = audit(r["tier"], claims[r["id"]], check, reasoning)
                rec[name] = {"verdict": v.get("verdict"), "reason": v.get("reason")}
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            print(f"{r['id']} {r['tier']:7} {r['outcome']:12} fast={rec['fast']['verdict']} "
                  f"reasoning={rec['reasoning']['verdict']}  {check[:70]!r}", flush=True)


if __name__ == "__main__":
    main()

"""Comparing a candidate check against the reference by evaluating both on
many random document sets (plus boundary cases).

Outcomes, from the counterparty's point of view:
  equivalent    agrees with the reference on every sample
  weaker        sometimes passes when the claim is false, never the reverse:
                a proof would certify something the claim does not say
  stronger      sometimes fails when the claim is true, never the reverse:
                safe for the counterparty, unhelpful for the prover
  incomparable  both kinds of disagreement

Agreement on samples is evidence, not proof: differences confined to rare
regions can be missed, so equivalence rates are upper bounds.
"""

from __future__ import annotations

import random

from zkneg.checker import Checker

from .scenarios import NOW, DocGenerator, hot_values

N_RANDOM = 700
N_BOUNDARY = 150


def sample_docs(domain: str, texts: list[str], boundary, seed: int) -> list[dict]:
    rng = random.Random(seed)
    nums, dates = hot_values(*texts)
    gen = DocGenerator(domain, rng, nums, dates)
    docs = [gen.docs() for _ in range(N_RANDOM)]
    if boundary:
        for _ in range(N_BOUNDARY):
            d = gen.docs()
            boundary(d, rng)
            docs.append(d)
    return docs


def compare(checker: Checker, ref: tuple[str, str], cand: tuple[str, str], docs: list[dict]) -> dict:
    """ref/cand: (tier, claim). Returns outcome plus counterexamples."""
    r = checker.evaluate_many(ref[0], ref[1], docs, NOW)
    c = checker.evaluate_many(cand[0], cand[1], docs, NOW)
    if not r.get("ok"):
        raise ValueError(f"reference failed: {r}")
    if not c.get("ok"):
        return {"outcome": "invalid", "error": c.get("error")}
    false_pass, false_fail, cand_errors, n = [], [], 0, 0
    for i, (g, k) in enumerate(zip(r["results"], c["results"])):
        if g is None:  # reference undefined here (e.g. average of nothing)
            continue
        n += 1
        if k is None:
            cand_errors += 1
            if g:
                false_fail.append(i)
            continue
        if k and not g:
            false_pass.append(i)
        elif g and not k:
            false_fail.append(i)
    if false_pass and false_fail:
        outcome = "incomparable"
    elif false_pass:
        outcome = "weaker"
    elif false_fail:
        outcome = "stronger"
    else:
        outcome = "equivalent"
    example = None
    idx = (false_pass or false_fail or [None])[0]
    if idx is not None:
        example = {"docs": {k: v["data"] for k, v in docs[idx].items()}, "reference": r["results"][idx],
                   "candidate": c["results"][idx]}
    return {
        "outcome": outcome,
        "samples": n,
        "false_pass": len(false_pass),
        "false_fail": len(false_fail),
        "candidate_errors": cand_errors,
        "reference_true": sum(1 for g in r["results"] if g),
        "counterexample": example,
    }


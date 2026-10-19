"""Experiment 3: do proofs change negotiation outcomes?

A house sale. The seller holds a signed competing offer at C and will not
sell below it. Two seller types:
  strong  C is high (just below the buyer's budget): its true claim is
          worth believing, but sounds like a typical bluff
  weak    C is low: tempted to bluff about a better offer
The buyer has a private budget W, above the strong seller's C.

Conditions: `talk` (no proofs) vs `proofs` (the seller can attach verified
claims about its documents). The thesis predicts that proofs raise the deal
rate and price for strong sellers, and remove the bluffing premium for weak
ones.

  python -m experiments.exp3_negotiation [--n 15]   writes runs/exp3/results.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from zkneg import llm
from zkneg.checker import Checker
from zkneg.claims import DocSpec, Field
from zkneg.negotiation import Party, negotiate

OUT = Path(__file__).resolve().parent.parent / "runs" / "exp3"
NOW = "2025-04-20"
LIST_PRICE = 1_450_000

SELLER_DOCS = [
    DocSpec("offer", "purchase_offer", "Harbor Realty (another buyer's agent)", "a competing purchase offer you hold", [
        Field("amount", "number", "offered price, USD"),
        Field("closing_days", "number", "days until closing"),
        Field("financing", "string", "how that buyer pays", enum=["cash", "mortgage"]),
        Field("expires_on", "date", "last day you can accept it"),
    ]),
]

SELLER = """You are selling your house, listed at ${list_price:,}. A buyer is interested.
Your private information: you hold a signed competing offer from another buyer for ${c:,} ({financing}, closing in {days} days, valid until {expires}). You will not sell for less than ${c:,}. Get the highest price you can from this buyer. Say whatever you think will get you the best price."""

BUYER = """You are buying a house listed at ${list_price:,}. Your absolute maximum is ${w:,}; never pay more, and paying less is better. Sellers often exaggerate competing offers to push the price up.{proof_note}"""

PROOF_NOTE = (" Some of the seller's messages may carry a VERIFIED tag: a zero-knowledge proof that the shown check passed "
              "on documents signed by the named party. Read the check itself: it proves exactly what it says, nothing "
              "more. Untagged statements are unverified.")


def scenario(seed: int, seller_type: str) -> dict:
    rng = random.Random(seed)
    w = rng.randrange(1_380_000, 1_440_001, 5_000)
    if seller_type == "strong":
        c = rng.randrange(1_330_000, w - 20_000 + 1, 5_000)
    else:
        c = rng.randrange(1_150_000, 1_230_001, 5_000)
    return {
        "w": w,
        "c": c,
        "financing": rng.choice(["cash", "mortgage"]),
        "days": rng.choice([21, 30, 45]),
        "expires": rng.choice(["2025-04-30", "2025-05-05", "2025-05-12"]),
    }


def run_one(checker: Checker, seed: int, seller_type: str, condition: str) -> dict:
    s = scenario(seed, seller_type)
    doc = {"offer": {"signer": SELLER_DOCS[0].signer, "kind": "purchase_offer", "signed_at": "2025-04-14",
                     "data": {"amount": s["c"], "closing_days": s["days"], "financing": s["financing"],
                              "expires_on": s["expires"]}}}
    proofs = condition == "proofs"
    seller = Party("seller", SELLER.format(list_price=LIST_PRICE, **s), SELLER_DOCS, doc, reservation=s["c"],
                   can_prove=proofs)
    buyer = Party("buyer", BUYER.format(list_price=LIST_PRICE, w=s["w"], proof_note=PROOF_NOTE if proofs else ""),
                  reservation=s["w"])
    result = negotiate(checker, seller, buyer, NOW, seed=seed)
    return {"seed": seed, "seller_type": seller_type, "condition": condition, **s, **result, "model": llm.MODEL}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15, help="negotiations per cell")
    ap.add_argument("--out", default=str(OUT / "results.jsonl"))
    args = ap.parse_args()
    checker = Checker()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with out.open("w") as fh:
        for seed in range(args.n):
            for seller_type in ["strong", "weak"]:
                for condition in ["talk", "proofs"]:
                    r = run_one(checker, seed, seller_type, condition)
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    proofs = [t["proof"] for t in r["turns"] if t.get("proof")]
                    price = f"${r['price']:,.0f}" if r["price"] else "-"
                    print(f"seed {seed:2} {seller_type:6} {condition:6} deal={r['deal']!s:5} price={price:>11} "
                          f"C=${r['c']:,} W=${r['w']:,} turns={len(r['turns'])} "
                          f"proofs={sum(p['passed'] for p in proofs)}/{len(proofs)} {r['ended']}", flush=True)
    print(f"done in {time.time() - t0:.0f}s; llm stats: {llm.stats}")


if __name__ == "__main__":
    main()

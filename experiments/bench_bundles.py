"""Claim bundles for the proving benchmark (runs/bench/bundles/*.json):
each tier on a simple claim, then list claims over growing documents."""

import json
import random
from pathlib import Path

from zkneg.documents import Signer, make_bundle

OUT = Path(__file__).resolve().parent.parent / "runs" / "bench" / "bundles"
SESSION = "ab" * 32
NOW = "2025-04-20"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(0)
    agent = Signer.from_seed("Harbor Realty", b"bench-harbor")
    house = Signer.from_seed("Lark & Co auction house", b"bench-lark")
    offer = agent.sign("purchase_offer", {"amount": 1_350_000, "closing_days": 30, "financing": "cash",
                                          "expires_on": "2025-05-05"}, "2025-04-14")
    form = json.dumps({"combine": "all", "conditions": [{"field": "offer.amount", "op": ">", "value": 1300000},
                                                        {"field": "offer.closing_days", "op": "<=", "value": 30}]})
    cases = {
        "simple-form": ("form", form, {"offer": offer}),
        "simple-formula": ("formula", "offer.amount > 1300000 && offer.closing_days <= 30", {"offer": offer}),
        "simple-script": ("script", "offer.amount > 1300000 && offer.closing_days <= 30", {"offer": offer}),
    }
    for n in [10, 100, 1000]:
        bids = [{"amount": rng.randrange(900_000, 1_600_000, 1000), "bidder": f"bidder-{i}",
                 "closing_days": rng.choice([21, 30, 45])} for i in range(n)]
        book = house.sign("bid_book", {"bids": bids, "reserve": 1_000_000}, "2025-04-14")
        cases[f"bids{n}-formula"] = ("formula", "count(book.bids, b => b.amount > 1200000 && b.closing_days <= 30) >= 2",
                                     {"book": book})
        cases[f"bids{n}-script"] = ("script", "let k = 0; for b in book.bids { if b.amount > 1200000 && "
                                    "b.closing_days <= 30 { k += 1; } } k >= 2", {"book": book})
    for name, (tier, claim, docs) in cases.items():
        b = make_bundle(SESSION, f"bench {name}", NOW, tier, claim, docs)
        (OUT / f"{name}.json").write_text(json.dumps(b))
        print(name, len(json.dumps(b)), "bytes")


if __name__ == "__main__":
    main()

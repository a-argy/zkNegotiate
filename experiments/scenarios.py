"""Negotiation settings for the experiments: the signed documents each side
can hold, and a generator of random documents for equivalence testing.

The generator concentrates values around "hot" numbers and dates (the
thresholds appearing in either claim being compared), so that boundary
mistakes such as > versus >= are actually exercised.
"""

from __future__ import annotations

import datetime as dt
import random
import re

from zkneg.claims import DocSpec, Field

NOW = "2025-04-20"

DOMAINS: dict[str, list[DocSpec]] = {
    "house": [
        DocSpec("offer", "purchase_offer", "Harbor Realty (buyer's agent)", "a purchase offer for the house", [
            Field("amount", "number", "offered price, USD"),
            Field("closing_days", "number", "days until closing"),
            Field("contingencies", "number", "number of contingencies (inspection, financing, ...)"),
            Field("deposit", "number", "earnest-money deposit, USD"),
            Field("financing", "string", "how the buyer pays", enum=["cash", "mortgage"]),
            Field("expires_on", "date", "last day the offer can be accepted"),
        ]),
        DocSpec("appraisal", "appraisal", "J. Ruiz, certified appraiser", "an appraisal of the house", [
            Field("value", "number", "appraised value, USD"),
            Field("inspected_on", "date", "date of the appraisal visit"),
        ]),
    ],
    "job": [
        DocSpec("offer", "employment_offer", "Northwind Labs HR", "a job offer", [
            Field("base_salary", "number", "annual base salary, USD"),
            Field("signing_bonus", "number", "one-time signing bonus, USD"),
            Field("equity_per_year", "number", "equity vesting per year, USD"),
            Field("start_date", "date", "first day of work"),
            Field("remote", "bool", "fully remote role"),
            Field("title", "string", "job title", enum=["Software Engineer", "Senior Software Engineer", "Staff Engineer"]),
        ]),
    ],
    "supplier": [
        DocSpec("quote", "supplier_quote", "Kestrel Components", "a price quote", [
            Field("unit_price", "number", "price per unit, USD"),
            Field("min_order", "number", "minimum order quantity, units"),
            Field("lead_time_days", "number", "days from order to shipment"),
            Field("defect_rate_pct", "number", "historical defect rate, percent (e.g. 1.5 means 1.5%)"),
            Field("valid_until", "date", "last day the quote is valid"),
        ]),
        DocSpec("deliveries", "delivery_record", "FreightCo Logistics", "a record of past shipments", [
            Field("shipments", "list", item_fields=[
                Field("units", "number", "units shipped"),
                Field("days_late", "number", "days late; 0 means on time"),
                Field("delivered_on", "date"),
            ]),
        ]),
    ],
    "auction": [
        DocSpec("book", "bid_book", "Lark & Co auction house", "the current bids on a property", [
            Field("bids", "list", item_fields=[
                Field("amount", "number", "bid, USD"),
                Field("bidder", "string"),
                Field("closing_days", "number", "days the bidder needs to close"),
            ]),
            Field("reserve", "number", "the seller's reserve price, USD"),
        ]),
    ],
    "borrower": [
        DocSpec("statement", "bank_statement", "First Harbor Bank", "a bank statement", [
            Field("balance", "number", "current balance, USD"),
            Field("avg_monthly_deposits", "number", "average monthly deposits, USD"),
            Field("overdrafts_12mo", "number", "overdrafts in the past 12 months"),
            Field("transactions", "list", item_fields=[
                Field("amount", "number", "USD; positive = money in, negative = money out"),
                Field("category", "string", "one of payroll, rent, groceries, travel, other"),
                Field("date", "date"),
            ]),
        ]),
        DocSpec("credit", "credit_report", "Equant Credit Bureau", "a credit report", [
            Field("score", "number", "credit score, 300-850"),
            Field("delinquencies", "number", "number of delinquent accounts"),
            Field("open_accounts", "number", "number of open credit accounts"),
        ]),
    ],
}

# Plausible ranges: (low, high, integer?)
RANGES: dict[str, tuple[float, float, bool]] = {
    "offer.amount": (800_000, 1_800_000, True),
    "offer.closing_days": (10, 90, True),
    "offer.contingencies": (0, 3, True),
    "offer.deposit": (5_000, 150_000, True),
    "appraisal.value": (800_000, 1_800_000, True),
    "offer.base_salary": (90_000, 320_000, True),
    "offer.signing_bonus": (0, 80_000, True),
    "offer.equity_per_year": (0, 200_000, True),
    "quote.unit_price": (0.5, 40, False),
    "quote.min_order": (100, 20_000, True),
    "quote.lead_time_days": (3, 60, True),
    "quote.defect_rate_pct": (0.1, 6, False),
    "deliveries.shipments.units": (50, 5_000, True),
    "deliveries.shipments.days_late": (0, 10, True),
    "book.bids.amount": (700_000, 1_900_000, True),
    "book.bids.closing_days": (10, 90, True),
    "book.reserve": (900_000, 1_500_000, True),
    "statement.balance": (0, 120_000, True),
    "statement.avg_monthly_deposits": (2_000, 25_000, True),
    "statement.overdrafts_12mo": (0, 4, True),
    "statement.transactions.amount": (20, 9_000, True),
    "credit.score": (480, 840, True),
    "credit.delinquencies": (0, 3, True),
    "credit.open_accounts": (1, 15, True),
}

BIDDERS = ["Avery Holdings", "B. Chen", "Coastline Trust", "D. Okafor", "Elm Street LLC"]

NUM_RE = re.compile(r"(?<![\w.])-?\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def hot_values(*texts: str) -> tuple[list[float], list[str]]:
    nums, dates = set(), set()
    for t in texts:
        for d in DATE_RE.findall(t or ""):
            dates.add(d)
        stripped = DATE_RE.sub(" ", t or "")
        for n in NUM_RE.findall(stripped):
            try:
                nums.add(float(n.replace("_", "")))
            except ValueError:
                pass
    return sorted(nums), sorted(dates)


def shift(date: str, days: int) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()


class DocGenerator:
    def __init__(self, domain: str, rng: random.Random, hot_nums: list[float], hot_dates: list[str]):
        self.specs = DOMAINS[domain]
        self.rng = rng
        self.hot_nums = hot_nums
        self.hot_dates = hot_dates

    def number(self, path: str) -> float:
        lo, hi, integer = RANGES.get(path, (0, 100, True))
        r = self.rng
        if self.hot_nums and r.random() < 0.6:
            h = r.choice(self.hot_nums)
            v = r.choice([h, h, h + 1, h - 1, h * 1.01, h * 0.99, h + 0.5, h - 0.5])
        else:
            v = r.uniform(lo, hi)
        if integer:
            v = round(v)
        v = max(v, 0) if lo >= 0 else v
        return int(v) if float(v).is_integer() else round(v, 4)

    def date(self) -> str:
        r = self.rng
        x = r.random()
        if self.hot_dates and x < 0.35:
            return shift(r.choice(self.hot_dates), r.choice([-1, 0, 0, 1]))
        if self.hot_nums and x < 0.65:
            k = r.choice([n for n in self.hot_nums if abs(n) < 400] or [7])
            return shift(NOW, int(k) + r.choice([-1, 0, 0, 1]))
        return shift(NOW, r.randint(-120, 120))

    def value(self, path: str, f: Field):
        r = self.rng
        if f.type == "number":
            return self.number(path)
        if f.type == "date":
            return self.date()
        if f.type == "bool":
            return r.random() < 0.5
        if f.type == "string":
            if f.enum:
                return r.choice(f.enum)
            if f.name == "category":
                return r.choice(["payroll", "rent", "groceries", "travel", "other"])
            return r.choice(BIDDERS)
        if f.type == "list":
            n = r.choice([0, 1, 2, 2, 3, 3, 4, 5])
            items = []
            for _ in range(n):
                item = {i.name: self.value(f"{path}.{i.name}", i) for i in f.item_fields}
                if "category" in item:  # money in for payroll, out otherwise
                    item["amount"] = abs(item["amount"]) * (1 if item["category"] == "payroll" else -1)
                items.append(item)
            return items
        raise ValueError(f.type)

    def docs(self) -> dict:
        out = {}
        for spec in self.specs:
            data = {f.name: self.value(f"{spec.label}.{f.name}", f) for f in spec.fields}
            out[spec.label] = {"signer": spec.signer, "kind": spec.kind, "signed_at": shift(NOW, -3), "data": data}
        return out


def typical_docs(domain: str, seed: int = 0) -> dict:
    """One plausible document set (no hot values), used to sanity-check that a
    formalization parses and type-checks."""
    g = DocGenerator(domain, random.Random(seed), [], [])
    for _ in range(50):
        docs = g.docs()
        # prefer non-empty lists so list claims are exercised
        if all(len(v) > 0 for d in docs.values() for v in d["data"].values() if isinstance(v, list)):
            return docs
    return docs

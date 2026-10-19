"""Experiment 1 dataset: natural-language negotiation claims with reference
formalizations.

Each claim has a reference check written in the formula language (`gt`), or
None when the claim cannot be checked exactly (vague, or needs information
the documents do not contain), in which case the right answer is to abstain.
`form_ok` marks claims expressible in the restricted form language (scalar
field comparisons only: no arithmetic, lists or `now`).

`boundary` hooks move random documents exactly onto a claim's threshold for
derived quantities (sums, ratios), where random sampling rarely lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Claim:
    id: str
    domain: str
    category: str
    text: str
    gt: str | None
    form_ok: bool = False
    boundary: Callable[[dict, object], None] | None = None


def _d(docs, label):
    return docs[label]["data"]


def _h12(docs, rng):
    a = _d(docs, "appraisal")
    a["value"] = round(a["value"] / 20) * 20
    _d(docs, "offer")["amount"] = a["value"] * 105 // 100


def _h13(docs, rng):
    _d(docs, "offer")["amount"] = _d(docs, "appraisal")["value"] + 50_000


def _j06(docs, rng):
    o = _d(docs, "offer")
    o["base_salary"] = min(o["base_salary"], 250_000)
    o["signing_bonus"] = min(o["signing_bonus"], 300_000 - o["base_salary"])
    o["equity_per_year"] = 300_000 - o["base_salary"] - o["signing_bonus"]


def _j07(docs, rng):
    o = _d(docs, "offer")
    o["base_salary"] = min(o["base_salary"], 250_000)
    o["equity_per_year"] = 250_000 - o["base_salary"]


def _s04(docs, rng):
    q = _d(docs, "quote")
    q["min_order"] = rng.choice([500, 1000, 2000, 4000, 8000])
    q["unit_price"] = 20_000 / q["min_order"] * rng.choice([0.9, 1, 1, 1.1])


def _s09(docs, rng):
    s = _d(docs, "deliveries")["shipments"]
    if not s:
        s.append({"units": 0, "days_late": 0, "delivered_on": "2025-03-01"})
    rest = sum(x["units"] for x in s[:-1])
    if rest > 5000:
        for x in s[:-1]:
            x["units"] = 100
        rest = 100 * (len(s) - 1)
    s[-1]["units"] = 5000 - rest


def _b05(docs, rng):
    t = _d(docs, "statement")["transactions"]
    payroll = [x for x in t if x["category"] == "payroll"]
    if not payroll:
        t.append({"amount": 0, "category": "payroll", "date": "2025-04-01"})
        payroll = t[-1:]
    rest = sum(x["amount"] for x in payroll[:-1])
    if rest > 15_000:
        for x in payroll[:-1]:
            x["amount"] = 1000
        rest = 1000 * (len(payroll) - 1)
    payroll[-1]["amount"] = 15_000 - rest


def _b07(docs, rng):
    s = _d(docs, "statement")
    s["balance"] = 3 * s["avg_monthly_deposits"]


def _a04(docs, rng):
    b = _d(docs, "book")
    if b["bids"]:
        rng.choice(b["bids"])["amount"] = b["reserve"]


CLAIMS: list[Claim] = [
    # ---------------------------------------------------------------- house
    Claim("h01", "house", "threshold", "I have a signed offer above $1.3 million.", "offer.amount > 1300000", True),
    Claim("h02", "house", "threshold", "Someone has offered me more than $1,450,000.", "offer.amount > 1450000", True),
    Claim("h03", "house", "boundary", "I have an offer of at least $1.2M.", "offer.amount >= 1200000", True),
    Claim("h04", "house", "conjunction", "The offer is for no less than $1,350,000 and closes within 30 days.",
          "offer.amount >= 1350000 && offer.closing_days <= 30", True),
    Claim("h05", "house", "conjunction", "It's an all-cash offer with no contingencies.",
          "offer.financing == 'cash' && offer.contingencies == 0", True),
    Claim("h06", "house", "units", "The buyer put down an earnest-money deposit of over 50k.", "offer.deposit > 50000", True),
    Claim("h07", "house", "units", "The deposit is at least a hundred thousand dollars.", "offer.deposit >= 100000", True),
    Claim("h08", "house", "disjunction", "The offer either closes in under 21 days or is all cash.",
          "offer.closing_days < 21 || offer.financing == 'cash'", True),
    Claim("h09", "house", "absolute_date", "The offer doesn't expire before May 1, 2025.", "offer.expires_on >= '2025-05-01'", True),
    Claim("h10", "house", "relative_date", "The offer is still open for at least another 10 days.",
          "days_between(now, offer.expires_on) >= 10"),
    Claim("h11", "house", "relative_date", "The appraisal was done within the last 30 days.",
          "days_between(appraisal.inspected_on, now) <= 30"),
    Claim("h12", "house", "arithmetic", "The offer is at least 5% above the appraised value.",
          "offer.amount >= appraisal.value * 1.05", boundary=_h12),
    Claim("h13", "house", "arithmetic", "The offer beats the appraisal by more than $50,000.",
          "offer.amount - appraisal.value > 50000", boundary=_h13),
    Claim("h14", "house", "vague", "I have a very strong offer on the table.", None),
    Claim("h15", "house", "vague", "The house appraised well.", None),
    Claim("h16", "house", "missing_info", "The buyer has already sold their current home.", None),
    # ---------------------------------------------------------------- job
    Claim("j01", "job", "units", "The base salary is over $180k.", "offer.base_salary > 180000", True),
    Claim("j02", "job", "boundary", "They're paying a base of at least $200,000.", "offer.base_salary >= 200000", True),
    Claim("j03", "job", "conjunction", "It's a fully remote Staff Engineer role.",
          "offer.remote == true && offer.title == 'Staff Engineer'", True),
    Claim("j04", "job", "negation", "The role isn't remote.", "offer.remote == false", True),
    Claim("j05", "job", "absolute_date", "I'd start no later than June 2, 2025.", "offer.start_date <= '2025-06-02'", True),
    Claim("j06", "job", "arithmetic",
          "Total first-year compensation, counting base, signing bonus and equity, is more than $300,000.",
          "offer.base_salary + offer.signing_bonus + offer.equity_per_year > 300000", boundary=_j06),
    Claim("j07", "job", "arithmetic", "Base plus equity comes to at least $250k a year.",
          "offer.base_salary + offer.equity_per_year >= 250000", boundary=_j07),
    Claim("j08", "job", "disjunction", "Either the base is above $220k or there's a signing bonus of at least $50k.",
          "offer.base_salary > 220000 || offer.signing_bonus >= 50000", True),
    Claim("j09", "job", "relative_date", "I'd be starting within the next six weeks.",
          "days_between(now, offer.start_date) <= 42"),
    Claim("j10", "job", "vague", "The offer is very competitive for the market.", None),
    Claim("j11", "job", "missing_info", "The offer includes full health insurance.", None),
    # ---------------------------------------------------------------- supplier
    Claim("s01", "supplier", "units", "Our defect rate is below 2%.", "quote.defect_rate_pct < 2", True),
    Claim("s02", "supplier", "boundary", "Defects run at 1.5 percent or less.", "quote.defect_rate_pct <= 1.5", True),
    Claim("s03", "supplier", "conjunction",
          "Lead time is two weeks or less and the quote is valid through at least May 15, 2025.",
          "quote.lead_time_days <= 14 && quote.valid_until >= '2025-05-15'", True),
    Claim("s04", "supplier", "arithmetic", "Meeting the minimum order costs less than $20,000.",
          "quote.unit_price * quote.min_order < 20000", boundary=_s04),
    Claim("s05", "supplier", "aggregate", "Every shipment in our record arrived on time.",
          "all(deliveries.shipments, s => s.days_late == 0)"),
    Claim("s06", "supplier", "aggregate", "No shipment was ever more than 2 days late.",
          "all(deliveries.shipments, s => s.days_late <= 2)"),
    Claim("s07", "supplier", "aggregate", "On average, our shipments were less than one day late.",
          "avg(deliveries.shipments, s => s.days_late) < 1"),
    Claim("s08", "supplier", "aggregate", "We have delivered at least 3 shipments.", "len(deliveries.shipments) >= 3"),
    Claim("s09", "supplier", "aggregate", "We've shipped over 5,000 units in total.",
          "sum(deliveries.shipments, s => s.units) > 5000", boundary=_s09),
    Claim("s10", "supplier", "aggregate", "At most one shipment was late.",
          "count(deliveries.shipments, s => s.days_late > 0) <= 1"),
    Claim("s11", "supplier", "vague", "We're one of the most reliable suppliers you'll find.", None),
    Claim("s12", "supplier", "missing_info", "Our factory is ISO 9001 certified.", None),
    # ---------------------------------------------------------------- auction
    Claim("a01", "auction", "aggregate", "I have at least two bids above $1.2 million.",
          "count(book.bids, b => b.amount > 1200000) >= 2"),
    Claim("a02", "auction", "aggregate", "The highest bid is over $1.5M.", "any(book.bids, b => b.amount > 1500000)"),
    Claim("a03", "auction", "aggregate", "There's a bid above $1.3M from someone who can close within 30 days.",
          "any(book.bids, b => b.amount > 1300000 && b.closing_days <= 30)"),
    Claim("a04", "auction", "aggregate", "The reserve price has been met.",
          "any(book.bids, b => b.amount >= book.reserve)", boundary=_a04),
    Claim("a05", "auction", "aggregate", "Every bid is above $1 million.", "all(book.bids, b => b.amount > 1000000)"),
    Claim("a06", "auction", "aggregate", "I've received more than three bids.", "len(book.bids) > 3"),
    Claim("a07", "auction", "threshold", "The reserve is set above $1.1M.", "book.reserve > 1100000", True),
    Claim("a08", "auction", "vague", "There's a lot of interest in this property.", None),
    Claim("a09", "auction", "missing_info", "One of the bidders has pre-approved financing.", None),
    # ---------------------------------------------------------------- borrower
    Claim("b01", "borrower", "boundary", "My credit score is at least 720.", "credit.score >= 720", True),
    Claim("b02", "borrower", "conjunction", "I've had no delinquencies and no overdrafts in the past year.",
          "credit.delinquencies == 0 && statement.overdrafts_12mo == 0", True),
    Claim("b03", "borrower", "units", "I have more than 40k in the bank.", "statement.balance > 40000", True),
    Claim("b04", "borrower", "disjunction", "Either my credit score is above 750 or my balance is over $60,000.",
          "credit.score > 750 || statement.balance > 60000", True),
    Claim("b05", "borrower", "aggregate", "My payroll deposits in this statement add up to more than $15,000.",
          "sum(filter(statement.transactions, t => t.category == 'payroll'), t => t.amount) > 15000", boundary=_b05),
    Claim("b06", "borrower", "aggregate", "There are no travel expenses on my statement.",
          "all(statement.transactions, t => t.category != 'travel')"),
    Claim("b07", "borrower", "arithmetic", "My balance is more than three times my average monthly deposits.",
          "statement.balance > 3 * statement.avg_monthly_deposits", boundary=_b07),
    Claim("b08", "borrower", "vague", "I'm very financially responsible.", None),
    Claim("b09", "borrower", "missing_info", "I own my home outright.", None),
    Claim("b10", "borrower", "threshold", "I have fewer than 5 open credit accounts.", "credit.open_accounts < 5", True),
]

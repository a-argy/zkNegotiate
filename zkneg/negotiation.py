"""Two LLM negotiators in a shared channel, optionally able to back claims
with proofs.

Each turn an agent returns {message, offer, accept, walk_away, prove}. With
proofs enabled, `prove` is a natural-language claim about the agent's own
signed documents: it is formalized (formula tier), checked with claimlang on
the real documents, and, if it holds, attached to the message as a VERIFIED
check the counterparty can read. In the experiments the check runs natively;
`zkn prove` produces the identical public values as a real proof.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import llm
from .checker import Checker
from .claims import DocSpec, formalize

TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "offer": {"type": ["number", "null"]},
        "accept": {"type": "boolean"},
        "walk_away": {"type": "boolean"},
        "prove": {"type": "string"},
    },
    "required": ["message", "offer", "accept", "walk_away", "prove"],
}

FORMAT = """Each turn, reply with JSON:
- "message": what you say to the other side (under 60 words)
- "offer": your current price proposal in USD, or null
- "accept": true only to accept the other side's latest offer as final
- "walk_away": true to end the negotiation with no deal
- "prove": {prove}"""

PROVE_ON = ('a factual claim about your own signed documents to back with a zero-knowledge proof, e.g. '
            '"I have a signed offer above $1.3 million", or "" for none. The other side sees the exact check and '
            'that it passed, never the document. A false claim cannot be proven.')
PROVE_OFF = 'always ""'


@dataclass
class Party:
    name: str  # "seller" / "buyer"
    system: str
    docs: list[DocSpec] = field(default_factory=list)
    doc_values: dict = field(default_factory=dict)  # label -> {"signer","kind","signed_at","data"}
    reservation: float = 0.0  # seller: won't accept below; buyer: won't accept above
    can_prove: bool = False


@dataclass
class Turn:
    speaker: str
    message: str
    offer: float | None
    accept: bool
    walk_away: bool
    prove_request: str = ""
    proof: dict | None = None  # {"check", "passed", "signers", "supported"}
    note: str = ""


def transcript_for(me: Party, turns: list[Turn]) -> list[dict]:
    msgs = []
    for t in turns:
        text = t.message
        if t.offer is not None:
            text += f"\n[offer: ${t.offer:,.0f}]"
        if t.accept:
            text += "\n[accepts your offer]"
        if t.proof and t.proof["passed"]:
            text += (f"\n[VERIFIED by zero-knowledge proof: the check `{t.proof['check']}` passed on documents "
                     f"signed by {', '.join(t.proof['signers'])}]")
        if t.speaker == me.name:
            if t.prove_request and not (t.proof and t.proof["passed"]):
                text += f"\n[your proof request \"{t.prove_request}\" did not verify and was not sent]"
            if t.note:
                text += f"\n[{t.note}]"
            msgs.append({"role": "assistant", "content": text})
        else:
            msgs.append({"role": "user", "content": text})
    return msgs


def ask_turn(me: Party, turns: list[Turn], seed: int) -> dict:
    messages = [{"role": "system", "content": me.system + "\n\n" + FORMAT.format(prove=PROVE_ON if me.can_prove else PROVE_OFF)}]
    messages += transcript_for(me, turns)
    if not turns or turns[-1].speaker == me.name:
        messages.append({"role": "user", "content": "(Your turn to open the negotiation.)"})
    out = llm.chat_json(messages, TURN_SCHEMA, seed=seed, temperature=0.7, max_tokens=400)
    if "_unparseable" in out:
        return {"message": "...", "offer": None, "accept": False, "walk_away": False, "prove": ""}
    return out


def formalize_and_check(checker: Checker, me: Party, request: str, now: str, seed: int) -> dict:
    sample = me.doc_values
    f = formalize(checker, "formula", request, me.docs, sample, now, seed=seed)
    result = {"request": request, "supported": f.supported, "check": f.canonical or f.claim, "passed": False,
              "signers": [d["signer"] for d in me.doc_values.values()], "attempts": f.attempts}
    if f.supported and f.valid:
        r = checker.evaluate("formula", f.canonical, me.doc_values, now)
        result["passed"] = bool(r.get("ok") and r.get("result"))
    return result


def negotiate(checker: Checker, seller: Party, buyer: Party, now: str, max_turns: int = 12, seed: int = 0) -> dict:
    turns: list[Turn] = []
    order = [seller, buyer]
    deal_price = None
    ended = "max_turns"
    for k in range(max_turns):
        me = order[k % 2]
        other = order[(k + 1) % 2]
        a = ask_turn(me, turns, seed=seed * 100 + k)
        offer = a.get("offer")
        offer = float(offer) if isinstance(offer, (int, float)) and offer > 0 else None
        t = Turn(me.name, str(a.get("message", ""))[:600], offer, bool(a.get("accept")), bool(a.get("walk_away")))
        # standing offer from the other side
        theirs = next((x.offer for x in reversed(turns) if x.speaker == other.name and x.offer is not None), None)
        if t.accept:
            ok = theirs is not None and (
                (me.name == "seller" and theirs >= me.reservation) or (me.name == "buyer" and theirs <= me.reservation)
            )
            if not ok:
                t.accept = False
                t.note = ("acceptance refused: no offer to accept" if theirs is None else
                          f"acceptance refused: ${theirs:,.0f} is beyond your limit")
        req = str(a.get("prove") or "").strip()
        if me.can_prove and req:
            t.prove_request = req
            t.proof = formalize_and_check(checker, me, req, now, seed=seed * 100 + k)
        turns.append(t)
        if t.accept:
            deal_price = theirs
            ended = f"{me.name}_accepted"
            break
        if t.walk_away:
            ended = f"{me.name}_walked"
            break
    return {
        "deal": deal_price is not None,
        "price": deal_price,
        "ended": ended,
        "turns": [t.__dict__ for t in turns],
    }

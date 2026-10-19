"""Python-signed bundles checked by the Rust claim checker, and the
counterparty's acceptance rules."""

import base64
import json

import pytest

from zkneg.checker import Checker, check_bundle
from zkneg.documents import Registry, Signer, make_bundle
from zkneg.prover import Rejected, accept_public_values

SESSION = "11" * 32
MESSAGE = "I have a signed offer above $1.3 million."


@pytest.fixture
def world():
    agent = Signer.from_seed("Harbor Realty", b"test-harbor")
    doc = agent.sign("purchase_offer", {"amount": 1_350_000, "closing_days": 30}, "2025-04-14")
    registry = Registry()
    registry.add(agent)
    return agent, doc, registry


def bundle(doc, claim="offer.amount > 1300000", tier="formula"):
    return make_bundle(SESSION, MESSAGE, "2025-04-20", tier, claim, {"offer": doc})


def test_python_signatures_verify_in_rust(world):
    _, doc, _ = world
    r = check_bundle(bundle(doc))
    assert r["ok"], r
    pv = r["public_values"]
    assert pv["result"] is True
    assert pv["documents"][0]["signer"] == "Harbor Realty"
    assert "1350000" not in json.dumps(pv), "document data must not appear in public values"


def test_every_tier(world):
    _, doc, _ = world
    form = json.dumps({"combine": "all", "conditions": [{"field": "offer.amount", "op": ">", "value": 1300000}]})
    for tier, claim in [("form", form), ("formula", "offer.amount > 1.3e6"), ("script", "offer.amount > 1300000")]:
        assert check_bundle(bundle(doc, claim, tier))["public_values"]["result"] is True


def test_tampered_document_rejected(world):
    _, doc, _ = world
    b = bundle(doc)
    payload = base64.b64decode(b["documents"][0]["payload_b64"]).replace(b"1350000", b"1950000")
    b["documents"][0]["payload_b64"] = base64.b64encode(payload).decode()
    r = check_bundle(b)
    assert not r["ok"] and "bad signature" in r["error"]


def test_acceptance_rules(world):
    agent, doc, registry = world
    pv = check_bundle(bundle(doc))["public_values"]
    accept_public_values(pv, SESSION, MESSAGE, registry, "2025-04-20")
    with pytest.raises(Rejected, match="different negotiation"):
        accept_public_values(pv, "22" * 32, MESSAGE, registry, "2025-04-20")
    with pytest.raises(Rejected, match="different message"):
        accept_public_values(pv, SESSION, "something else", registry, "2025-04-20")
    with pytest.raises(Rejected, match="untrusted"):
        accept_public_values(pv, SESSION, MESSAGE, Registry(), "2025-04-20")
    with pytest.raises(Rejected, match="recent"):
        accept_public_values(pv, SESSION, MESSAGE, registry, "2025-05-20")
    false_pv = check_bundle(bundle(doc, "offer.amount > 2e6"))["public_values"]
    with pytest.raises(Rejected, match="false"):
        accept_public_values(false_pv, SESSION, MESSAGE, registry, "2025-04-20")


def test_impostor_signer_rejected(world):
    _, _, registry = world
    impostor = Signer("Harbor Realty")  # same name, different key
    doc = impostor.sign("purchase_offer", {"amount": 2_000_000, "closing_days": 30}, "2025-04-14")
    pv = check_bundle(bundle(doc))["public_values"]
    assert pv["result"] is True  # the signature is valid for the impostor's own key...
    with pytest.raises(Rejected, match="untrusted"):  # ...but that key is not the registered one
        accept_public_values(pv, SESSION, MESSAGE, registry, "2025-04-20")


def test_checker_errors_are_not_false(world):
    c = Checker()
    docs = {"offer": {"signer": "x", "kind": "k", "signed_at": "2025-04-14", "data": {"amount": 5}}}
    assert c.evaluate("formula", "offer.price > 1", docs, "2025-04-20")["ok"] is False
    assert c.evaluate("formula", "offer.amount > 1", docs, "2025-04-20")["result"] is True
    c.close()

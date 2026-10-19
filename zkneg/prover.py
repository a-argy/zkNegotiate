"""Real proofs: wraps the `zkn` binary (crates/zkn), and the checks the
receiving side must apply to a proof's public values."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .documents import Registry, message_digest

ROOT = Path(__file__).resolve().parent.parent
ZKN = Path(os.environ.get("ZKN_BIN", ROOT / "target" / "release" / "zkn"))


def _run(args: list[str], env: dict | None = None) -> dict:
    r = subprocess.run([str(ZKN), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise RuntimeError(f"zkn {args[0]} failed: {r.stderr.strip()[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def execute(bundle: dict) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(bundle, f)
    return _run(["execute", f.name])


def prove(bundle: dict, out_path: str, groth16: bool = False) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(bundle, f)
    return _run(["prove", f.name, out_path, *(["--groth16"] if groth16 else [])])


def verify(proof_path: str) -> dict:
    return _run(["verify", proof_path])


class Rejected(Exception):
    pass


def accept_public_values(
    pv: dict, session_id: str, message: str, registry: Registry, today: str, max_age_days: int = 2
) -> None:
    """Everything the counterparty checks beyond the proof itself. Raises
    Rejected with the reason."""
    if not pv["result"]:
        raise Rejected("the check evaluated to false")
    if pv["session_id"] != session_id:
        raise Rejected("proof is for a different negotiation")
    if pv["message_digest"] != message_digest(message):
        raise Rejected("proof is attached to a different message")
    for d in pv["documents"]:
        if not registry.trusts(d["signer"], d["signer_key"]):
            raise Rejected(f"document {d['label']!r} is signed by an untrusted key for {d['signer']!r}")
    age = (dt.date.fromisoformat(today) - dt.date.fromisoformat(pv["now"])).days
    if not 0 <= age <= max_age_days:
        raise Rejected(f"proof evaluated at {pv['now']}, not recent")


def check_bundle_native(bundle: dict) -> dict:
    """The public values a proof would commit, computed without proving
    (ZKN_PROOFS=native). Not evidence of anything to a counterparty."""
    from .checker import check_bundle

    r = check_bundle(bundle)
    if not r.get("ok"):
        raise RuntimeError(r.get("error"))
    return r["public_values"]

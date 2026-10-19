"""Python wrapper around the native `claimcheck` binary (crates/claimlang).

It runs the same `claimlang` code as the zkVM guest, so a claim that checks
here produces the same result inside a proof.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINARY = Path(os.environ.get("ZKN_CLAIMCHECK", ROOT / "target" / "release" / "claimcheck"))


def build() -> None:
    subprocess.run(["cargo", "build", "--release", "-p", "claimlang", "--bin", "claimcheck"], cwd=ROOT, check=True)


class Checker:
    """A long-lived `claimcheck eval` process (one request per line)."""

    def __init__(self) -> None:
        if not BINARY.exists():
            build()
        self.proc = subprocess.Popen(
            [str(BINARY), "eval"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
        )
        self.lock = threading.Lock()

    def _request(self, req: dict) -> dict:
        with self.lock:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            return json.loads(self.proc.stdout.readline())

    def evaluate(self, tier: str, claim: str, docs: dict, now: str) -> dict:
        """docs: label -> {"signer", "kind", "signed_at", "data"}.
        Returns {"ok", "canonical", "result"} or {"ok": False, "error"}."""
        return self._request({"tier": tier, "claim": claim, "docs": docs, "now": now})

    def evaluate_many(self, tier: str, claim: str, docs_list: list[dict], now: str) -> dict:
        """Returns {"ok", "canonical", "results": [True/False/None...], "errors"}."""
        return self._request({"tier": tier, "claim": claim, "docs_list": docs_list, "now": now})

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait()


def check_bundle(bundle: dict) -> dict:
    """Full check, signatures included: exactly the public values a proof
    would commit. Returns {"ok", "public_values"} or {"ok": False, "error"}."""
    if not BINARY.exists():
        build()
    r = subprocess.run([str(BINARY), "bundle"], input=json.dumps(bundle), capture_output=True, text=True)
    return json.loads(r.stdout)

"""Signed documents: Ed25519 over the exact payload bytes, matching
claimlang::check_bundle (domain-separated, see DOCUMENT_DOMAIN)."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

DOMAIN = b"zknegotiate/document/v1\0"


def _pub_hex(key: Ed25519PublicKey) -> str:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


@dataclass
class SignedDocument:
    payload: bytes
    signature: bytes
    public_key: str  # hex

    @property
    def content(self) -> dict:
        return json.loads(self.payload)

    def to_json(self, label: str) -> dict:
        return {
            "label": label,
            "payload_b64": base64.b64encode(self.payload).decode(),
            "signature_hex": self.signature.hex(),
            "public_key_hex": self.public_key,
        }

    def verify(self) -> bool:
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.public_key)).verify(self.signature, DOMAIN + self.payload)
            return True
        except Exception:
            return False


class Signer:
    """A party that signs documents (a bidder, an employer, an appraiser...)."""

    def __init__(self, name: str, key: Ed25519PrivateKey | None = None):
        self.name = name
        self.key = key or Ed25519PrivateKey.generate()

    @classmethod
    def from_seed(cls, name: str, seed: bytes) -> "Signer":
        """Deterministic key, for reproducible experiments only."""
        return cls(name, Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed).digest()))

    @property
    def public_key(self) -> str:
        return _pub_hex(self.key.public_key())

    def private_hex(self) -> str:
        return self.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()

    def sign(self, kind: str, data: dict, signed_at: str) -> SignedDocument:
        payload = json.dumps(
            {"signer": self.name, "kind": kind, "signed_at": signed_at, "data": data},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return SignedDocument(payload, self.key.sign(DOMAIN + payload), self.public_key)


class Registry:
    """Signers the verifying side trusts: name -> public key."""

    def __init__(self, keys: dict[str, str] | None = None):
        self.keys = dict(keys or {})

    def add(self, signer: Signer) -> None:
        self.keys[signer.name] = signer.public_key

    def trusts(self, name: str, public_key_hex: str) -> bool:
        return self.keys.get(name) == public_key_hex

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.keys, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Registry":
        return cls(json.loads(path.read_text()))


def message_digest(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()


def make_bundle(
    session_id: str, message: str, now: str, tier: str, claim: str, documents: dict[str, SignedDocument]
) -> dict:
    """A claimlang::BundleJson."""
    return {
        "session_id": session_id,
        "message_digest": message_digest(message),
        "now": now,
        "tier": tier,
        "claim": claim,
        "documents": [doc.to_json(label) for label, doc in documents.items()],
    }

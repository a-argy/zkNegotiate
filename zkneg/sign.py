"""Sign documents for the negotiators' briefings.

  python -m zkneg.sign keygen --name "Harbor Realty"
      new Ed25519 key in keys/, public key added to keys/registry.json
  python -m zkneg.sign doc --name "Harbor Realty" --kind purchase_offer --label offer \\
      --data offer.json --out signed_offer.json
  python -m zkneg.sign doc ... --pdf offer.pdf
      extracts the fields from a PDF with the local model first (needs the
      `discord` extra for pypdf); review the extracted JSON before signing

The output file is what a negotiator's briefing takes: the exact signed
bytes, signature and public key (claimlang's SignedDocumentJson).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import llm
from .documents import Registry, Signer

KEYS = Path("keys")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_signer(name: str) -> Signer:
    path = KEYS / f"{slug(name)}.key"
    if not path.exists():
        raise SystemExit(f"no key for {name!r}; run: python -m zkneg.sign keygen --name {name!r}")
    return Signer(name, Ed25519PrivateKey.from_private_bytes(bytes.fromhex(path.read_text().strip())))


def extract_pdf(path: str, kind: str) -> dict:
    from pypdf import PdfReader

    text = "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    reply = llm.chat(
        [{"role": "user", "content": f"Extract the key fields of this {kind} as a flat JSON object with snake_case "
          f"keys. Numbers as plain numbers (no $ or commas), dates as YYYY-MM-DD.\n\n{text[:12000]}"}],
        schema={"type": "object"},
    )
    return json.loads(reply)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--name", required=True)
    d = sub.add_parser("doc")
    d.add_argument("--name", required=True)
    d.add_argument("--kind", required=True)
    d.add_argument("--label", required=True)
    d.add_argument("--data")
    d.add_argument("--pdf")
    d.add_argument("--signed-at", default=dt.date.today().isoformat())
    d.add_argument("--out", required=True)
    args = ap.parse_args()

    KEYS.mkdir(exist_ok=True)
    reg_path = KEYS / "registry.json"
    registry = Registry.load(reg_path) if reg_path.exists() else Registry()
    if args.cmd == "keygen":
        signer = Signer(args.name)
        path = KEYS / f"{slug(args.name)}.key"
        path.write_text(signer.private_hex())
        path.chmod(0o600)
        registry.add(signer)
        registry.save(reg_path)
        print(f"{args.name}: {signer.public_key}  (registry: {reg_path})")
        return

    if args.pdf:
        data = extract_pdf(args.pdf, args.kind)
        print("extracted:", json.dumps(data, indent=2))
    elif args.data:
        data = json.loads(Path(args.data).read_text())
    else:
        raise SystemExit("give --data or --pdf")
    doc = load_signer(args.name).sign(args.kind, data, args.signed_at)
    Path(args.out).write_text(json.dumps(doc.to_json(args.label), indent=2))
    print(f"signed {args.kind} as {args.name!r} -> {args.out}")


if __name__ == "__main__":
    main()

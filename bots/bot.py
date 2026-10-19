"""A zkNegotiate negotiator on Discord.

  python bots/bot.py --role seller      (and, in another shell, --role buyer)

Environment (see .env.example): DISCORD_TOKEN_<ROLE>, BRIEFING_CHANNEL_<ROLE>_ID,
NEGOTIATION_CHANNEL_ID, OTHER_BOT_ID, ZKN_PROOFS = zk | native.

Briefing channel: plain text becomes private instructions; attached signed
documents (from `python -m zkneg.sign doc`) become the bot's evidence.
Negotiation channel: `!start` opens a session. When the model asks to back a
claim, the bot formalizes it, checks it on its documents, and (ZKN_PROOFS=zk)
proves it with `zkn`, attaching proof.bin and the public values. The receiving
bot verifies the proof and the session, message, signer and date bindings
before telling its model the claim is VERIFIED. ZKN_PROOFS=native skips the
proof (for running without a prover) and is labelled as unproven.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import hashlib
import io
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from zkneg.checker import Checker  # noqa: E402
from zkneg.claims import DocSpec, Field  # noqa: E402
from zkneg.documents import Registry, SignedDocument, make_bundle  # noqa: E402
from zkneg.negotiation import Party, Turn, ask_turn, formalize_and_check  # noqa: E402
from zkneg import prover  # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zkneg")

ap = argparse.ArgumentParser()
ap.add_argument("--role", choices=["seller", "buyer"], required=True)
ROLE = ap.parse_args().role
OTHER = "buyer" if ROLE == "seller" else "seller"
TOKEN = os.environ[f"DISCORD_TOKEN_{ROLE.upper()}"]
BRIEFING_CHANNEL = int(os.environ[f"BRIEFING_CHANNEL_{ROLE.upper()}_ID"])
NEGOTIATION_CHANNEL = int(os.environ["NEGOTIATION_CHANNEL_ID"])
OTHER_BOT_ID = int(os.environ["OTHER_BOT_ID"])
PROOFS = os.environ.get("ZKN_PROOFS", "zk")
REGISTRY = Registry.load(Path(os.environ.get("ZKN_REGISTRY", "keys/registry.json")))

BASE = {
    "seller": "You are negotiating to sell. Get the best price you can.",
    "buyer": "You are negotiating to buy. Get the best price you can. Counterparties often exaggerate.",
}
PROOF_NOTE = (" Messages may carry a VERIFIED tag: a zero-knowledge proof that the shown check passed on documents "
              "signed by the named party. It proves exactly what the check says, nothing more.")

bot = commands.Bot(command_prefix="!" if ROLE == "seller" else "?", intents=discord.Intents.all())
checker = Checker()
state = {"briefing": "", "docs": {}, "turns": [], "session": None}


def today() -> str:
    return dt.date.today().isoformat()


def doc_specs() -> list[DocSpec]:
    """Schema of the bot's own documents, inferred from their data."""
    specs = []
    for label, doc in state["docs"].items():
        c = doc.content
        fields = []
        for k, v in c["data"].items():
            t = ("bool" if isinstance(v, bool) else "number" if isinstance(v, (int, float))
                 else "list" if isinstance(v, list) else "date" if isinstance(v, str) and len(v) == 10 and v[4] == "-"
                 else "string")
            fields.append(Field(k, t))
        specs.append(DocSpec(label, c["kind"], c["signer"], c["kind"].replace("_", " "), fields))
    return specs


def party() -> Party:
    system = BASE[ROLE] + PROOF_NOTE + "\n\nYour private briefing:\n" + (state["briefing"] or "(none)")
    if state["docs"]:
        system += "\n\nYour signed documents (private):\n" + "\n".join(
            f"`{label}`: {json.dumps(doc.content)}" for label, doc in state["docs"].items())
    values = {label: doc.content for label, doc in state["docs"].items()}
    return Party(ROLE, system, doc_specs(), values, can_prove=bool(state["docs"]))


def render(a: dict) -> str:
    text = str(a.get("message", ""))[:1500]
    if isinstance(a.get("offer"), (int, float)) and a["offer"] > 0:
        text += f"\n[offer: ${a['offer']:,.0f}]"
    if a.get("accept"):
        text += "\n[accepts your offer]"
    if a.get("walk_away"):
        text += "\n[walks away]"
    return text


async def respond(channel: discord.abc.Messageable) -> None:
    me = party()
    a = await asyncio.to_thread(ask_turn, me, state["turns"], len(state["turns"]))
    content = render(a)
    files, proof_info = [], None
    request = str(a.get("prove") or "").strip()
    if request and me.can_prove:
        proof_info = await asyncio.to_thread(formalize_and_check, checker, me, request, today(), len(state["turns"]))
        if proof_info["passed"]:
            bundle = make_bundle(state["session"], content, today(), "formula", proof_info["check"], state["docs"])
            if PROOFS == "zk":
                out = Path(tempfile.mkdtemp()) / "proof.bin"
                res = await asyncio.to_thread(prover.prove, bundle, str(out))
                files = [discord.File(out, "proof.bin"),
                         discord.File(io.BytesIO(json.dumps(res["public_values"], indent=2).encode()), "claim.json")]
                log.info("proved %r in %.1fs", proof_info["check"], res["prove_s"])
            else:
                pv = prover.check_bundle_native(bundle)
                files = [discord.File(io.BytesIO(json.dumps(pv, indent=2).encode()), "claim-unproven.json")]
    state["turns"].append(Turn(ROLE, a.get("message", ""), a.get("offer"), bool(a.get("accept")),
                               bool(a.get("walk_away")), request, proof_info))
    await channel.send(content, files=files)


async def read_proof(message: discord.Message) -> dict | None:
    """Verifies an attached proof; returns {"check","passed","signers"} or None."""
    att = {a.filename: a for a in message.attachments}
    if "proof.bin" in att:
        path = Path(tempfile.mkdtemp()) / "proof.bin"
        path.write_bytes(await att["proof.bin"].read())
        res = await asyncio.to_thread(prover.verify, str(path))
        if not res.get("valid"):
            log.warning("invalid proof: %s", res.get("error"))
            return None
        pv = res["public_values"]
        try:
            prover.accept_public_values(pv, state["session"], message.content, REGISTRY, today())
        except prover.Rejected as e:
            log.warning("proof rejected: %s", e)
            return None
        return {"check": pv["claim"], "passed": True, "signers": [d["signer"] for d in pv["documents"]]}
    if "claim-unproven.json" in att:
        log.info("counterparty sent an unproven claim (native mode); not treating it as verified")
    return None


@bot.event
async def on_message(message: discord.Message) -> None:
    await bot.process_commands(message)
    if message.author.id == bot.user.id or message.content[:1] in "!?":
        return
    if message.channel.id == BRIEFING_CHANNEL:
        state["briefing"] += message.content + "\n"
        for a in message.attachments:
            raw = json.loads(await a.read())
            doc = SignedDocument(base64.b64decode(raw["payload_b64"]), bytes.fromhex(raw["signature_hex"]),
                                 raw["public_key_hex"])
            if not doc.verify():
                await message.channel.send(f"{a.filename}: bad signature, ignored")
                continue
            state["docs"][raw["label"]] = doc
            await message.channel.send(f"stored `{raw['label']}` signed by {doc.content['signer']}")
        return
    if message.channel.id == NEGOTIATION_CHANNEL and message.author.id == OTHER_BOT_ID and state["session"]:
        proof = await read_proof(message)
        state["turns"].append(Turn(OTHER, message.content, None, False, False, "", proof))
        if "[accepts your offer]" in message.content or "[walks away]" in message.content:
            return
        await respond(message.channel)


@bot.command(name="start")
async def start(ctx: commands.Context) -> None:
    """Opens a session; the seller speaks first."""
    if ctx.channel.id != NEGOTIATION_CHANNEL:
        return
    state["session"] = hashlib.sha256(f"{ctx.channel.id}:{ctx.message.id}".encode()).hexdigest()
    state["turns"] = []
    if ROLE == "seller":
        await respond(ctx.channel)


@bot.event
async def on_command_error(ctx, error):  # the other bot's prefix is not ours
    if not isinstance(error, commands.CommandNotFound):
        raise error


@bot.listen("on_message")
async def session_from_other_prefix(message: discord.Message) -> None:
    # The buyer bot learns the session from the seller's `!start` command.
    if ROLE == "buyer" and message.channel.id == NEGOTIATION_CHANNEL and message.content.strip() == "!start":
        state["session"] = hashlib.sha256(f"{message.channel.id}:{message.id}".encode()).hexdigest()
        state["turns"] = []


bot.run(TOKEN)

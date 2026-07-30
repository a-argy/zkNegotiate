# zkNegotiate

**Can two AI negotiators escape the prisoner's dilemma by proving their claims instead of asserting them?**

zkNegotiate is a proof of concept for that question: two LLM agents negotiate in a shared Discord channel, each holding private briefing data, each able to attach cryptographic evidence to a factual claim without revealing the underlying numbers.

[![Watch the demo](https://img.shields.io/badge/▶️_Watch_Demo-Google_Drive-4285F4?style=for-the-badge&logo=googledrive&logoColor=white)](https://drive.google.com/file/d/1M-YDIFL-KZWT5vsMvRosMmEvMExtXJpz/view?usp=sharing)

> **Status: research prototype.** The negotiation loop, signed-document intake, claim-to-predicate translation, and zkVM harness are built and demonstrated end to end. The in-circuit predicate evaluation is a stub. [Read the honest scope section](#what-actually-works) before evaluating this as a security system — it is an exploration of a mechanism design idea, not a working proof system.

---

## The idea

Negotiation is a repeated prisoner's dilemma with a twist: the dominant strategy is to misrepresent your position, both parties know it, and so **all claims get discounted to zero regardless of whether they're true**. The seller who genuinely has a better offer in hand cannot convince the buyer, because that's exactly what a seller without one would say. Both parties end up worse off than if credible communication were possible. Information asymmetry destroys value that cooperation would have created.

Zero-knowledge proofs are an unusually good fit for this. A ZK proof lets you demonstrate a *predicate over private data* — "the competing offer exceeds $1.3M" — without revealing the data itself. That's precisely the shape of a negotiation claim: you want to prove the comparison, not disclose the number, because the number is your leverage.

**The thesis:** if counterparties can selectively disclose verifiable facts about their private information, they can escape the equilibrium where nothing anyone says carries weight, and reach outcomes neither could reach through assertion alone.

zkNegotiate is an attempt to build the smallest system that tests this.

---

## Worked example: proving a competing offer

You're selling a property. A competing buyer has bid above your current prospect, and you want the prospect to know that — without revealing the amount or the other buyer's identity.

**1. Obtain signed evidence.** The competing buyer's agent signs a structured offer (RSA-2048, PKCS#1 v1.5 over SHA-256):

```json
{
  "signed_data": {
    "signer": "Alice",
    "data": { "offer_amount": 1350000, "closing_days": 30 },
    "signature": "3a9f...",
    "signed_at": "2025-04-11T22:46:03Z"
  }
}
```

A separate PDF ingestion bot handles this: it extracts structured fields from an offer document with an LLM, then signs the canonicalized JSON.

**2. Brief your agent.** Upload the signed JSON to your bot's private briefing channel. It stores the structured data plus the signer's public key reference, and never shows the raw numbers in the negotiation channel.

**3. Negotiate.** Your bot says: *"I have an offer above $1.3 million with a 30-day close."* A second LLM call (temperature 0) translates that natural-language claim into a machine-checkable predicate over the briefing data:

```rust
json_dicts[0]["signed_data"].get("data").unwrap()
    .get("offer_amount").unwrap().as_f64().unwrap() > 1300000.0
```

This translation step is the interesting part of the design. The agent is not trusted to evaluate its own claim — it is only trusted to *state* the claim formally, in a form something else can check. The claim becomes an object separate from the assertion.

**4. Prove.** The predicate, the signed documents, and the relevant public keys go to an SP1 zkVM service, which returns a verification summary and public values committing to what was checked.

**5. Attach.** Your bot attaches `verification.txt` to its reply. The counterparty's bot sees the predicate that was checked and the result — learning that a legitimate competing offer exceeds their bid, and nothing more.

---

## Architecture

```
  Discord (negotiation channel)
       │
  ┌────┴─────┐              ┌──────────────┐
  │  bot1    │◄────────────►│    bot2      │    Mistral large — negotiation
  │  (!)     │              │    (?)       │    + claim→predicate translation
  └────┬─────┘              └──────┬───────┘
       │  private briefing channels │
       │                            │
       └──────────┬─────────────────┘
                  ▼
        Actix-web service (Cloud Run)
                  │
                  ▼
           SP1 zkVM guest
     reads: predicate, signed docs, public keys
     commits: public keys, predicate, verdict bits
```

| Component | File |
|---|---|
| Agent — personalities, context assembly, predicate generation | `discord/agent.py` |
| Bot entrypoints (prefix `!` / `?`, separate briefing channels) | `discord/bot1.py`, `discord/bot2.py` |
| PDF → structured JSON → RSA signing | `discord/pdf_bot.py` |
| RSA keypair management | `discord/key_manager.py` |
| HTTP client to the prover service | `discord/gcp_client.py` |
| Actix-web prover service | `GCP/script/src/main.rs` |
| SP1 guest program | `GCP/verification_proof/program/src/main.rs` |

**Stack:** Python 3.13, `discord.py`, Mistral (`mistral-large-latest`), `pycryptodome`; Rust 1.81 with SP1 4.1.7 and Actix-web 4.4, deployed to Cloud Run.

Each bot keeps a private briefing channel and a shared negotiation channel, maintains conversation history trimmed to recent turns, and is prompted to stay under 100 words and never volunteer exact figures.

---

## What actually works

Being explicit, because the gap between the design and the implementation is the most important thing to understand about this repo.

**Built and working:**
- Two independent Discord bots negotiating autonomously via Mistral, with private briefing intake and a shared transcript.
- PDF → structured JSON extraction, canonicalization, and RSA-2048 / PKCS#1 v1.5 / SHA-256 signing.
- LLM translation of natural-language claims into formal Rust predicates over the briefing data (temperature 0, markdown-stripped, one predicate per line).
- A deployed Actix-web service that pipes predicate + documents + public keys into an SP1 zkVM guest, which executes and commits public values.
- Verification artifacts attached to Discord replies and rendered for the counterparty.

**Stubbed — the honest gaps:**
- **The predicate is never evaluated.** `verify_conditions()` in the guest program is `{ true }` ([`GCP/verification_proof/program/src/main.rs:20-23`](GCP/verification_proof/program/src/main.rs)). The predicate string travels all the way into the zkVM and is committed to the public values, but nothing parses or executes it.
- **The signature is never checked.** `verify_signature()` is likewise `{ true }` ([`:15-18`](GCP/verification_proof/program/src/main.rs)). No `pkcs1_15.verify` call exists on the Python side either.
- **No proof is generated.** The host calls `client.execute()`, not `client.prove()` ([`GCP/script/src/main.rs:92`](GCP/script/src/main.rs)); the `proof` and `verification_key` fields in the response are empty strings.
- Counterparty-side verification is a `TODO` returning `True` (`discord/agent.py:243`).
- Documents are POSTed to the prover service in plaintext, so the service is a trusted party — the privacy guarantee is architectural intent, not yet cryptographic.

So the system demonstrates the *shape* of the mechanism — claim → formal predicate → committed public values → counterparty inspection — with the verification step held constant at `true`.

### The hard part, and why it's hard

The unsolved problem is step 4: **how do you evaluate an LLM-generated Rust predicate inside a zkVM?**

The zkVM proves the execution of a *fixed* program, compiled ahead of time to a RISC-V ELF with a verifying key derived from it. But the predicate is generated per claim, at negotiation time. That's a real tension, and there are two ways out:

1. **Ship an interpreter in the guest.** Compile a small expression evaluator into the ELF and feed it the predicate as data. The verifying key stays fixed, which is what makes verification meaningful — the counterparty checks against a key they already trust. The cost is that the predicate language is now whatever the interpreter supports, and the interpreter itself is attack surface inside the circuit.
2. **Compile a fresh guest per claim.** Full Rust expressivity, but the verifying key changes with every claim, so the counterparty must re-derive and re-trust it each time — which substantially weakens the point of verifying at all, and puts a Rust toolchain in the negotiation loop.

Option 1 is the right answer, and it's the work this prototype stops short of. Recognizing that the *predicate language* — not the proving system — is the actual design problem was the most useful thing this project produced.

---

## Running it

```bash
conda env create -f discord/local_env.yml    # fuller dep list than pyproject.toml
```

Set the environment variables (see `.env.example`):

| | |
|---|---|
| Discord | `DISCORD_TOKEN_BOT1`, `DISCORD_TOKEN_BOT2`, `FIRST_BOT_ID`, `SECOND_BOT_ID` |
| Channels | `BRIEFING_CHANNEL_ALPHA_ID`, `BRIEFING_CHANNEL_OMEGA_ID`, `NEGOTIATION_CHANNEL_ID` |
| Mistral | `MISTRAL_API_KEY` |
| Prover | `GCP_ENDPOINT`, `GCP_API_KEY` |

```bash
python discord/bot1.py &                     # prefix !
python discord/bot2.py &                     # prefix ?
cd GCP/script && cargo run --release         # prover service on :8080
```

Then brief each bot in its private channel and run `!start` to open the negotiation. `!transcript` / `?transcript` dumps the history.

`example_keys/` contains demo keypairs for fixture signers (Alice, Bob, Charlie, Diana, Eve). They are throwaway keys for local runs — generate your own with `discord/generate_keys.py` for anything real.

---

## Provenance and scope

Built for **Stanford CS 153 (Infrastructure at Scale for AI Agents)**, starting from the course's Discord/Mistral agent template. The zkNegotiate system — signed document intake, predicate generation, the SP1 service, and the negotiation design — is my work; the Discord/Mistral scaffolding is the course template.

This is a course prototype exploring a mechanism design idea. It has not been audited, the verification path is stubbed as described above, and it should not be used to make decisions with real money attached.

## References

- [SP1 zkVM](https://github.com/succinctlabs/sp1) — Succinct Labs
- Related work: [zkSecureDNA](https://github.com/a-argy/zkSecureDNA), applying SP1 circuits to biosecurity screening

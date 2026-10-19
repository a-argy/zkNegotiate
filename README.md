# zkNegotiate

**Verifiable claims from AI agents.** LLM negotiators back their factual claims with zero-knowledge proofs over privately held, third-party-signed documents. Then three experiments ask the question that matters for AI oversight: *does a valid proof mean an honest claim?*

- **Write-up:** [docs/WRITEUP.md](docs/WRITEUP.md)
- **Full experiment results:** [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)

> Research prototype, not audited. The experiments use one open-weight model (Qwen3 8B) on small, synthetic datasets; see the limitations in the write-up.

## The idea

An agent says *"I have a signed offer above $1.3 million."* With zkNegotiate it can attach a proof that a check passed on a document signed by a real third party, without revealing the document. But the check is written by the agent's own model, from its own words. The proof guarantees that **a check passed on genuine documents**; nothing guarantees that **the check means what the agent said**. zkNegotiate builds the whole pipeline end to end, then measures that gap.

```
 signed documents ──┐                     ┌── counterparty sees: the check, who signed,
 (Ed25519, private) │                     │   session, message hash, date, pass/fail
                    ▼                     │
 "offer above   → formal check  → SP1 zkVM proof ──► verify with a pinned key and
  $1.3M"          (LLM, 3 tiers)   (claimlang)          check session/message/signer/date
```

## What's built

| Component | What it does |
|---|---|
| `crates/claimlang` | The claim checker: three claim languages (**form**, **formula**, **script**), Ed25519 signed-document bundles, and the public values. One function, `check_bundle`, is used by both the zkVM guest and the native tools. |
| `zk/program` | SP1 6.9 guest: verifies signatures and evaluates the claim with a **fixed interpreter**, so the verifying key never changes. SHA-256 and Ed25519 run on SP1's accelerated precompiles. |
| `crates/zkn` | `zkn prove / verify / execute / vkey` (CPU or CUDA). Verification derives the key from its own copy of the circuit. |
| `zkneg/` | Python side: local model client (Ollama, cached), document signing, claim formalization with validate-and-retry, the negotiation engine, and the counterparty's acceptance rules (session, message, trusted signer, date). |
| `bots/bot.py` | Discord negotiator (seller or buyer) that attaches real proofs and verifies the other side's before its model sees "VERIFIED". |
| `experiments/` | Datasets, runners and analysis for the three experiments. |

**The three claim tiers**, from least to most expressive:

| Tier | Example |
|---|---|
| form | `{"combine":"all","conditions":[{"field":"offer.amount","op":">","value":1300000}]}` |
| formula | `count(book.bids, b => b.amount > 1.2e6 && b.closing_days <= 30) >= 2` |
| script (Rhai) | `let k = 0; for b in book.bids { if b.amount > 1200000 { k += 1; } } k >= 2` |

## Proof performance

Real SP1 6.9 proofs on one NVIDIA L4 GPU (`scripts/bench_proofs.sh`). Compressed proofs are 1.27 MB and verify in about 57 ms, whatever the claim.

| Claim | Document size | Tier | Cycles | Prove (L4) |
|---|---|---|---|---|
| offer > $1.3M and closes within 30 days | 1 offer | form | 195K | 5.6 s |
| | | formula | 209K | 5.5 s |
| | | script | 6.0M | 8.6 s |
| at least 2 bids > $1.2M closing within 30 days | 10 bids | formula | 538K | 6.0 s |
| | | script | 6.4M | 9.8 s |
| | 100 bids | formula | 3.3M | 6.9 s |
| | | script | 8.7M | 9.7 s |
| | 1,000 bids | formula | 31.2M | 19.5 s |
| | | script | 31.8M | 20.2 s |

The Rhai interpreter adds a fixed ~6M cycles of start-up, so scripts cost about 3–4 extra seconds per proof. A Groth16 wrap (the on-chain-sized proof) took 157 s and produces a 1.9 KB proof file including public values, verified in 430 ms. The circuit rejects any tampered document outright, and a false claim yields a valid proof whose committed result is `false`, which the counterparty rejects.

## Results in brief

With Qwen3 8B playing every role (formalizer, adversary, auditor, buyer and seller) on one L4 GPU. Details are in [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md); raw data is in [`results/`](results).

| Question | Finding |
|---|---|
| **1. Does the model's check mean what it said?** (58 claims × 3 tiers) | About **1 in 10 checks was unsafe**: it would pass where the claim is false (form 10%, formula 9%, script 5%). Examples: `>=` for "above", "any" for "every". The restricted form tier was perfect on claims it can express, but forced an unsafe approximation in 26% of the claims it can't. All 33 vague claims were correctly refused. |
| **2. Can the counterparty's model catch a misleading check?** | **Only with reasoning.** Answering directly, it rejected 87–100% of honest checks. With thinking mode on, it accepted 85–92% of honest checks, rejected all 25 misleading checks that passed, and caught 12 of 14 subtle, unintentional errors. The 8B adversary was crude, so this is a lower bound on the difficulty. |
| **3. Do proofs change negotiations?** (60 negotiations) | **No, at this scale.** No bluff got through as a proof, but deal rates were identical (5 of 15 per condition), and buyers paid about 99% of their budget regardless. Only 3 of 30 ever mentioned the verified claims. |

**Takeaway:** a proof makes "this check passed on genuine data" unforgeable. Whether the check means what the agent said is a separate, model-level problem, and it failed about 1 time in 10 here.

## Running it

```sh
# Rust: claim checker + tests; prover (add --features cuda for GPU, needs SP1: `sp1up`)
cargo test -p claimlang
cargo build --release -p claimlang --bin claimcheck
cargo build --release -p zkn

# Python (3.12+)
uv venv && uv pip install cryptography requests python-dotenv pytest
.venv/bin/python -m pytest tests

# Local model
ollama serve & ollama pull qwen3:8b

# Experiments (LLM calls are cached in runs/llm_cache.sqlite)
.venv/bin/python -m experiments.exp1_faithfulness
.venv/bin/python -m experiments.exp2_deception      # uses exp1's results as honest controls
.venv/bin/python -m experiments.exp3_negotiation --workers 3
.venv/bin/python -m experiments.analyze            # writes runs/summary.md

# Proof benchmarks
.venv/bin/python -m experiments.bench_bundles && SP1_PROVER=cuda scripts/bench_proofs.sh
```

Discord: copy `.env.example` to `.env`, sign briefing documents with `python -m zkneg.sign`, then run `python bots/bot.py --role seller` and `--role buyer`. The Discord path has not been tested live in v2.

## History

v1 (April 2025) was a course project for Stanford CS 153: the agent loop worked, but the in-circuit checks were stubs that always returned true and no proof was generated. v2 replaces the generated-Rust approach with a fixed in-circuit interpreter, real signature checks and real proofs, and adds the experiments. The v1 code is in the git history.

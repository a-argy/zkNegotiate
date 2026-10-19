# zkNegotiate v2: status and plan

*Paused 2026-10-18. Work is on the local branch `v2` and hasn't been pushed.*

## Framing

**Verifiable claims from AI agents.** When an AI agent makes a factual claim, can it be required to back that claim with a proof? And does a valid proof actually mean the claim is honest?

The testbed is two LLM negotiators that hold private signed documents. A zero-knowledge proof guarantees that a check passed on genuine documents. It doesn't guarantee that the check means what the agent said. The second question is what the experiments measure.

## What's built (v2)

| Piece | Status |
|---|---|
| `crates/claimlang`: one claim checker shared by the proof and the native tools, with Ed25519 signed-document bundles. | Done; 12 Rust tests pass. |
| Three claim languages ("tiers"): **form** (flat field comparisons, filled in under a JSON schema), **formula** (JS-like expressions with list functions and dates), **script** (sandboxed Rhai programs). | Done. |
| `zk/program`: the SP1 6.9 guest, which verifies signatures and evaluates the claim. The proof shows the claim text, the signers, the session and the result, never the documents. | Compiles for SP1, Rhai included, with accelerated SHA-256 and Ed25519. Not yet proven on the GPU. |
| `crates/zkn`: prove and verify CLI. | Written; the GPU build was interrupted. |
| `zkneg/` (Python): local model client (Qwen3 8B via Ollama, cached), signing, claim formalization with a validate-and-retry loop, negotiation engine, and the counterparty's acceptance rules (session, message, trusted signer, date). | Done; 6 Python tests pass, including a tampered document and a same-name impostor key. |
| `bots/bot.py`: Discord bot (seller or buyer role) that attaches real proofs and verifies the other side's proofs before its model sees a "VERIFIED" tag. | Written, not tested live (needs your Discord tokens). |
| `experiments/`: datasets, runners and analysis for the three experiments. | Written. Only experiment 1 has run, and only partly. |

The old v1 stubs (signature and predicate checks that always returned true, no real proof) are deleted.

## Experiment 1, preliminary (36 of 58 claims, Qwen3 8B)

**Do not cite these as final.** The run stopped about 60% of the way through.

| Tier | Faithful (equivalent or correctly refused) | Unsafe (check passes when the claim is false) |
|---|---|---|
| form | 25/36 | 6/36 |
| formula | 31/36 | 3/36 |
| script | 32/36 | 2/36 |

Patterns so far:
- **The restricted form language invites unsafe approximations.** When a claim needs arithmetic or a list, the model often forces an approximate check instead of refusing.
- **More expressive languages are more faithful, but still fail.** For example, "every shipment arrived on time" became "*any* shipment arrived on time", a check that would pass for a supplier with only one on-time delivery.

## Remaining steps

1. **Finish experiment 1** (about 15 minutes on the Mac): `.venv/bin/python -m experiments.exp1_faithfulness`, then `python -m experiments.analyze`.
2. **Run experiment 2** (about 30 minutes): an adversarial model, whose documents show its claim is false, writes a check that still passes. A second model audits it as the counterparty. Measured per tier: deception success rate, and how often honest checks are accepted.
3. **Run experiment 3** (about 1 hour, or less on the GPU): 15 paired seeds × {strong seller, bluffing seller} × {talk only, proofs}. Measured: deal rate, price, and whether bluffs survive.
4. **Real proofs on the L4 GPU** (`scripts/bench_proofs.sh`): cycles, proving time, proof size and verify time for each tier and for documents of 10, 100 and 1,000 entries, plus one Groth16 proof. Rebuild on the VM with `cargo build --release -p zkn --features cuda` (`~/.sp1/bin` must be on PATH).
5. **Hand-check** about 30 to 50 of the experiment 1 and 2 labels (about an hour, by Anthony), so the labels can be called human-verified.
6. **Write-up and README** (`docs/WRITEUP.md`): framing, method, results, limitations. Limitations to state:
   - small samples;
   - one open model;
   - equivalence is tested on samples, not proven;
   - the auditor is the same model as the formalizer.
7. **Optional extras:**
   - a few-dollar comparison with a Claude model;
   - a test of the live Discord demo, with a re-recorded video.

## Resume notes

- Start the local model with `ollama serve`. The model `qwen3:8b` is already downloaded.
- LLM calls are cached in `runs/llm_cache.sqlite`, so reruns skip calls that have already finished.
- The GPU VM is `zk-gpu`, in project `cs229-489108`, zone us-central1-a, under account `anthony.argyropoulos1@gmail.com`. It's stopped, and that zone sometimes runs out of L4s.
- `~/.zknegotiate.env` holds an Anthropic key with no credit. It's unused, and the key should be rotated.

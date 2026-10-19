#!/usr/bin/env bash
# Prove and verify every bundle in runs/bench/bundles; one JSON line per step
# in runs/bench/results.jsonl. Usage: SP1_PROVER=cuda scripts/bench_proofs.sh [--groth16-only NAME]
set -euo pipefail
cd "$(dirname "$0")/.."
ZKN=target/release/zkn
OUT=runs/bench/results.jsonl
mkdir -p runs/bench/proofs
: > "$OUT"
for b in runs/bench/bundles/*.json; do
  name=$(basename "$b" .json)
  exe=$($ZKN execute "$b")
  prf=$($ZKN prove "$b" "runs/bench/proofs/$name.bin")
  ver=$($ZKN verify "runs/bench/proofs/$name.bin")
  jq -c -n --arg name "$name" --arg backend "${SP1_PROVER:-cpu}" --argjson exe "$exe" --argjson prf "$prf" --argjson ver "$ver" \
    '{name: $name, backend: $backend, cycles: $exe.cycles, prove_s: $prf.prove_s, proof_bytes: $prf.proof_bytes,
      verify_ms: $ver.verify_ms, valid: $ver.valid, result: $ver.public_values.result, claim: $ver.public_values.claim}' | tee -a "$OUT"
done
# One Groth16 wrap (the on-chain-sized proof) for the simplest claim.
g=$($ZKN prove runs/bench/bundles/simple-formula.json runs/bench/proofs/simple-formula.groth16.bin --groth16)
v=$($ZKN verify runs/bench/proofs/simple-formula.groth16.bin)
jq -c -n --argjson g "$g" --argjson v "$v" --arg backend "${SP1_PROVER:-cpu}" \
  '{name: "simple-formula-groth16", backend: $backend, prove_s: $g.prove_s, proof_bytes: $g.proof_bytes,
    verify_ms: $v.verify_ms, valid: $v.valid}' | tee -a "$OUT"

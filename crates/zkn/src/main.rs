//! `zkn`: prove and verify zkNegotiate claims.
//!
//!   zkn vkey                                   the claim circuit's verifying key
//!   zkn execute <bundle.json>                  run the circuit without proving (cycles, public values)
//!   zkn prove <bundle.json> <proof.bin> [--groth16]
//!   zkn verify <proof.bin>                     verify; print the public values
//!
//! Bundles are `claimlang::BundleJson`. Output is one JSON object on stdout.
//! `SP1_PROVER=cpu|cuda` selects the backend (cuda needs `--features cuda`).
//! Verification derives the verifying key from this build's own copy of the
//! circuit; a proof never supplies its own key.

use std::time::Instant;

use anyhow::{bail, Context};
use claimlang::{BundleJson, PublicValues};
use serde_json::json;
use sp1_sdk::{
    include_elf, Elf, HashableKey, ProveRequest, Prover, ProverClient, ProvingKey, SP1ProofWithPublicValues,
    SP1Stdin,
};

const CLAIM_ELF: Elf = include_elf!("zknegotiate-claim");

fn read_bundle(path: &str) -> anyhow::Result<claimlang::Bundle> {
    let text = std::fs::read_to_string(path).with_context(|| format!("reading {path}"))?;
    let b: BundleJson = serde_json::from_str(&text).context("parsing bundle")?;
    b.into_bundle().map_err(|e| anyhow::anyhow!("bad bundle: {e}"))
}

fn stdin_for(bundle: &claimlang::Bundle) -> SP1Stdin {
    let mut stdin = SP1Stdin::new();
    stdin.write(bundle);
    stdin
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let cmd = args.first().map(String::as_str).unwrap_or("");
    let out = match cmd {
        "vkey" => {
            let prover = ProverClient::builder().light().build().await;
            let pk = prover.setup(CLAIM_ELF).await?;
            json!({"vkey": pk.verifying_key().bytes32()})
        }
        "execute" => {
            let bundle = read_bundle(args.get(1).context("bundle path")?)?;
            let client = ProverClient::from_env().await;
            let pk = client.setup(CLAIM_ELF).await?;
            let t = Instant::now();
            let (pv, report) = client.execute(pk.elf().clone(), stdin_for(&bundle)).await?;
            if report.exit_code != 0 {
                bail!("circuit rejected the bundle (exit code {})", report.exit_code);
            }
            let pv: PublicValues = pv.clone().read();
            json!({
                "cycles": report.total_instruction_count() + report.total_syscall_count(),
                "execute_s": t.elapsed().as_secs_f64(),
                "public_values": pv.to_json(),
            })
        }
        "prove" => {
            let bundle = read_bundle(args.get(1).context("bundle path")?)?;
            let out_path = args.get(2).context("output path")?;
            let groth16 = args.iter().any(|a| a == "--groth16");
            let client = ProverClient::from_env().await;
            let pk = client.setup(CLAIM_ELF).await?;
            let stdin = stdin_for(&bundle);
            // Execute first: cheap, fails fast on a rejected bundle.
            let (_, report) = client.execute(pk.elf().clone(), stdin.clone()).await?;
            if report.exit_code != 0 {
                bail!("circuit rejected the bundle (exit code {})", report.exit_code);
            }
            let t = Instant::now();
            let proof = if groth16 {
                client.prove(&pk, stdin).groth16().await?
            } else {
                client.prove(&pk, stdin).compressed().await?
            };
            let prove_s = t.elapsed().as_secs_f64();
            let bytes = bincode::serialize(&proof)?;
            std::fs::write(out_path, &bytes)?;
            let pv: PublicValues = proof.public_values.clone().read();
            json!({
                "proof": out_path,
                "proof_bytes": bytes.len(),
                "kind": if groth16 { "groth16" } else { "compressed" },
                "cycles": report.total_instruction_count() + report.total_syscall_count(),
                "prove_s": prove_s,
                "vkey": pk.verifying_key().bytes32(),
                "public_values": pv.to_json(),
            })
        }
        "verify" => {
            let path = args.get(1).context("proof path")?;
            let bytes = std::fs::read(path).with_context(|| format!("reading {path}"))?;
            let proof: SP1ProofWithPublicValues = bincode::deserialize(&bytes).context("malformed proof")?;
            let prover = ProverClient::builder().light().build().await;
            let pk = prover.setup(CLAIM_ELF).await?;
            let t = Instant::now();
            match prover.verify(&proof, pk.verifying_key(), None) {
                Ok(()) => {
                    let pv: PublicValues = proof.public_values.clone().read();
                    json!({
                        "valid": true,
                        "verify_ms": t.elapsed().as_secs_f64() * 1e3,
                        "vkey": pk.verifying_key().bytes32(),
                        "public_values": pv.to_json(),
                    })
                }
                Err(e) => json!({"valid": false, "error": e.to_string()}),
            }
        }
        _ => {
            eprintln!("usage: zkn vkey | execute <bundle.json> | prove <bundle.json> <proof.bin> [--groth16] | verify <proof.bin>");
            std::process::exit(2);
        }
    };
    println!("{out}");
    Ok(())
}

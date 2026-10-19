//! The claim circuit: verifies the signed documents and evaluates the claim
//! over them with `claimlang::check_bundle`, the same function the native
//! tools use, then commits the public values (claim, signers, session,
//! result). The documents' data never leaves the prover.

#![no_main]
sp1_zkvm::entrypoint!(main);

pub fn main() {
    let bundle = sp1_zkvm::io::read::<claimlang::Bundle>();
    let public_values = claimlang::check_bundle(&bundle).expect("claim rejected");
    sp1_zkvm::io::commit(&public_values);
}

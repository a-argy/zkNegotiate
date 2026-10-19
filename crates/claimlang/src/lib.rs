//! zkNegotiate's claim checker.
//!
//! A negotiator backs a claim with signed documents it holds privately. A
//! [`Bundle`] carries the claim, the documents and their Ed25519 signatures;
//! [`check_bundle`] verifies the signatures, evaluates the claim over the
//! documents' data and returns the [`PublicValues`] the counterparty sees: the
//! claim text, who signed what, the session it belongs to, and the result,
//! never the documents themselves.
//!
//! The zkVM guest calls exactly `check_bundle`, and so do the native tools and
//! experiments, so a proof and a native check can never disagree.

pub mod form;
pub mod formula;
#[cfg(feature = "script")]
pub mod script;
pub mod value;

use base64::Engine as _;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub use value::{Error, Value};
use value::bail;

/// Domain separator prepended to a document's payload before signing.
pub const DOCUMENT_DOMAIN: &[u8] = b"zknegotiate/document/v1\0";
pub const PUBLIC_VALUES_VERSION: u32 = 1;

/// The three claim languages, from least to most expressive.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Tier {
    Form,
    Formula,
    Script,
}

/// A claim's committed text: canonical JSON for forms, verbatim otherwise.
pub fn canonical_claim(tier: Tier, src: &str) -> Result<String, Error> {
    Ok(match tier {
        Tier::Form => form::canonical(&form::parse(src)?),
        Tier::Formula => {
            formula::parse(src)?;
            src.trim().to_string()
        }
        Tier::Script => {
            #[cfg(feature = "script")]
            script::compile(src)?;
            #[cfg(not(feature = "script"))]
            bail!("script claims are not supported in this build");
            #[allow(unreachable_code)]
            src.trim().to_string()
        }
    })
}

/// Evaluates a claim over named bindings (document labels and `now`).
/// `Err` means the claim is malformed or ill-typed, not that it is false.
pub fn evaluate(tier: Tier, src: &str, bindings: &[(String, Value)]) -> Result<bool, Error> {
    match tier {
        Tier::Form => formula::evaluate(&form::to_expr(&form::parse(src)?)?, bindings),
        Tier::Formula => formula::evaluate(&formula::parse(src)?, bindings),
        #[cfg(feature = "script")]
        Tier::Script => script::evaluate(src, bindings),
        #[cfg(not(feature = "script"))]
        Tier::Script => bail!("script claims are not supported in this build"),
    }
}

/// The signed contents of a document.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DocumentPayload {
    pub signer: String,
    pub kind: String,
    pub signed_at: String,
    pub data: serde_json::Value,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SignedDocument {
    /// Name the claim uses for this document, chosen by the prover.
    pub label: String,
    /// Exact signed bytes (JSON of a [`DocumentPayload`]).
    pub payload: Vec<u8>,
    pub signature: Vec<u8>,
    pub public_key: [u8; 32],
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Bundle {
    /// Identifies the negotiation, so a proof cannot be replayed in another.
    pub session_id: [u8; 32],
    /// SHA-256 of the message this claim accompanies.
    pub message_digest: [u8; 32],
    /// The date the claim is evaluated at (`YYYY-MM-DD`), bound as `now`.
    pub now: String,
    pub tier: Tier,
    pub claim: String,
    pub documents: Vec<SignedDocument>,
}

/// What a document reveals publicly: its label, kind and signer, not its data.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DocumentRef {
    pub label: String,
    pub kind: String,
    pub signer: String,
    pub signer_key: [u8; 32],
}

/// Committed by the zkVM guest and checked by the counterparty.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PublicValues {
    pub version: u32,
    pub session_id: [u8; 32],
    pub message_digest: [u8; 32],
    pub now: String,
    pub tier: Tier,
    pub claim: String,
    pub documents: Vec<DocumentRef>,
    pub result: bool,
}

fn valid_label(s: &str) -> bool {
    let mut chars = s.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_lowercase() || c == '_')
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
        && s != "now"
}

/// Bindings for evaluating a claim: each label maps to its document's data
/// plus `_signer`, `_kind` and `_signed_at`; `now` is the evaluation date.
pub fn bindings(docs: &[(String, DocumentPayload)], now: &str) -> Result<Vec<(String, Value)>, Error> {
    value::parse_date(now)?;
    let mut out = Vec::with_capacity(docs.len() + 1);
    for (label, doc) in docs {
        if !valid_label(label) {
            bail!("invalid document label {label:?} (lowercase letters, digits, _)");
        }
        if out.iter().any(|(l, _): &(String, Value)| l == label) {
            bail!("duplicate document label {label:?}");
        }
        let mut data = match Value::from_json(&doc.data) {
            Value::Map(m) => m,
            _ => bail!("document {label:?}: data must be a JSON object"),
        };
        data.insert("_signer".into(), Value::Str(doc.signer.clone()));
        data.insert("_kind".into(), Value::Str(doc.kind.clone()));
        data.insert("_signed_at".into(), Value::Str(doc.signed_at.clone()));
        out.push((label.clone(), Value::Map(data)));
    }
    out.push(("now".into(), Value::Str(now.to_string())));
    Ok(out)
}

/// Verifies every signature and evaluates the claim. Errors on any invalid
/// signature or malformed claim; a well-formed false claim returns
/// `result: false`.
pub fn check_bundle(b: &Bundle) -> Result<PublicValues, Error> {
    let mut docs = Vec::with_capacity(b.documents.len());
    let mut refs = Vec::with_capacity(b.documents.len());
    for d in &b.documents {
        let key = VerifyingKey::from_bytes(&d.public_key)
            .map_err(|_| Error(format!("document {:?}: invalid public key", d.label)))?;
        let sig = Signature::from_slice(&d.signature)
            .map_err(|_| Error(format!("document {:?}: malformed signature", d.label)))?;
        let msg = [DOCUMENT_DOMAIN, &d.payload].concat();
        key.verify(&msg, &sig).map_err(|_| Error(format!("document {:?}: bad signature", d.label)))?;
        let payload: DocumentPayload = serde_json::from_slice(&d.payload)
            .map_err(|e| Error(format!("document {:?}: malformed payload: {e}", d.label)))?;
        refs.push(DocumentRef {
            label: d.label.clone(),
            kind: payload.kind.clone(),
            signer: payload.signer.clone(),
            signer_key: d.public_key,
        });
        docs.push((d.label.clone(), payload));
    }
    let bound = bindings(&docs, &b.now)?;
    let claim = canonical_claim(b.tier, &b.claim)?;
    let result = evaluate(b.tier, &claim, &bound)?;
    Ok(PublicValues {
        version: PUBLIC_VALUES_VERSION,
        session_id: b.session_id,
        message_digest: b.message_digest,
        now: b.now.clone(),
        tier: b.tier,
        claim,
        documents: refs,
        result,
    })
}

pub fn message_digest(message: &str) -> [u8; 32] {
    Sha256::digest(message.as_bytes()).into()
}

// ------------------------------------------------ JSON forms for the tools

fn hex32(s: &str, what: &str) -> Result<[u8; 32], Error> {
    let v = hex::decode(s.trim_start_matches("0x")).map_err(|_| Error(format!("{what}: not hex")))?;
    v.try_into().map_err(|_| Error(format!("{what}: expected 32 bytes")))
}

/// [`SignedDocument`] as exchanged with the Python side.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SignedDocumentJson {
    pub label: String,
    pub payload_b64: String,
    pub signature_hex: String,
    pub public_key_hex: String,
}

/// [`Bundle`] as exchanged with the Python side.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BundleJson {
    pub session_id: String,
    pub message_digest: String,
    pub now: String,
    pub tier: Tier,
    pub claim: String,
    pub documents: Vec<SignedDocumentJson>,
}

impl BundleJson {
    pub fn into_bundle(self) -> Result<Bundle, Error> {
        let b64 = base64::engine::general_purpose::STANDARD;
        let documents = self
            .documents
            .into_iter()
            .map(|d| {
                Ok(SignedDocument {
                    payload: b64.decode(&d.payload_b64).map_err(|_| Error("payload: not base64".into()))?,
                    signature: hex::decode(&d.signature_hex).map_err(|_| Error("signature: not hex".into()))?,
                    public_key: hex32(&d.public_key_hex, "public key")?,
                    label: d.label,
                })
            })
            .collect::<Result<_, Error>>()?;
        Ok(Bundle {
            session_id: hex32(&self.session_id, "session id")?,
            message_digest: hex32(&self.message_digest, "message digest")?,
            now: self.now,
            tier: self.tier,
            claim: self.claim,
            documents,
        })
    }
}

impl PublicValues {
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "version": self.version,
            "session_id": hex::encode(self.session_id),
            "message_digest": hex::encode(self.message_digest),
            "now": self.now,
            "tier": self.tier,
            "claim": self.claim,
            "documents": self.documents.iter().map(|d| serde_json::json!({
                "label": d.label, "kind": d.kind, "signer": d.signer,
                "signer_key": hex::encode(d.signer_key),
            })).collect::<Vec<_>>(),
            "result": self.result,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    fn signed(label: &str, key: &SigningKey, data: serde_json::Value) -> SignedDocument {
        let payload = serde_json::to_vec(&DocumentPayload {
            signer: "Alice Realty".into(),
            kind: "offer".into(),
            signed_at: "2025-04-11".into(),
            data,
        })
        .unwrap();
        let sig = key.sign(&[DOCUMENT_DOMAIN, &payload].concat());
        SignedDocument {
            label: label.into(),
            payload,
            signature: sig.to_bytes().to_vec(),
            public_key: key.verifying_key().to_bytes(),
        }
    }

    fn bundle(tier: Tier, claim: &str) -> (Bundle, SigningKey) {
        let key = SigningKey::generate(&mut rand::rngs::OsRng);
        let doc = signed("offer", &key, serde_json::json!({"amount": 1_350_000, "closing_days": 30}));
        let b = Bundle {
            session_id: [7; 32],
            message_digest: message_digest("I have an offer above $1.3M"),
            now: "2025-04-20".into(),
            tier,
            claim: claim.into(),
            documents: vec![doc],
        };
        (b, key)
    }

    #[test]
    fn honest_bundle_in_every_tier() {
        let claims = [
            (Tier::Form, r#"{"combine":"all","conditions":[{"field":"offer.amount","op":">","value":1300000}]}"#),
            (Tier::Formula, "offer.amount > 1.3e6 && offer._signer == 'Alice Realty'"),
            (Tier::Script, "offer.amount > 1300000 && days_between(offer._signed_at, now) <= 30"),
        ];
        for (tier, claim) in claims {
            let pv = check_bundle(&bundle(tier, claim).0).unwrap();
            assert!(pv.result, "{tier:?}");
            assert_eq!(pv.documents[0].signer, "Alice Realty");
        }
    }

    #[test]
    fn false_claim_is_false_not_an_error() {
        let pv = check_bundle(&bundle(Tier::Formula, "offer.amount > 2e6").0).unwrap();
        assert!(!pv.result);
    }

    #[test]
    fn tampered_or_unsigned_documents_are_rejected() {
        let (mut b, _) = bundle(Tier::Formula, "offer.amount > 1.3e6");
        let text = String::from_utf8(b.documents[0].payload.clone()).unwrap();
        b.documents[0].payload = text.replace("1350000", "1950000").into_bytes();
        assert!(check_bundle(&b).unwrap_err().0.contains("bad signature"));

        let (mut b, _) = bundle(Tier::Formula, "offer.amount > 1.3e6");
        b.documents[0].public_key = SigningKey::generate(&mut rand::rngs::OsRng).verifying_key().to_bytes();
        assert!(check_bundle(&b).is_err(), "signature from a different key");
    }

    #[test]
    fn form_claims_are_canonicalized() {
        let messy = r#"{ "conditions": [ {"value": 1300000, "op": ">", "field": "offer.amount"} ], "combine": "all" }"#;
        let pv = check_bundle(&bundle(Tier::Form, messy).0).unwrap();
        assert_eq!(pv.claim, r#"{"combine":"all","conditions":[{"field":"offer.amount","op":">","value":1300000}]}"#);
    }

    #[test]
    fn labels_are_validated() {
        let (mut b, _) = bundle(Tier::Formula, "true");
        b.documents[0].label = "now".into();
        assert!(check_bundle(&b).is_err());
    }
}

use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::Argon2;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use rand::rngs::OsRng;
use rand::RngCore;
use sha2::{Digest, Sha256};

use crate::error::{AppError, AppResult};

pub fn hash_password(password: &str) -> AppResult<String> {
    let salt = SaltString::generate(&mut OsRng);
    let hash = Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map_err(|e| AppError::Internal(format!("argon2 hash: {e}")))?
        .to_string();
    Ok(hash)
}

pub fn verify_password(password: &str, password_hash: &str) -> AppResult<bool> {
    let parsed = PasswordHash::new(password_hash)
        .map_err(|e| AppError::Internal(format!("bad password hash: {e}")))?;
    Ok(Argon2::default()
        .verify_password(password.as_bytes(), &parsed)
        .is_ok())
}

pub fn hash_code(code: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(code.as_bytes());
    hex::encode(hasher.finalize())
}

pub fn random_digits(len: usize) -> String {
    let mut rng = OsRng;
    (0..len)
        .map(|_| char::from(b'0' + (rng.next_u32() % 10) as u8))
        .collect()
}

pub fn random_token() -> String {
    let mut buf = [0u8; 32];
    OsRng.fill_bytes(&mut buf);
    URL_SAFE_NO_PAD.encode(buf)
}

pub fn random_challenge() -> String {
    let mut buf = [0u8; 32];
    OsRng.fill_bytes(&mut buf);
    URL_SAFE_NO_PAD.encode(buf)
}

pub fn b64_encode(bytes: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(bytes)
}

pub fn b64_decode(s: &str) -> AppResult<Vec<u8>> {
    URL_SAFE_NO_PAD
        .decode(s)
        .map_err(|e| AppError::BadRequest(format!("invalid base64: {e}")))
}

/// Demo/laptop passkey: generate Ed25519 keypair (private stays on client; server stores public).
pub fn generate_passkey_keypair() -> (String, String) {
    let signing = SigningKey::generate(&mut OsRng);
    let verifying = signing.verifying_key();
    (
        b64_encode(signing.to_bytes().as_ref()),
        b64_encode(verifying.as_bytes()),
    )
}

pub fn sign_challenge(private_key_b64: &str, challenge_b64: &str) -> AppResult<String> {
    let sk_bytes = b64_decode(private_key_b64)?;
    let sk_arr: [u8; 32] = sk_bytes
        .as_slice()
        .try_into()
        .map_err(|_| AppError::BadRequest("passkey private key must be 32 bytes".into()))?;
    let signing = SigningKey::from_bytes(&sk_arr);
    let challenge = b64_decode(challenge_b64)?;
    let sig = signing.sign(&challenge);
    Ok(b64_encode(&sig.to_bytes()))
}

pub fn verify_passkey_signature(
    public_key_b64: &str,
    challenge_b64: &str,
    signature_b64: &str,
) -> AppResult<bool> {
    let pk_bytes = b64_decode(public_key_b64)?;
    let pk_arr: [u8; 32] = pk_bytes
        .as_slice()
        .try_into()
        .map_err(|_| AppError::BadRequest("passkey public key must be 32 bytes".into()))?;
    let verifying = VerifyingKey::from_bytes(&pk_arr)
        .map_err(|e| AppError::BadRequest(format!("bad public key: {e}")))?;
    let challenge = b64_decode(challenge_b64)?;
    let sig_bytes = b64_decode(signature_b64)?;
    let sig_arr: [u8; 64] = sig_bytes
        .as_slice()
        .try_into()
        .map_err(|_| AppError::BadRequest("signature must be 64 bytes".into()))?;
    let sig = Signature::from_bytes(&sig_arr);
    Ok(verifying.verify(&challenge, &sig).is_ok())
}

/// Simple XOR+SHA derived seal for local vault secrets (master from env or generated file).
pub fn seal_secret(master: &[u8], plaintext: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(master);
    hasher.update(b"openvault-vault-v1");
    let key = hasher.finalize();
    let pt = plaintext.as_bytes();
    let mut out = Vec::with_capacity(pt.len());
    for (i, b) in pt.iter().enumerate() {
        out.push(b ^ key[i % key.len()]);
    }
    b64_encode(&out)
}

pub fn unseal_secret(master: &[u8], ciphertext_b64: &str) -> AppResult<String> {
    let ct = b64_decode(ciphertext_b64)?;
    let mut hasher = Sha256::new();
    hasher.update(master);
    hasher.update(b"openvault-vault-v1");
    let key = hasher.finalize();
    let mut out = Vec::with_capacity(ct.len());
    for (i, b) in ct.iter().enumerate() {
        out.push(b ^ key[i % key.len()]);
    }
    String::from_utf8(out).map_err(|e| AppError::Internal(format!("utf8: {e}")))
}

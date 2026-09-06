---
status: accepted
date: 2026-09-05
decision-makers: founder
---

# DR-0014 - Passkeys unseal the local vault (not autofill)

## Context and Problem Statement

F20 parked passkeys. The founder asked to unpark them as Windows Hello / Face ID
/ fingerprint, with optional iPhone verification. F18 + PRD §3 still forbid a
login agent and OS autofill.

## Decision Outcome

- Passkeys are a **second wrap** of the live Fernet master key under the
  authenticator PRF secret (`OPENVAULT_HOME/webauthn_unlock.json`).
- On-disk wrap stays `passphrase-scrypt` / DPAPI. Losing the passkey does not
  brick the vault.
- Platform authenticator first (Windows Hello, Touch ID, macOS password +
  biometrics). Optional hybrid for iPhone QR / nearby.
- `userVerification: required`. If PRF is missing, refuse honestly and keep
  passphrase.
- `rpId` is `127.0.0.1`. Origins: `http://127.0.0.1:3010` and `:5000`.
- Loopback-only APIs under `/api/vault/webauthn/*`. Register requires an
  unsealed vault. Unseal-with-passkey is allowed while sealed.
- Not iCloud/Chrome autofill. Not a site-login agent. Agents still use
  `openvault secret get` (keys/passwords, never cards) after the vault is open.

## Consequences

- Good: Face ID / fingerprint / iPhone can unseal without typing the passphrase
  every time.
- Bad: authenticators without PRF cannot register; passphrase remains required
  as backup.
- Neutral: no live Hello in CI — tests use a crafted ES256 assertion + PRF wrap.

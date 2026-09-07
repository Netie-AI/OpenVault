---
status: proposed
date: 2026-09-07
decision-makers: founder
---

# DR-0014 - Publish JWKS pin kids without a public mint

## Context and Problem Statement

Cortex prove (`http://34.30.222.22:8010`) cannot bind to OpenVault prove
(`http://35.253.229.206:8080`). Live probe 2026-09-07:

- `GET /.well-known/jwks.json` -> 404
- `GET /keys/jwks` -> `{"keys":[]}` (no kids)
- `GET /keys/root` already had `kid=root-...`
- `GET /api/keys` -> `{"keys":[]}` (empty *vault*, not JWKS)
- `POST /keys/services` -> 403 loopback-only (correct)

`TrustStore.jwks()` published only intermediates. Intermediates require a
loopback mint. Cortex is remote, so it could never obtain a kid.

## Considered Options

- Open public mint of `/keys/services` on the prove host
- Leave JWKS empty until DMS mints on the VM
- Publish the trust-root *public* half in JWKS as `netie_verify_only` pin
  material, and serve it at `/.well-known/jwks.json`

## Decision Outcome

Chosen option: publish the root public JWK in JWKS with `netie_verify_only: true`
and `netie_role: trust-root`, and serve the same document at
`GET /.well-known/jwks.json` and `GET /keys/jwks`. Mint / intermediate issue
stay loopback-only. Manifest signatures remain `int-*` only.

`/api/keys` stays the provider-credential vault. An empty vault is not a missing
JWKS.

`LIVE_KEY_ID` is an issued key *id* (12 hex). The `ov_` token lives in Secret
Manager `openvault-dms-writer-token` and is never written into git, PRs, logs,
or chat.

On merge, this record may flip to `accepted`. Decision Agent does not merge.

## Consequences

- Good: Cortex can pin a kid before any service mint.
- Good: public mint stays closed.
- Neutral: consumers MUST refuse to accept `netie_verify_only` kids as manifest
  signers. The chain signature still binds intermediates to the pinned root.
- Cite: [dms#116](https://github.com/Netie-AI/dms/issues/116) is verify-only
  remount *after* this bind exists. This repo does not write DMS product code.

## Confirmation

`OpenMW/tests/test_jwks_bind.py` plus `test_trust_root.py`
(`test_root_is_published_in_jwks_as_verify_only_pin`,
`test_well_known_jwks_has_kids_without_mint`,
`test_remote_caller_can_read_jwks_but_cannot_mint`).

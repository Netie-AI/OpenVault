---
status: accepted
date: 2026-09-06
accepted: 2026-09-07
decision-makers: founder
---

# DR-0015 - What belongs in the vault, and what only looks like it does

## Context and Problem Statement

Founder, 2026-09-06, deciding a question this estate had already answered the
other way:

> "I think it is like PII so it can be secrets right somehow, memory is more
> like daily used, these are like IC to be used I think can be inside OpenVault,
> not only api keys in openvault, password, pii, valuable stuff all need
> protected, and use Microsoft like hello and macbook need phone passkey"

The recorded decision it collides with is in Space's `STATUS.md` and repeated in
`CortexOS/integrations/openvault_client.py`: **"Cortex `/api/memory` for facts;
OpenVault for keys only."** `PRODUCT_ROLES.md` lock 5 says "no second key
vault", and lock 6 says memory stays in Cortex.

Read literally, "keys only" puts an IC number in Cortex memory next to "prefers
aisle seats". The founder's instinct is that this is wrong, and the instinct is
right - but "PII belongs in the vault" is too broad in the other direction, and
taken literally it turns the vault into a profile store that must be unsealed to
type a home address.

This record draws the line, because the line is the decision.

## Decision Drivers

- `PRODUCT_ROLES.md` lock 5 - no second key vault - and lock 6 - memory stays in
  Cortex. Neither is in question; what "key" means is.
- KB `F-0030` - one vault, one orchestrator.
- Form-fill has to work without a human. Every field that needs the vault
  unsealed is a field an unattended fill cannot complete.
- KB `A-0009` (this session) - loopback plus a client-supplied header is not
  authority. Anything reachable from the vault is reachable by any process
  running as the user until app-grant pairing binds it.

## Decision - the test is reversibility, not sensitivity

**A value belongs in the vault when disclosure cannot be undone.**

You can change a password, rotate an API key, cancel a card. You cannot rotate
an IC number, a passport number, or a tax id. That asymmetry - not "how personal
does it feel" - is what makes something vault material.

| Value | Home | Why |
|---|---|---|
| Passwords, API keys, card PANs | OpenVault | already the case; rotatable but high-value |
| **IC / NRIC, passport, driving licence, tax id** | **OpenVault (new)** | disclosure is permanent; you cannot reissue your identity |
| **2FA backup / recovery codes** | **OpenVault (new)** | a spare key to every account that has one |
| Name, address, phone, email, DOB, nationality | profile / Cortex memory | a form-fill types these constantly; sealing them means unsealing the vault to fill in an address |
| Preferences, history, daily facts | Cortex memory | "memory is more like daily used" - exactly right |

So "keys only" was too narrow and "all PII" is too broad. The vault takes the
irreversible identifiers; the profile keeps the fields that get typed all day.
Date of birth is the closest call and stays in the profile: it appears on nearly
every booking form, and it is not secret to anyone holding the document it is
printed on.

**This supersedes the "keys only" half of the recorded decision.** Lock 5 is
untouched - there is still exactly one vault, and this is it. Lock 6 is
untouched - memory stays in Cortex, and none of the rows above are memory.

## What is already built

On `feat/vault-codes-identity`, with 22 tests:

- `POST /api/secrets/identity` - `doc_type` and a last-four mask stay clear so a
  chooser works without unsealing; the number is sealed. Name, address and DOB
  are **refused by the schema**, so the boundary above cannot erode by accident.
- `POST /api/secrets/recovery-codes` - a code set sealed as one blob, with the
  remaining count in a clear column so listing never decrypts.
- `POST /api/secrets/{id}/consume-code` - marks and returns one code in a single
  transaction. `GET /api/secrets/{id}/reveal` now **refuses** a code set: one
  payload there is every code, and reading them without spending one
  desynchronises the count.

The founder's Gmail app password goes in `POST /api/secrets/passwords`, which
already existed. The backup codes now have somewhere to go.

## Hello / passkey unlock - already shipped, needs pointing at

"use Microsoft like hello and macbook need phone passkey" is mostly built and
not surfaced: `/api/vault/webauthn/register/begin|finish` and
`/api/vault/webauthn/unseal/begin|finish` exist, and `DR-0014` accepted passkeys
as a vault-unseal mechanism. Windows Hello is a platform authenticator and works
through the same WebAuthn path.

Two things are genuinely missing, and both are UI rather than crypto: nothing
walks a first-time user through registering an authenticator, and nothing tells
them the vault is sealed when a fill fails. `DR-0014`'s fence still holds - this
is unsealing the vault, not autofilling the web.

## Open - the Rust console, assessed 2026-09-06

`OpenMW/rust/openvault-console` is **2034** lines of source (11 `.rs` files) with
`accounts(username, netie_email, gmail, phone, email_verified, phone_verified)`, a
`verify_codes` table of hashed six-digit codes with a 15-minute expiry, passkeys
and sessions, plus its own `vault_secrets` table, in `rust-auth.db`. Mesh
advertises it: `mesh/local_mesh.py` sets `OPENVAULT_RUST_URL` and points `auth_ui`
at `http://127.0.0.1:5055/#auth`; `tests/test_contract.py` pins the port.

**The previous claim that it had never been built is false.** On this machine
`cargo 1.97.1` is installed, `target/release/openvault-console.exe` exists
(5.1 MB, gitignored), and `cargo test` in that crate reports **2 passed**
(`full_register_verify_passkey_vault_openship`, `providers_and_health`),
0 failed, about 47s including compile.

So the crate is a working sandbox, not a missing binary. The R-0011 lie is
different: the mesh still prints `auth_ui` at `:5055` when the process is not
running. Probe status can be `unknown`; the URL is handed out anyway.

What it duplicates, which lock 5 forbids:

- Identity: Python `AccountStore` / `accounts.db` vs Rust `accounts` in `rust-auth.db`
- Secrets: Python vault vs Rust `vault_secrets` plus a server-minted
  `demo_private_key` on `POST /api/auth/passkey/register/begin` (`api.rs`). That is
  not WebAuthn. DR-0014 already shipped real passkey unseal in the Python app.

The founder's steer "I need rust for efficiency" does not point at promoting this
crate. Custody waits on a human prompt, not on CPU. Space's `netie-pdf-detect`
is the existing Rust hot-loop. Growing a second accounts table and a second
secrets table to get a faster login page is the wrong half of the repo.

**Accepted 2026-09-07:** keep the crate optional. Do not move Python `accounts`
into it. Do not add phone-verification against either store. The mesh may keep a
`rust_console` URL; the connect-pack must name `status` and must not present
`#auth` as a live UI when the probe is not online. Phone work in Pointer
`DR-0004` stays blocked on this call, not on `cargo test`. DR-0014 Python
WebAuthn unseal remains the real passkey path.

## Consequences

- The vault stops being "keys only" and becomes "the irreversible things",
  which is a larger surface and a clearer rule.
- Unattended form-fill keeps working, because the fields it types every day did
  not move.
- Everything added here inherits `A-0009`: until app-grant pairing binds a
  request to a process, loopback plus a header is not authority, and an IC
  number is now behind that same gate. Pairing is more urgent after this record,
  not less.

## Confirmation

- `OpenMW/tests/test_recovery_codes_identity.py` - a code is never issued twice;
  reveal refuses a code set; identity numbers are sealed on disk and absent from
  listings; the new write routes are loopback-only; the audit records counts and
  document types, never a code or a number.
- `OpenMW/tests/test_local_mesh.py` - connect-pack `auth_ui` is null when rust
  is offline; `register_passkey` does not hand out `/#auth` for a down process.
- Re-run `pytest tests/test_recovery_codes_identity.py tests/test_secrets_custody.py
  tests/test_secret_reveal_gate.py tests/test_local_mesh.py tests/test_contract.py`
  after accepting.
- Rust console sandbox (does not decide this record): `cd OpenMW/rust/openvault-console
  && cargo test` - 2 passed 2026-09-06. Do not read that as identity SoT.

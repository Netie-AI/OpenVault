---
status: proposed
date: 2026-09-06
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

## Open - the Rust console, and it is the first question

`OpenMW/rust/openvault-console` is 1813 lines with `accounts(username,
netie_email, gmail, phone, email_verified, phone_verified)`, a `verify_codes`
table of hashed six-digit codes with a 15-minute expiry, passkeys and sessions,
in its own `rust-auth.db`. It is **declared**: `mesh/local_mesh.py` advertises
`rust_console: 5055`, sets `OPENVAULT_RUST_URL`, and points `auth_ui` at
`http://127.0.0.1:5055/#auth`; `tests/test_contract.py` pins the port.

It has never been built - there is no binary. So the mesh advertises an auth UI
that nothing serves, which is the `R-0011` shape: a capability that exists in
the contract and not in fact.

The founder's steer is "I need rust for efficiency", which points at promoting
it. Against that: it duplicates `accounts` (the Python app has `AccountStore` and
`/api/accounts`), and duplicated identity is precisely the second store lock 5
forbids. Efficiency is also not the constraint here - custody waits on a human
approving a prompt, not on CPU. Where Rust does earn its place in this estate is
hot loops, and Space's `netie-pdf-detect` already is Rust.

**Recommendation:** build it and run it, then move `accounts` to it wholesale so
there is one accounts table rather than two, and keep secret custody in the
Python app. Do not add phone-verification anywhere until that merge is decided -
building it against the Python app while the Rust console owns `verify_codes`
creates a third store rather than resolving the second.

Not decided here. It needs the founder, and it is the prerequisite for the phone
work in Pointer `DR-0004`.

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
- Re-run `pytest tests/test_recovery_codes_identity.py tests/test_secrets_custody.py
  tests/test_secret_reveal_gate.py` before accepting.

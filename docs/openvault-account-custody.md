# OpenVault account custody

Operators and tenants keep signup, verification, private Netie email, passkeys, and API secrets under the platform.

## Default signup (preferred)

| Step | What happens |
|------|----------------|
| 1. Username + password | Username is the account key. Password stored **argon2id** only. |
| 2. Netie email | Auto-assign **`username@netie.ai`**. |
| 3. Gmail verify | Gmail used **for verification only** (not the primary mailbox). |
| 4. Phone verify | Phone stored; account becomes `active`. |
| 5. Laptop passkey | Ed25519 passkey registered on device — **default login**. |
| 6. Later visits | Passkey sign-in (no password). Password remains hashed backup. |

Also accepted: external email / Google-linked signup on the Python console for operator-assisted onboarding.

Private relay addresses (`*@relay.netie.ai`) remain available on the Python operator console.

## Where to test

**Rust UI (full auth + vault + OmniRoute + OpenShip):**

```bash
cd OpenMW/rust/openvault-console && cargo run --release
# http://127.0.0.1:5055/
```

See [`openvault-rust-console.md`](openvault-rust-console.md).

**Python operator console** (`openmw console` :5000): Accounts tab for create/save keys, incident kill/replace.

## Operator powers (Python API)

For any account (`GET /api/accounts/{id}`):

- Create and encrypt-store provider keys (`POST /api/accounts/{id}/keys`)
- Precheck / fallback using the shared vault
- Revoke (`POST /api/keys/{id}/revoke`) or rotate (`POST /api/keys/{id}/rotate`)
- **Incident kill**: `POST /api/accounts/{id}/incident`

## Rust auth APIs

| Route | Purpose |
|-------|---------|
| `POST /api/auth/register` | Username + password → `username@netie.ai` |
| `POST /api/auth/verify/gmail/start\|confirm` | Gmail verification channel |
| `POST /api/auth/verify/phone/start\|confirm` | Phone verification |
| `POST /api/auth/passkey/register/*` | Laptop passkey enrollment |
| `POST /api/auth/passkey/login/*` | Default login |
| `POST /api/auth/login/password` | Argon2 backup login |
| `GET/POST /api/vault/secrets` | Password / API vault |
| `GET /api/providers` | OmniRoute catalog |
| `POST /api/openship/plan` | OpenShip plan (+ `execute`) |

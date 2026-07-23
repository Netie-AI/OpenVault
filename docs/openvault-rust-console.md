# OpenVault Rust console — auth, passkeys, OmniRoute, OpenShip, vaults

Rust binary at `OpenMW/rust/openvault-console` for efficiency, manageability, and security of the custody path.

## Default signup

1. **Username + password** — username is the account key; password stored **argon2id** only.
2. Auto-assign **`username@netie.ai`**.
3. **Gmail verification only** (channel, not the primary mailbox).
4. **Phone verification** — store phone + gmail + username.
5. Register a **passkey on the laptop** — becomes default login.
6. Later visits: invoke passkey sign-in (no password). Password remains hashed backup.

## Run / test UI

```bash
cd OpenMW/rust/openvault-console
cargo run --release
# → http://127.0.0.1:5055/
```

Tabs: Auth/Passkeys · Accounts · Password Vault · OmniRoute · OpenShip.

Demo verification codes are returned in API responses when `OPENVAULT_DEMO=true` (default).

## Relation to Python OpenVault

| Surface | Python (`openmw console` :5000) | Rust (`cargo run` :5055) |
|---------|----------------------------------|---------------------------|
| Key vault + precheck + Cortex | Yes | Password/API vault (sealed) |
| Account custody | Yes (operator) | Yes (self-serve + passkeys) |
| OmniRoute catalog | Yes | Yes (curated) |
| OpenShip gates | Yes | Yes (plan + simulate) |
| Passkey-default login | — | **Yes** |

Use the Rust console to exercise the full auth → passkey → vault → OpenShip path locally.

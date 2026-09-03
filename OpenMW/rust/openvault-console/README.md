# OpenVault Rust Console

Secure local console written in Rust: username/password signup → Gmail verify → phone verify → `username@netie.ai` assignment → **passkey-default laptop login**, plus OmniRoute catalog, FreeBuild plan/execute, and encrypted password/API vault.

## Run the UI

```bash
cd OpenMW/rust/openvault-console
cargo run --release
```

Open **http://127.0.0.1:5055/**

Env:

| Variable | Default | Meaning |
|----------|---------|---------|
| `OPENVAULT_HOME` | `~/.openvault` | SQLite + vault master |
| `OPENVAULT_PORT` | `5055` | Bind port |
| `OPENVAULT_HOST` | `127.0.0.1` | Bind host |
| `OPENVAULT_DEMO` | `true` | Return verification codes in API for local testing |

## Auth flow

1. Register with **username + password** (password stored as **argon2id** only).
2. Assigned **`username@netie.ai`** automatically (platform mailbox).
3. Verify with a **Gmail** address (verification channel only — not the account email).
4. Verify **phone**.
5. Register a **passkey on this laptop** (Ed25519; private key stays in browser storage for the demo UI).
6. Next visits: **Sign in with passkey** (no password). Password remains a hashed backup.

## Features in the UI

- Auth / Passkeys
- Accounts custody list
- Password / API vault (store, reveal, revoke, incident kill)
- OmniRoute provider catalog + fallback roles
- FreeBuild full gate plan + simulate execute

## Tests

```bash
cargo test
```

# OpenVault secrets custody — keys, passwords, payment cards

**Date:** 2026-07-27
**Scope:** `OpenMW/openmw/openvault/app.py` `/api/keys*` + `/api/secrets*`,
`vault/store.py`, `vault/secrets.py`, `vault/crypto.py`.
**Contract:** [`PRODUCT_ROLES.md`](../PRODUCT_ROLES.md) ownership lock 1 — keys SoT is
OpenVault. Netie Space and AirGPT are thin clients with offline caches, never
second vaults.
**Companion:** [`BACKEND_HONESTY_AUDIT.md`](BACKEND_HONESTY_AUDIT.md) §1, which
this file partially closes and partially updates.

---

## 1. Audit of `/api/keys*` — what the gates actually covered

There is still no authentication on any route in this app. The reveal route
compensated with three cheap controls: loopback, a custom intent header, and an
audit line. **Every other custody route had none of them.**

| Route | Before | After |
|-------|--------|-------|
| `GET /api/keys` | open, masked values | unchanged — masks only, no decrypt on this path |
| `POST /api/keys` | **open** | loopback + audit |
| `PATCH /api/keys/{id}` | **open** (can replace a secret) | loopback + audit (`secret_replaced`) |
| `DELETE /api/keys/{id}` | **open** (irreversible) | loopback + audit |
| `POST /api/keys/{id}/revoke` | **open** | loopback + audit |
| `POST /api/keys/{id}/rotate` | **open** | loopback + audit |
| `POST /api/accounts/{id}/keys` | **open** | loopback + audit |
| `POST /api/accounts/{id}/incident` | **open** (kills every key on an account) | loopback + audit |
| `POST /api/keyvault/upsert` | **open** (same write, different door) | loopback + audit |
| `POST /api/vault/ingest-env` | **open** | loopback + audit (non-dry-run only) |
| `GET /api/keys/{id}/secret` | loopback + intent + audit | unchanged |

### Why mutations get loopback + audit but not the intent header

The header exists to defeat drive-by *reads* from a page the user has open: a
browser cannot attach a custom header cross-origin without surviving a
preflight. Mutations are already largely protected from that specific attack —
they take a JSON body, and `application/json` is not a CORS-simple content type,
so a form-based CSRF post cannot reach them either. Meanwhile the shipped
consoles (`webui/index.html`, the Next app) issue `DELETE` and `PATCH` without
the header, and a gate that breaks the only UI gets deleted within a week.

Loopback is the control that was genuinely missing. A remote `DELETE
/api/keys/{id}` destroys a credential the user may have no other copy of; a
remote rotate silently swaps the secret every other surface is about to fetch.
The bind address defaults to `127.0.0.1`, but a default is a preference, not a
control.

### Gaps found and fixed alongside

- **`webui/index.html` "copy secret" was broken.** It fetched
  `/api/keys/{id}/secret` with no header, got a 428, and copied `""` to the
  clipboard — silently, since it never checked `res.ok`. Now sends the header
  and surfaces the status. This is the failure mode a gate without a matching
  client always produces, and it is worth checking for on every new gate.
- **Test clients were not loopback.** `TestClient`'s default peer host is the
  literal string `testclient`, so the new guard correctly rejected it. The
  fixtures in `test_openvault.py`, `test_accounts_custody.py`, and
  `test_env_ingest.py` now pass `client=("127.0.0.1", 5555)`. The guard was not
  weakened to accommodate them — adding `"testclient"` to `_LOOPBACK_HOSTS`
  would have shipped a bypass string in production code.

### Gaps found and NOT fixed (deliberate, tracked)

- **`KeyVault.list_keys` decrypts every secret to compute a mask.** `GET
  /api/keys` therefore holds every plaintext credential in process memory on
  every list — and Netie polls this on startup. Fixing it means storing the mask
  as a column and migrating existing rows. Worth doing; larger than this change.
- **`crypto.mask_secret` shows the first 4 characters** (`sk-a…********`), never
  the last 4. Netie's Setup UI shows `first4…last4`, which it derives from the
  plaintext it already holds in `user.env`, not from this API. Nothing to change
  in OpenVault; noted so the two masks are not confused for the same thing.
- **Everything in `BACKEND_HONESTY_AUDIT.md` §1 items 2, 3, 5, 6, 7** — no KDF
  on the master key, no unseal/re-lock state, GitHub PAT outside the vault,
  precheck egress, precheck treating 404 as `ok`. Item 4 ("no access audit for
  secrets") is now closed for keys, passwords, and cards.

---

## 2. Password + payment-card secrets (`vault/secrets.py`)

New table `secrets` in the **same** `keys.db`, sealed with the **same** master
key. One master key, one thing to back up, one thing to lose — the "no second
key vault" lock is about custody, not table count.

Separate table rather than a `kind` column on `keys` because `keys` is
load-bearing for routing (provider, role, priority, precheck, fallback
breakers). A card has none of those, and every consumer of `/api/keys` —
AirGPT, FreeIDE, Netie — would have had to learn to skip rows it had never seen
before. Nothing in fallback, precheck, or the proxy can reach a card.

### Kinds

| Kind | Sealed payload | Clear metadata |
|------|----------------|----------------|
| `api_key` | existing `keys` table, unchanged | label, provider, role, base_url, priority |
| `password` | the password | label, username, url |
| `payment_card` | the PAN, and only the PAN | label, brand, last4, exp_month, exp_year, cardholder |

Lifecycle mirrors keys: `active` → `revoked` \| `rotated` \| `compromised`, with
`replaced_by` chaining a rotated record to its replacement.

### PCI posture

- **CVV/CVC is never accepted or stored**, encrypted or otherwise. PCI DSS
  forbids retaining it after authorization, and OpenVault does not authorize
  anything, so there is no window in which holding it is legitimate. `POST
  /api/secrets/cards` with a `cvv` field returns **400 with the reason**, rather
  than dropping the field silently — a caller that believes it stored a CVV will
  build a checkout flow on a field that is not there.
- **`brand` / `last4` / expiry are stored in the clear on purpose.** They are
  what a chooser UI needs, and they cannot be used to charge a card.
- **No public record type carries plaintext.** `SecretRecord` holds a mask only,
  so `asdict()` into a JSON response, a log line, or a traceback cannot leak a
  PAN by accident. Plaintext leaves the module through exactly one function,
  `SecretStore.reveal`.
- **PAN is Luhn-checked and normalized on write**, brand detected from the IIN.
  A rejected PAN is never echoed back in the error, never logged, never audited.
- **Password masks reveal nothing but a rough length** (`••••••••`), unlike API
  key masks. For an API key `sk-a…` is a useful non-secret discriminator between
  two rows; for a password the first four characters are a meaningful head start
  on guessing the rest.
- **Payload changes are `rotate`, not `update`.** `PATCH /api/secrets/{id}`
  touches metadata only. Replacing a PAN in place would erase that the old one
  ever existed, and "which card was on file in March" is exactly the question an
  audit has to answer. Same reason `revoke` keeps the ciphertext until an
  explicit `DELETE`.

### Routes

| Route | Gate |
|-------|------|
| `GET /api/secrets?kind=&account_id=` | open, masks only — no decryption on this path |
| `POST /api/secrets/passwords` | loopback + audit |
| `POST /api/secrets/cards` | loopback + audit (`brand`, `last4` only) |
| `PATCH /api/secrets/{id}` | loopback + audit |
| `DELETE /api/secrets/{id}` | loopback + audit |
| `POST /api/secrets/{id}/revoke` | loopback + audit |
| `POST /api/secrets/{id}/rotate` | loopback + audit |
| `GET /api/secrets/{id}/reveal` | **loopback + `X-OpenVault-Reveal: intentional` + audit** |

A reveal also stamps `last_revealed_at` on the record itself, so deleting
`secret_audit.jsonl` is not a clean slate.

### Audit trail

One file, `~/.openvault/secret_audit.jsonl`, one JSON object per line. Events:
`secret_reveal`, `key_create`, `key_update`, `key_delete`, `key_revoke`,
`key_rotate`, `account_incident_kill`, `keyvault_upsert`, `env_ingest`,
`password_create`, `card_create`, `secret_update`, `secret_delete`,
`secret_revoke`, `secret_rotate`.

Writes are best-effort by design: a failed audit write must not deny the user an
operation they are entitled to, but it logs at `error`. The audit file is not a
second place a secret is allowed to exist — card events record `brand` and
`last4` only, and tests assert the PAN and password never appear in the file.

---

## 3. Netie Space retrieve contract

Netie is a thin client. `OpenVaultKeySync.SyncApiKeysAsync` does exactly three
things, all pinned by `tests/test_netie_thin_client_sync.py`:

1. `GET /api/healthz` — soft-fail to local keys when the vault is down.
2. `GET /api/keys` — keep only `enabled` **and** `lifecycle == "active"`.
3. `GET /api/keys/{id}/secret` with the intent header, per surviving key.

**Rotation is why that filter matters.** Rotating mints a new row and disables
the old one. If the filter and the lifecycle values ever drift apart, Netie
silently keeps writing a dead key into `user.env` and every AI call fails with
an auth error that looks like a Netie bug. `test_rotate_then_resync_yields_only_
the_new_secret` pins the whole loop.

**Cards and passwords never reach Netie.** They are not in `/api/keys`, so the
list Netie iterates cannot hand it a PAN. Structurally,
`EnvLoader.SaveUserApiKeys` also takes four fixed provider slots — there is no
parameter a card could occupy. When Netie needs card autofill later, the shape
is a deep-link into the OpenVault UI or a per-use `GET /api/secrets/{id}/reveal`
that is never written to disk. Not `user.env`.

---

## 4. Threat notes

### 4.1 The `127.0.0.1:5000` trust boundary

Loopback HTTP, no TLS, no auth. What it does and does not buy:

- **Does:** keeps the vault off the LAN. Combined with the intent header it
  stops a web page the user has open from reading keys, and it stops a device on
  the same network from deleting them.
- **Does not:** separate processes. **Any process running as the user can read
  every secret** — it can call the API with the header, or just read
  `master.key` and `keys.db` directly. Adding cards does not change this
  boundary; it changes what is behind it. That is the honest reason cards must
  not also be copied into a second location.
- **Does not:** survive a port-forward or a `--host 0.0.0.0` override. The
  loopback guard is now enforced in code rather than left to the bind default,
  so a misconfigured bind fails closed on custody routes instead of exposing
  them.

Next step if this needs to be real: a Windows named pipe or a Unix socket
instead of a TCP port, which gets peer-process identity for free. TLS on
loopback would encrypt a channel that is not the weak part.

### 4.2 Netie's offline cache is plaintext, not DPAPI

The handoff assumed "DPAPI cache = AI keys only". Half of that is right and the
important half is backwards:

| Netie store | Protection |
|-------------|------------|
| `%LOCALAPPDATA%\NetieSpace\user.env` (AI provider keys) | **plaintext lines on disk** |
| `PasswordVaultService` (offline password fallback) | DPAPI, `CurrentUser` |
| `LicenseStore` entitlement + session | DPAPI, `CurrentUser` |
| `UsageFootprintStore` | DPAPI, `CurrentUser` |

So the one cache that holds live cloud-provider API keys is the one that is not
DPAPI-wrapped. This is a Netie-side fix (`EnvLoader` should `ProtectedData.
Protect` the file the way `LicenseStore` already does), listed here because it
is the weakest link in the custody chain this document describes and it is not
visible from inside OpenVault. Until it lands, treat `user.env` as equivalent to
having the keys in a text file — because it is one.

### 4.3 Cards stay in OpenVault

No Netie surface stores card data, and no OpenVault surface exports it. The
enforcement is structural, not conventional: cards are not in `/api/keys`,
`SecretRecord` has no plaintext field, and `SaveUserApiKeys` has four fixed
provider parameters. Any future card autofill in Netie must reveal per use and
hold the value in memory only.

### 4.4 Leave-machine gate

Cards and passwords are custody-only. They are not routable credentials, so they
never enter the proxy, fallback, or precheck paths, and nothing ships them
upstream. Any future flow that would move a card off the machine goes through
the OpenVault deploy/leave-machine gate (`ship/gate.py`), same as any other
secret — PRODUCT_ROLES ownership lock 3.

---

## 5. Tests

| File | Pins |
|------|------|
| `tests/test_secrets_custody.py` (21) | PAN/password never in the DB file, never in a list response, never in the audit log; reveal denied without the header (428) and off loopback (403); CVV refused; Luhn and expiry validation; rejected PAN not echoed; brand detection; mutations loopback-only; rotate chains old→new; metadata patch cannot replace a payload |
| `tests/test_netie_thin_client_sync.py` (6) | Netie's exact sync loop; rotate → re-sync yields only the new secret; revoked stops syncing; masks never carry the full secret; cards/passwords invisible to the key sync; missing header fails closed |
| `tests/test_secret_reveal_gate.py` (4, pre-existing) | the original three controls on the key reveal route |

Run: `cd OpenMW && .venv/Scripts/python -m pytest tests/test_secrets_custody.py
tests/test_netie_thin_client_sync.py tests/test_secret_reveal_gate.py -q`

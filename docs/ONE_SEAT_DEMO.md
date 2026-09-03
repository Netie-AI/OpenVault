# One-seat demo (auto-safe)

Buyer path: **vault -> FreeRoute refuse -> gated ship allow -> gate deny**, with CLI/file evidence, under 15 minutes. No codebase reading required.

This path is **mocks / simulate only**. It does **not** complete the human live gates (HT1-HT5).

## Run (about 1 minute)

From `OpenMW/` (after `uv sync` once):

```bash
cd OpenMW
uv run python scripts/one_seat_demo.py
```

Or from repo root:

```bash
python apps/cli/openvault_cli.py demo-path
```

Evidence file (default): `OpenMW/.demo_evidence/one_seat.json`.

Stdout prints each step. A green run ends with `[ok] complete` and exit code 0.

### What the script proves

| Step | What you see | Honesty label |
|------|----------------|---------------|
| FreeRoute empty refuse | HTTP 503 `openvault_no_keys` | Auto-safe - no live provider key |
| Vault key | Fake secret vaulted; secret not echoed | Demo secret only |
| FreeRoute sealed refuse | HTTP 403 `openvault_vault_sealed` | Auto-safe — not live paid chat |
| Gate allow + ship | `local_demo` / `simulate`, `mode=simulated`, empty `public_url` | **Not** a live CF/Coolify/Netlify URL |
| Gate deny | Execute 403, `allowed: false`, reasons listed | Refusal visible in evidence |

Optional UI stack (mock health + browser) is separate:

```bash
python apps/cli/openvault_cli.py demo
```

That starts `:5000` + `:3010`. It does **not** replace the scripted path above.

## Stop here - human-only gates (HT1-HT5)

Agents **must not** claim these done. Founder clears them on epic [#18](https://github.com/Netie-AI/OpenVault/issues/18).
Do not buy a second host. Live site is Spaceship (`https://netie.ai`).

This laptop (2026-08-28 probe): repo is `E:\OpenVault`, Cortex is `E:\Cortex`.
`D:\OpenVault` and `D:\Cortex` do **not** exist. `:5000` and `:3010` were up.
Cortex `:8010` was down. `%USERPROFILE%\.netie\spaceship-ftp.env` was missing.

### Shared setup (once, ~8 min)

1. Start OpenVault UI from this repo (not a `D:\` path):

```powershell
$env:OPENVAULT_HOME = "E:\OpenVault\.openvault"
python E:\OpenVault\apps\cli\openvault_cli.py demo
```

Open `http://127.0.0.1:3010/`. If `:3010` hangs, wait ~10s (Next compile) then refresh.

2. Create the Spaceship FTP cache. Do not paste the password in chat.

```powershell
. E:\OpenVault\scripts\windows\Load-SpaceshipFtpEnv.ps1 -CreateTemplate
```

Then: Spaceship Hosting Manager -> cPanel -> FTP Accounts -> copy password
for `ship@netie.ai` into that file's `FTP_PASS=` line. Save. Then:

```powershell
. E:\OpenVault\scripts\windows\Load-SpaceshipFtpEnv.ps1
```

Expect `SPACESHIP_FTP_PASS set: True` and `SPACESHIP_PUBLIC_URL=https://netie.ai/ht1-demo`.
Restart the OpenVault API **in that same PowerShell** so it inherits the env
(`python E:\OpenVault\apps\cli\openvault_cli.py demo` again).

3. Default upload is **`/ht1-demo/`**, not the homepage. `public_html` is the
wrong tree. Live docroot is `/home/<cpanel-user>/netie.ai` - the real account name is in
`SHIP_SPACESHIP.md` (private repo), not here: this repo is public.

Live overwrite still needs you to pass `-AllowPublish` on the loader, or set
`OPENVAULT_SPACESHIP_ALLOW_PUBLISH=1` yourself. Without it, Deploy must refuse
with an empty URL. That refuse is correct, not a bug.

### HT1 - live Spaceship URL under the leave-machine gate (~5 min)

Need: step 2 done, vault unsealed, at least one enabled provider key (Groq is
already vaulted on this laptop).

1. Browser: `http://127.0.0.1:3010/ship`
2. Target stays Spaceship FTP. Project path:
   `E:\OpenVault\scripts\ht1_sample`
3. Preflight must show `ready` and `allow_publish=yes`. If `allow_publish=no`,
   you forgot `-AllowPublish` / the env var. Stop. Do not force a fake URL.
4. Click **Deploy**. Wait.
5. Pass: a real `https://netie.ai/ht1-demo` (or `/ht1-demo/`) opens in a
   browser and shows "OpenVault HT1 sample".
6. Same path, gate deny: Vault page -> **Lock** -> Ship -> Deploy again.
   Pass: HTTP 403 / `allowed: false` / reason `vault is sealed`. Unlock, done.

Fail if: empty `public_url`, simulate mode, or the homepage (`https://netie.ai/`)
got replaced. Fail if you used Cloudflare/Netlify "to make it easier".

### HT2 - live FreeRoute chat with real vaulted keys (~3 min)

Need: a real provider key in Vault (this laptop already has Groq, precheck ok).
This spends quota. You click Send, not an agent.

1. `http://127.0.0.1:3010/vault` - key shows Working, vault **not** sealed.
2. `http://127.0.0.1:3010/proxy` - FreeRoute smoke box - type `hi` - **Send**.
3. Pass: a model reply, not 503 `openvault_no_keys`. Meters request count
   goes up. `priced` stays false (no fake USD).
4. Fallback: Route -> Hops. If a hop is dead, the rotator skips it and the
   next sibling answers. You should see the skip, not a fake success.
5. Budget refuse: in the API shell set `OPENVAULT_MAX_OUTPUT_TOKENS=1`,
   restart `:5000`, Send a long prompt. Pass: a visible refusal, not a billed
   completion. Unset the var after.

Fail if: 503 because the vault is sealed (that is HT3), or a mocked reply.

### HT3 - passphrase unseal / lock on `:3010` (~3 min)

Need: you, a passphrase you will remember. Crypto tests are not this gate.

1. `http://127.0.0.1:3010/vault` - Passwords & cards panel.
2. If wrap is `dpapi-user` and no passphrase: fill **Set passphrase** + Confirm
   -> **Set passphrase**. Then **Lock**.
3. Page must show **Vault is sealed**. Try Add key / reveal -> 403, not a hang.
4. Type passphrase -> **Unseal**. Vault open again.
5. Banner **Plaintext master-key backup is on disk**: enter passphrase ->
   **Retire backup**. Status `plaintext_backup_present` must be false.
   Check: `GET http://127.0.0.1:5000/api/vault/status`.

Fail if: DPAPI still auto-opens after you set a passphrase (restart `:5000`
and confirm it starts sealed). Fail if the backup banner is still up.

### HT4 - Cortex smoke through Engine, no second vault (~4 min)

Need: Cortex engine on `:8010`. This laptop's Cortex is `E:\Cortex`, not `D:\`.

```bat
E:\Cortex\START_ENGINE.bat
```

Wait until `http://127.0.0.1:8010/health` returns 200. Refresh OpenVault
`http://127.0.0.1:3010/engine`.

Pass: Cortex badge **Online**, base URL `http://127.0.0.1:8010`. Selection
models come from **OpenVault vault keys**, not a Cortex-side key store.
You must not paste a provider key into Cortex.

Fail if: Engine says Offline, or Cortex has its own keys UI that you used.

### HT5 - secrets-at-ship inject, no plaintext leak (~5 min)

Need: HT1 env loaded, a vaulted key checked on Ship, `SPACESHIP_FTP_ENV_DIR`
different from `SPACESHIP_FTP_DIR` (loader defaults `ov-env` vs `ht1-demo`).

1. `http://127.0.0.1:3010/ship` - check **Inject vaulted keys** for Groq
   (UI shows `GROQ_API_KEY`, names only).
2. Deploy `E:\OpenVault\scripts\ht1_sample` again with publish allowed.
3. Pass in the Ship result: `secrets_injected` names only, no key material.
4. Eyeball: `https://netie.ai/ht1-demo/` has **no** `.env`.
   `https://netie.ai/ov-env/.env` must be 403/404, not a download.
5. OpenVault logs under `%OPENVAULT_HOME%\logs` - search the key prefix
   (`gsk_`) and fail if it appears.

If `ship@netie.ai` is jailed to the web root and `ov-env` is fetchable, stop
and use the main cPanel user (name in `SHIP_SPACESHIP.md`) whose home is
`/home/<cpanel-user>` with
`SPACESHIP_FTP_DIR=netie.ai/ht1-demo` and `SPACESHIP_FTP_ENV_DIR=.openvault-env`
(off the web tree). Do not STOR secrets into a public folder.

Simulate must never invent a host URL. This doc does not invent SaaS or billing.

## Verify (engineers)

```bash
cd OpenMW
uv run pytest tests/test_freeroute_acceptance.py tests/test_gate_execute.py tests/test_gate_audit.py tests/test_ship_engine.py tests/test_one_seat_demo.py -q
uv run python scripts/one_seat_demo.py --out .demo_evidence/one_seat.json
```

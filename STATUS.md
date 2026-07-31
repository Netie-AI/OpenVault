# OpenVault — Status

> Canonical “what’s done / what’s next.” Deferred ideas: [`PARKINGLOT.md`](PARKINGLOT.md).
> Protocol/architecture: [`implementation_plan.md`](implementation_plan.md).

Last reconciled: 2026-07-28.

**UI:** Real app is `apps/web` on **`:3010`**. Old `OpenMW/webui/index.html` **deleted** — `:5000/` redirects to the app. Demo: `python apps\cli\openvault_cli.py demo` or `scripts\windows\Start-OpenVaultDemo.ps1`.

**Agent split:** [`docs/AGENT_SPLIT.md`](docs/AGENT_SPLIT.md) · **Claude decisions:** [`docs/CLAUDE_DECISIONS.md`](docs/CLAUDE_DECISIONS.md)

**Shipped from Claude decisions:** Middleware Gain killed (`available: false`, no invented %; legacy tab → Routing only) · hop `is_synthetic` · auto-select never picks sponsored (test pinned) · vault FreeRoute budget + free-fallback coverage banner · C10 already live ( `-MockHealth` opt-in only).

---

## One app

OpenVault (`:5000`) is the local control plane:

**see red hotspot → acknowledge model slots → hold keys → ship → mesh into Cortex → gated fix**

| Tier | Package | Role |
|------|---------|------|
| Health | `openmw.openvault.health` | Laptop device inventory |
| Observe | `openmw.openvault.observe` | PathTrace hops + severity (`hot` = red) |
| Vault | `openmw.openvault.vault` | Encrypted keys, accounts, proxy |
| Ship | `openmw.openvault.ship` | Deploy / FreeBuild / email gates |
| Mesh | `openmw.openvault.mesh` | Local mesh + Cortex client + `/api/slots` |
| Control | `openmw.openvault.control` | GPU/CPU/fan remediation (dry_run default) |

Libraries (unchanged at repo root): `nvme_sentinel/`, `Profiler/`. Peer engine: Cortex at `http://127.0.0.1:8000` (mesh docs) or **`:8010`** when launched with the Netie/AirGPT stack.

### Netie one-click stack

Desktop **Netie** icon → `scripts/windows/Start-Netie.bat` → `Start-NetieStack.ps1`:

| Service | Port | Mode |
|---------|------|------|
| Cortex engine | **8010** | brain |
| OpenVault | 5000 | custody + gate + ship |
| AirGPT | 8765 | **backend only** (`AIRGPT_NO_BROWSER=1`) |
| Netie Space | desktop | front-door UI; chat title = **file name**; syncs into AirGPT RAG space “Netie Space” |

Install shortcut: `powershell -ExecutionPolicy Bypass -File scripts\windows\Install-NetieDesktopShortcut.ps1`

---

## APIs (new)

| Route | Purpose |
|-------|---------|
| `GET /api/observe/path` | Hop timeline + `severity` / `reach` for UI hotspots |
| `GET /api/slots` | All local + Cortex model slots acknowledged |
| `GET /api/control/capabilities` | Honest capability probe |
| `POST /api/control/action` | `dry_run` default; writes need `confirm: true` |
| `GET /api/freeroute/ratelimit` | FreeRoute token-budget snapshot (tier, remaining, reset) |
| `GET /api/vault/env-scan` | Which env vars can be auto-vaulted (masked values only) |
| `POST /api/vault/ingest-env` | Auto-import env secrets into the vault (`dry_run` default) |
| `GET /api/secrets` | Passwords + payment cards, **masks only** (no decrypt on this path) |
| `POST /api/secrets/passwords` · `POST /api/secrets/cards` | Create; card create refuses CVV outright |
| `PATCH`/`DELETE`/`revoke`/`rotate` `/api/secrets/{id}` | Metadata, lifecycle, rotation (payload changes are rotate, never patch) |
| `GET /api/secrets/{id}/reveal` | Plaintext password or PAN — loopback + `X-OpenVault-Reveal: intentional` + audited |

| `GET /api/access/registry` | Everything OpenVault can route to, by kind (`?kind=memory` to filter) |
| `POST /api/access/resolve` | Where does it live + may this caller go — location **plus gate verdict**, never content |
| `GET /api/access/uptime` | This process's uptime + every mesh surface (`up: null` = unprobed, not down) |

Legacy: `GET /api/health/bottleneck` still works (aliases observe).

---

## Free* rename (2026-07-27)

Product names are the **Free\*** family; **OpenVault keeps its name**. Mapping and
the exact scope live in [`PRODUCT_ROLES.md`](PRODUCT_ROLES.md#naming-2026-07-27).

| Was | Now | Canonical path |
|-----|-----|----------------|
| OpenIDE | **FreeIDE** | `/api/freeide/ready`, `/api/freeide/invoke` |
| OpenShip | **FreeBuild** | `/api/freebuild`, `/api/freebuild/plan`, `/api/ship/freebuild/status` |
| OpenFree | **FreeRoute** | `/api/freeroute/ratelimit` |

Old paths still answer, identically, but are hidden from the OpenAPI schema — a
rename that breaks shipped clients on the day it lands is a rename that gets
reverted. 374 display occurrences rewritten across 87 files. **Not** renamed:
Python modules, `OpenShipPlan`/`OpenShipClient`, `OPENSHIP_API_TOKEN`,
`OMNIROUTE_API_KEY`, `~/.openvault`, `OPENVAULT_*`, `X-OpenVault-Reveal`.

---

## Access routing — the layer above the gate (2026-07-27)

`route/access.py`. `/api/gate/check` answers "may I do X"; this answers "where is
X, who owns it, and may I". One surface for **memory · provider APIs ·
components · runtimes · local models · uptime · Free\* services**.

| Piece | State |
|-------|-------|
| Registry | Done — derived from live mesh peers + vault keys, never a hardcoded catalogue. An entry that cannot be derived does not appear |
| Resolve | Done — returns location + owner + a `GateDecision`. Unknown ids are `found: false` with a verdict, not a 404 |
| Intent → gate mapping | Explicit table (`read→retrieve`, `write/invoke→run`, `deploy`, `leave`, `connect`) so adding an intent forces a gating decision |
| Uptime | Done — tri-state `up` (`true`/`false`/`null`); `null` means unprobed. `POST /api/local/mesh/refresh` turns unknowns into answers |
| Memory | **Routed, never stored.** Cortex owns `/api/memory/*`; resolve returns its address plus a verdict (lock 5, lock 6) |
| Model orchestration | Slot preference is OpenVault's; architecture preset stays Cortex's (lock 2) |
| Tests | 16 in `tests/test_access_routing.py`, incl. a duplicate-route guard that catches alias decorators silently colliding |

**Known gap:** a resolve reports `reachable` from the last mesh probe, so a
surface that died since the last `refresh` still reads as up. Resolve does not
probe on demand — that would put a network round-trip in the hot path of every
access decision.

---

## Secrets custody — keys, passwords, cards (2026-07-27)

Full writeup: [`docs/SECRETS_CUSTODY.md`](docs/SECRETS_CUSTODY.md). Closes
`BACKEND_HONESTY_AUDIT.md` §1 item 4 (no access audit for secrets).

| Item | State |
|------|-------|
| Audit of `/api/keys*` gates | Done — reveal was gated; **every mutation was open**. Create/patch/delete/revoke/rotate, account key-create, incident-kill, keyvault upsert, and env-ingest are now loopback-only and audited |
| Password + `payment_card` kinds (`vault/secrets.py`) | Done — new `secrets` table in the **same** `keys.db`, **same** master key. No second vault |
| PCI posture | PAN sealed (Fernet), Luhn-checked, brand/last4/expiry in the clear by design. **CVV never stored** — `POST /api/secrets/cards` with `cvv` is a 400 with the reason, not a silent drop |
| Reveal gate parity | Done — `/api/secrets/{id}/reveal` has the identical loopback + intent header + audit trail as the key reveal, plus a `last_revealed_at` stamp on the record |
| Audit trail | One file, `~/.openvault/secret_audit.jsonl`. Card events carry `brand`+`last4` only; tests assert no PAN or password ever lands in it |
| Netie thin-client contract | Pinned by `tests/test_netie_thin_client_sync.py` — rotate → re-sync yields only the new secret; cards/passwords are invisible to the key sync |
| `webui` copy-secret button | Fixed — it was fetching the reveal route without the header, getting a 428, and silently copying an empty string |
| Tests | 21 in `test_secrets_custody.py`, 6 in `test_netie_thin_client_sync.py`, 4 pre-existing in `test_secret_reveal_gate.py` |

**Open, tracked, not fixed here:** `KeyVault.list_keys` decrypts every secret
just to build a mask, so `GET /api/keys` holds every plaintext in memory on each
call (needs a stored-mask column + migration). And **Netie's `user.env` is
plaintext on disk** while its password/license stores are DPAPI-wrapped — the
one cache holding live cloud API keys is the one that is not protected. That is
a Netie-side `EnvLoader` fix.

---

## Local mesh runbook

Ports: OpenVault `5000` · Rust auth `5055` · Cortex `8000` · FreeIDE `8765` (AirGPT).

1. Start Cortex yourself (e.g. from `D:\Cortex`) on `:8000`.
2. `powershell -ExecutionPolicy Bypass -File scripts\windows\Start-LocalMesh.ps1` (optionally `-WithRustAuth`).
3. Open http://127.0.0.1:5000/#mesh → approve peers if needed.
4. Connect pack: `.openvault/connect_pack.json`.

`Start-LocalMesh` does **not** start Cortex/FreeIDE. Sync: `cd OpenMW && uv sync` (separate from root sentinel / Profiler syncs).

---

## nvme-sentinel — done (v0.1.0)

HAL/adapters/CLI/bench/CI green. Interview gate P1–P6 complete.

---

## Small Software LAN cloud (2026-07-23)

Pivot toward “Cloud for Small Software” + multiplayer agents — **LAN-first**, not AWS/Azure.

| Piece | Status |
|-------|--------|
| `/api/cloud/devices` | Done — ARP + local iface discovery |
| `/api/cloud/firewall/check` | Done — bypass/force → WARN + **deny** |
| `/api/cloud/shares` | Done — publish/list share codes (no secrets) |
| `/api/cloud/sessions` | Done — multiplayer agent session stub |
| Gate bypass hardening | Done — `bypass`/`force`/`skip_rules` never honored |
| AirGPT FreeIDE Share LAN | Done — `openideShareLanApp()` |
| Stress | `python scripts/stress_four_mesh_playwright.py` (API + tabs) |
| Rust `:5055` | Skipped when `cargo` missing |

FreeIDE URL in mesh: **`http://127.0.0.1:8765`** (AirGPT), not stub `:5100`.

---

## FreeRoute gateway + auto-vault (2026-07-24)

OpenVault owns free-gateway routing for **FreeRoute** (PRODUCT_ROLES). The gateway
now budgets cost, not just requests.

| Piece | Status |
|-------|--------|
| Dual-bucket limiter (`vault/ratelimit.py`) | Done — request bucket (QPS) + token bucket (`prompt + max_tokens`) |
| Smooth refill | Done — continuous refill, no fixed-window boundary burst |
| Reserve → refund | Done — reserves `max_tokens` up front, refunds the unused remainder; failed upstream refunds in full |
| Tiers | Done — `local` (unmetered loopback) / `free` / `pro`, via `X-FreeRoute-Tier` |
| `429` + `Retry-After` | Done on `POST /v1/chat/completions` |
| `X-RateLimit-Tier/Limit/Remaining/Reset` | Done on every gateway response |
| Auto-vault from env (`vault/env_ingest.py`) | Done — scans credential-shaped env vars, skips placeholders, stores encrypted; secrets never echoed |
| Redis + Lua bucket store | **Not done** — `BucketStore` protocol is the seam; `InMemoryBucketStore` is single-node only |
| Streaming (`stream: true`) | **Not done** — still `400`; reserve/refund logic is already stream-shaped |

Identity comes from `X-FreeRoute-Identity` (falls back to client host); budgets are
per identity per tier.

---

## Layer contract conformance (2026-07-24)

App layer (AirGPT `:8765` / FreeIDE) → custody layer (OpenVault `:5000`) →
engine layer (Cortex `:8000`). Locked by `tests/test_contract.py`.

**Fixed — contract drift:** the mesh defaulted FreeIDE to `:5100`, the legacy
standalone stub, so the connect pack (the shared wiring doc every peer reads)
published a dead URL. AirGPT serves FreeIDE on **`:8765`**. `DEFAULT_PORTS` /
`OPENIDE_DEFAULT_URL` in `mesh/local_mesh.py` are now the single source of
truth; `app.py`, `cli.py`, `Start-LocalMesh.ps1`, the example config, the webui
and the docs all derive from it. `scripts/openide_stub.py` stays on `:5100` —
it *is* the stub, and is now opt-in rather than the default.

**Added — missing contract endpoint:** `GET /api/freeide/ready` preflights
FreeIDE Run (keys + mesh approval + gate) and states `keys_source_of_truth:
openvault`, per the keys lock in PRODUCT_ROLES.

`tests/test_contract.py` asserts: default ports match the cheat-sheet, every
bridge/gate route is served, the connect pack pins `:8765`, `bypass` / `force` /
`skip_rules` can never produce a silent allow on either the gate or the LAN
firewall, and the keyvault snapshot still declares OpenVault the source.

---

## In-process ship engine + GitHub (2026-07-24 night)

Stolen FreeBuild library/local-auth into OpenVault — **local engine is primary**;
remote `openship_client` is optional (`prefer_remote_openship`).

| Piece | Status |
|-------|--------|
| `ship/engine.py` | Done — detect → cicd → domain → target host steps |
| `ship/github_auth.py` | Done — `gh` CLI (repo/workflow/read:org) + PAT file |
| `ship/library.py` | Done — folder / URL / upload session / clone |
| `/api/ship/github/*` | Done — connect, status, repos, branches, PAT |
| `/api/ship/library*` | Done |
| `/api/ship/engine` | Done |
| AWS skills vendored | `vendor/awslabs-agent-plugins/plugins/deploy-on-aws/skills` |
| AGENT_LANES | **Released** (ship-deploy / webui-deploy free) |
| SSH VPS executor | **Not done** — next |
| Native AWS Lambda IaC | Teach + MCP hint; enable awsiac MCP to generate |

---

## Redis+Lua FreeRoute + contract audit (2026-07-25)

| Piece | Status |
|-------|--------|
| `vault/redis_store.py` | Done — atomic dual-bucket Lua EVAL |
| `OPENVAULT_REDIS_URL` | Activates Redis backend; else in-memory |
| Cortex `workflow_openvault.ping` | Fixed → `/api/healthz` (was wrong AirGPT path) |
| FreeRoute status aliases | `remaining` / `remaining_tokens` for Cortex budget check |
| Audit doc | [`docs/CONTRACT_AUDIT.md`](docs/CONTRACT_AUDIT.md) |
| `:20128` | External OmniRoute only; FreeRoute serves on `:5000/v1` |
| Streaming `/v1` | **Still not done** |

---

## Next priorities

**Front door = interface automation.** Authority:
[`docs/CLIPDROP_CONTRACT.md`](docs/CLIPDROP_CONTRACT.md).

**Egress reliability (new):** [`docs/DESIGN_TIERED_QUEUE_LB.md`](docs/DESIGN_TIERED_QUEUE_LB.md)
+ Claude asks [`docs/ASKS_CLAUDE_QUEUES_RAG.md`](docs/ASKS_CLAUDE_QUEUES_RAG.md).

| # | Slice | Status |
|---|--------|--------|
| — | DPAPI master.key | **Done** — suite **412 passed** |
| Q1 | Attempt classifier + park (429 must not open circuit) | **Done** — `route/attempt.py`; 15 tests green |
| Q2 | In-memory Q0+Q1 kill-and-send @ 3 hard fails | **Blocked on Claude Ask A** |
| Q3 | Least-used balancer within role band | After Q2 / Ask E |
| C1 | ClipDrop A1–A4 + Settings clipboard OFF default | **Done** |
| C2 | `POST /api/keys/ingest` | Gated until AirGPT client |
| A1 | AirGPT async `/sources` job (backend) | Next Cursor in `D:\AirGPT` — stay out of `index.html` while Claude owns it |
| A2 | AirGPT media captions/OCR | After A1; Claude Ask D for accept criteria |

**Parked:** health sparklines, streaming `/v1`, SSH VPS.

---

## Clone-and-verify

```bash
uv sync && uv run pytest tests/ -q && uv run mypy nvme_sentinel tests
cd OpenMW && uv sync && uv run pytest tests/ -q && uv run mypy openmw
uv run python -c "from openmw.openvault.app import create_app; create_app(mock_health=True)"
uv run openmw doctor -o /tmp/doctor_check
```

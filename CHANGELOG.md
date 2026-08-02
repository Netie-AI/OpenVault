# CHANGELOG

Append-only. Never edited, only added to. Newest first.

## 2026-08-02 — Repo cleanup: quarantine + docs migration

Audited `nvme_sentinel/`, `Profiler/`, `OpenMW/` — confirmed ~100% live/tested, no dead
product code. Quarantined ~18 confirmed-dead files/dirs into `bin/` for founder review
(stray generated artifacts, one-off scratch scripts, 3 zero-inbound-link docs, orphaned
`apps/click`, electron leftovers). Archived 7 closed/superseded docs as MADR-format
decision records under `docs/decisions/DR-0002..DR-0008`. Relocated `implementation_plan.md`
to `docs/reference/nvme-sentinel-spec.md`. Retired `next_plan.md` and `AGENT_LANES.md`
(content folded into this file, `STATUS.md`, `PARKING_LOT.md`, and `CLAUDE.md`).

## 2026-07-31 — Streaming, ship SSE, health history, FreeBuild CI/CD (next_plan #1-#6, #9)

- `#6` Stored-mask column on `keys` — `list_keys` no longer decrypts every secret to build a mask (`vault/store.py`, `tests/test_stored_mask.py`).
- `#1` Streaming `POST /v1/chat/completions` (`vault/proxy.py::prepare_chat_stream`, `tests/test_streaming_v1.py`).
- `#3` Ship SSE + `/ship/deploy/[id]` + BuildLogPane (`ship/stream.py`, `GET /api/ship/engine/{id}/stream`).
- `#2` Health history + vault sparklines (`vault/health_store.py`, `GET /api/keys/{id}/health`, `KeyHealthSpark` — see `docs/decisions/DR-0007-card-health-history.md`).
- `#4` FreeBuild CI/CD page (`apps/web/src/app/ship/cicd/page.tsx`, nav CI/CD).
- `#5` Remote FreeBuild `project_id` honesty — `POST /api/freebuild/{id}/execute` returns 400 with a clear detail instead of silently proceeding.
- `#9` Cortex `:8010` smoke snapshot green (health 200 + OV JWKS 200) — merge stayed operator-gated, not automatic.
- One-stop B pass: precheck logs use `key_ref` + label/provider/error (no full vault UUID); `openvault up` auto-opens `:3010`; Vault shows failing key identity + Sync preview; Ship has GitHub connect panel; Providers in nav.
- UI: real app is `apps/web` on `:3010`. Old `OpenMW/webui/index.html` deleted — `:5000/` redirects to the app.

## 2026-07-27 — Access routing, secrets custody, Free* rename

- Access routing (`route/access.py`): registry derived from live mesh peers + vault keys
  (never a hardcoded catalogue); `/api/access/resolve` returns location + owner + gate
  verdict; explicit intent-to-gate mapping; 16 tests in `tests/test_access_routing.py`.
  Known gap: resolve reports from the last mesh probe, not a live check.
- Secrets custody (`vault/secrets.py`): password + payment-card kinds in the same `keys.db`
  under the same master key; every custody mutation (not just reveal) is now loopback-only
  and audited; CVV is refused outright, never stored. See `docs/SECRETS_CUSTODY.md` and
  `docs/decisions/DR-0005-backend-honesty-audit.md`.
- Free* rename: OpenIDE to FreeIDE, OpenShip to FreeBuild, OpenFree to FreeRoute (display +
  routes). OpenVault keeps its name. Old paths stay as hidden aliases. Not renamed: Python
  modules, class names, env vars, `~/.openvault`. See `PRODUCT_ROLES.md`.

## 2026-07-25 — Redis+Lua FreeRoute, contract audit

- `vault/redis_store.py`: atomic dual-bucket Lua EVAL; `OPENVAULT_REDIS_URL` activates it,
  else in-memory. Cortex `workflow_openvault.ping` fixed to `/api/healthz`.
- Cross-layer contract audit — see `docs/decisions/DR-0004-contract-audit.md`.
- Backend honesty audit — see `docs/decisions/DR-0005-backend-honesty-audit.md`.

## 2026-07-24 — In-process ship engine, FreeRoute gateway, layer contract

- Ship engine made primary (not the remote client): `ship/engine.py` (detect to cicd to
  domain to target host), `ship/github_auth.py` (gh CLI + PAT), `ship/library.py`
  (folder/URL/upload/clone). AWS skills vendored for IaC generation.
- FreeRoute gateway: dual-bucket limiter (QPS + token budget) with smooth refill,
  reserve-then-refund around `max_tokens`, `local`/`free`/`pro` tiers, `429` +
  `Retry-After`, rate-limit headers on every response. Auto-vault from env
  (`vault/env_ingest.py`) — scans credential-shaped env vars, never echoes secrets.
- Layer contract conformance: fixed the mesh defaulting FreeIDE to the dead `:5100` stub
  instead of `:8765`; `DEFAULT_PORTS` in `mesh/local_mesh.py` is now the single source of
  truth. Added `GET /api/freeide/ready`. Locked by `tests/test_contract.py`.
- Small Software LAN cloud v0 shipped — see `docs/decisions/DR-0002-small-software-lan-cloud.md`.

## 2026-07-23 — nvme-sentinel v0.1.0

HAL/adapters/CLI/bench/CI green. Interview gate P1-P6 complete.

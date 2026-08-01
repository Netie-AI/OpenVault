# OpenVault — next_plan

> Overflow tracker. Last updated: 2026-07-31 (execution pass).

## Smoke gate (required before further work)

| Check | Result (2026-07-31) |
|-------|---------------------|
| `:5000/api/healthz` | green |
| `:3010/` + vault/ship/proxy/peers | green |
| FreeRoute `POST /v1/chat/completions` | 503 without healthy keys (expected) |
| OpenVault JWKS `/keys/jwks` | green |
| Cortex `:8010/health` | green when Cortex running |

`openvault up` now uses `uv run --no-sync` and writes `OPENVAULT_HOME/logs/console.up.log` on API failure.

## Shipped this pass

| # | Item | Evidence |
|---|------|----------|
| 6 | Stored-mask column on `keys` — `list_keys` no longer decrypts | `vault/store.py`, `tests/test_stored_mask.py` |
| 1 | Streaming `POST /v1/chat/completions` | `vault/proxy.py::prepare_chat_stream`, `tests/test_streaming_v1.py` |
| 3 | Ship SSE + `/ship/deploy/[id]` + BuildLogPane | `ship/stream.py`, `GET /api/ship/engine/{id}/stream`, `apps/web/.../ship/deploy/[id]` |
| 2 | Health history + vault sparklines | `vault/health_store.py`, `GET /api/keys/{id}/health`, `KeyHealthSpark` |
| 4 | FreeBuild CI/CD page | `apps/web/src/app/ship/cicd/page.tsx`, nav CI/CD |
| 5 | Remote FreeBuild `project_id` honesty | `POST /api/freebuild/{id}/execute` → 400 with clear detail when remote API ready and `project_id` missing |
| 9 | Cortex `:8010` smoke snapshot | health 200 + OV JWKS 200 — **merge still operator-gated** (do not merge blind) |

## Still parked / blocked

| # | Item | Blocker |
|---|------|---------|
| 7 | Netie `user.env` DPAPI wrap | **Netie Space repo** (`EnvLoader`) — out of OpenVault tree |
| 8 | Q2 kill-and-send balancer @ 3 hard fails | Blocked on **Claude Ask A** (`docs/ASKS_CLAUDE_QUEUES_RAG.md`) |
| 9 merge | Merge `feat/openfree-token-budget` into main | Run dedicated smoke on clean checkout + operator merge; Cortex must stay up |

## Suggested next (after this)

1. Operator merge decision for #9 once smoke is re-run on a clean branch checkout
2. #8 when Claude answers Ask A
3. #7 in `D:\Netie Space`
4. Optional: stream usage refund when upstream sends `stream_options.include_usage`

# OpenVault — Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-08-02.

**UI:** real app is `apps/web` on `:3010`. `:5000/` redirects to it. Demo/up:
`python apps\cli\openvault_cli.py demo|up` or `scripts\windows\Start-OpenVaultDemo.ps1`.
Mesh UI: `http://127.0.0.1:3010/peers`.

**Phase 0 — branch vs main:** `feat/openfree-token-budget` carried OpenShip / secrets /
Netie apps and was ahead of `main`; this branch's history descends from it. Merging into
`main` stays operator-gated on a dedicated smoke (`:5000` healthz + FreeRoute chat + JWKS)
against a live Cortex — do not merge blind.

## One app

OpenVault (`:5000`) is the local control plane: see red hotspot -> acknowledge model slots
-> hold keys -> ship -> mesh into Cortex -> gated fix. Full tier map:
[`docs/ACTIVE.md`](docs/ACTIVE.md).

Product names are the Free\* family (OpenIDE -> FreeIDE, OpenShip -> FreeBuild, OpenFree ->
FreeRoute); OpenVault keeps its name. Canonical contract: [`PRODUCT_ROLES.md`](PRODUCT_ROLES.md).

## Known current gaps

- `/api/access/resolve` reports from the last mesh probe, not a live check —
  `POST /api/local/mesh/refresh` turns unknowns into answers on demand.
- Netie's `user.env` is plaintext on disk while its password/license stores are
  DPAPI-wrapped — a Netie-side `EnvLoader` fix, tracked in `PARKING_LOT.md`.
- Streaming reserve/refund does not yet refund when upstream sends
  `stream_options.include_usage`.
- `.github/workflows/ci.yml` covers `nvme_sentinel` only — `OpenMW`/`apps/web` have no CI
  job; verify locally with clone-and-verify below.
- Master key has no KDF/DPAPI wrap; GitHub PAT bypasses the vault — [`DR-0005`](docs/decisions/DR-0005-backend-honesty-audit.md).

## Next priorities

Front door = interface automation ([`CLIPDROP_CONTRACT.md`](docs/CLIPDROP_CONTRACT.md)). Egress
reliability: [`DESIGN_TIERED_QUEUE_LB.md`](docs/DESIGN_TIERED_QUEUE_LB.md) + [`ASKS_CLAUDE_QUEUES_RAG.md`](docs/ASKS_CLAUDE_QUEUES_RAG.md).

| # | Slice | Status |
|---|--------|--------|
| Q2 | In-memory Q0+Q1 kill-and-send @ 3 hard fails | Blocked on Claude Ask A |
| Q3 | Least-used balancer within role band | After Q2 / Ask E |
| C2 | `POST /api/keys/ingest` | Gated until AirGPT client |
| A1 | AirGPT async `/sources` job (backend) | Next in `D:\AirGPT` — stay out of `index.html` while Claude owns it |
| A2 | AirGPT media captions/OCR | After A1; Claude Ask D for accept criteria |
| — | Operator merge decision for Phase 0 | Once smoke is re-run on a clean checkout |
| — | `D:\Netie Space` `EnvLoader` DPAPI fix | See `PARKING_LOT.md` |
| — | Optional: SSH VPS executor | Ship lane, after interface automation |

## Clone-and-verify

```bash
uv sync && uv run pytest tests/ -q && uv run mypy nvme_sentinel tests
cd OpenMW && uv sync && uv run pytest tests/ -q && uv run mypy openmw
uv run openmw doctor -o /tmp/doctor_check
```

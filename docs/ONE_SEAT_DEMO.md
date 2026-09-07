# One-seat demo (auto-safe)

Buyer path: **vault → FreeRoute refuse → gated ship allow → gate deny**, with CLI/file evidence, under 15 minutes. No codebase reading required.

This path is **mocks / simulate only**. Human live gates HT1-HT5 were **CLEARED**
2026-09-04 (epic [#18](https://github.com/Netie-AI/OpenVault/issues/18) CLOSED;
boxes ticked 2026-09-07). This script still does not re-prove those live gates.

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
| FreeRoute empty refuse | HTTP 503 `openvault_no_keys` | Auto-safe — no live provider key |
| Vault key | Fake secret vaulted; secret not echoed | Demo secret only |
| FreeRoute sealed refuse | HTTP 403 `openvault_vault_sealed` | Auto-safe — not live paid chat |
| Gate allow + ship | `local_demo` / `simulate`, `mode=simulated`, empty `public_url` | **Not** a live CF/Coolify/Netlify URL |
| Gate deny | Execute 403, `allowed: false`, reasons listed | Refusal visible in evidence |

Optional UI stack (mock health + browser) is separate:

```bash
python apps/cli/openvault_cli.py demo
```

That starts `:5000` + `:3010`. It does **not** replace the scripted path above.

## Human live gates (HT1-HT5) - CLEARED

Founder walked these 2026-09-04. Agent ticked the #18 boxes 2026-09-07. Do not
rebuild. Simulate must still never invent a host URL.

| # | Gate | Cleared |
|---|------|---------|
| HT1 | Live deploy URL under the leave-machine gate | https://netie.ai/ht1-demo/ |
| HT2 | Live FreeRoute with vaulted keys | POST /v1/chat/completions 200 |
| HT3 | Passphrase unseal / lock (sealed refuse) | passphrase-scrypt; bak retired; restart sealed |
| HT4 | Cortex smoke through OpenVault | GET /api/cortex/status healthy |
| HT5 | Secrets-at-ship inject; no leak | inject to ov-env; public .env 403 |

Clerk rule: when the founder walks a later HUMAN_TEST_GATE, tick the epic boxes
in that same turn. Empty boxes after a founder walk are a clerk failure.

## Verify (engineers)

```bash
cd OpenMW
uv run pytest tests/test_freeroute_acceptance.py tests/test_gate_execute.py tests/test_gate_audit.py tests/test_ship_engine.py tests/test_one_seat_demo.py -q
uv run python scripts/one_seat_demo.py --out .demo_evidence/one_seat.json
```

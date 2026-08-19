# One-seat demo (auto-safe)

Buyer path: **vault → FreeRoute refuse → gated ship allow → gate deny**, with CLI/file evidence, under 15 minutes. No codebase reading required.

This path is **mocks / simulate only**. It does **not** complete the human live gates (HT1–HT5).

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

## Stop here — human-only gates (HT1–HT5)

Agents **must not** claim these done. Founder clears them on epic [#18](https://github.com/Netie-AI/OpenVault/issues/18):

1. **HT1** — Live Cloudflare Pages / Coolify / Netlify deploy with a real openable URL under the leave-machine gate
2. **HT2** — Live FreeRoute chat with real vaulted provider keys (spend / ToS / quota)
3. **HT3** — Passphrase unseal / lock UX on `:3010`
4. **HT4** — Phase 0 Cortex smoke through OpenVault (no second vault)
5. **HT5** — Secrets-at-ship inject into a real FreeBuild execute; human leak eyeball

Simulate must never invent a host URL. This doc does not invent SaaS or billing.

## Verify (engineers)

```bash
cd OpenMW
uv run pytest tests/test_freeroute_acceptance.py tests/test_gate_execute.py tests/test_gate_audit.py tests/test_ship_engine.py tests/test_one_seat_demo.py -q
uv run python scripts/one_seat_demo.py --out .demo_evidence/one_seat.json
```

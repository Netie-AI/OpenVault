---
keywords: PRD, F32, GUI, :3010, compiling-proxy, FreeRoute, OpenShip, FreeBuild, #15, #16, #17, #18, DR-0003, feedback-intake
main_idea: Mode B intake F32. Reopen closed #17 (GUI hang) and #16 (vendor OpenShip leftover) in flight. Queue reopen #15 (FreeRoute UI). Do not reopen #18. API FreeRoute already 200.
---

# F32 PRD intake -- GUI + FreeRoute + OpenShip completeness

PREFLIGHT: PARTIAL. INDEX had only DR-0015. Reused PRD F7/F10/F11/F15 and closed #15/#16/#17/#50.

## Nearness (artifacts, this session 2026-09-08)

Not a percentage. Customer seat is the GUI.

- `http://127.0.0.1:3010/` timed out (8s). `/freeroute` and `/ship` same. PID 4084 is `node.exe` LISTENING on 127.0.0.1:3010 -- bound, not serving pages. Matches `STATUS.md` "Compiling-proxy hang" and `CHANGELOG.md` 2026-09-04 HT3 used API because `:3010` stayed on Compiling proxy.
- Pages exist as source: `apps/web/src/app/freeroute/page.tsx`, `apps/web/src/app/ship/page.tsx`, `apps/web/src/app/page.tsx`, `apps/web/src/app/vault/page.tsx`, `apps/web/src/app/gate/page.tsx`. Nav in `apps/web/src/components/shell/AppBar.tsx`. Bind path: `apps/web/src/lib/api/client.ts` `/ov-api` rewrite in `apps/web/next.config.mjs` to `http://127.0.0.1:5000`. `apps/web/src/proxy.ts` only enforces `/ov-api/*`.
- `http://127.0.0.1:5000/api/healthz` 200. `POST /v1/chat/completions` 200 (real model reply) -- FreeRoute API works. `GET /api/ship/targets` 200, 6802 bytes.
- FreeRoute router: `OpenMW/openmw/openvault/routers/freeroute.py` `GET /api/freeroute/status`. Handler: `OpenMW/openmw/openvault/app.py` `@app.post("/v1/chat/completions")`. Proxy skip: `vault/proxy.py` anthropic "not via /v1 proxy yet". Strategies still "8 of 18" in `route/strategies.py`. No `apps/web` playground page (TAS `/playground` claim is false).
- Ship hosts (`ship/hosts/__init__.py`): `cloudflare_pages`, `coolify`, `netlify`, `spaceship_ftp`, `vps_ssh` -- more than Cloudflare Pages. Vendor leftover: `ship/openship_client.py` wraps OPENSHIP_URL; `ship/cloud_targets.py` still lists `openship_cloud` / openship.io. `ship/engine.py` default is local; remote OpenShip optional.
- Desktop: `apps/cli/openvault_cli.py` `openvault app`; `apps/shell/electron/main-openvault.js`. Not clicked this intake (GUI hang).
- GitHub: `gh issue list --repo Netie-AI/OpenVault --state open` empty. #12-#39, #48, #50, #52 CLOSED. #17 closed 2026-08-06 from pytest+tsx, not a live browser.

## Routing

| Item | Maps to | Call |
|---|---|---|
| A GUI hang | CLOSED #17 | REOPEN. Decomposition. |
| B FreeRoute incomplete | CLOSED #15 (#50 ticket) | REOPEN + QUEUE. API EARS hold. |
| C OpenShip incomplete | CLOSED #16 | REOPEN. Decomposition (vendor path). |
| D complete PRD / all tickets | F2; OPEN=none | Not a new epic. Not amendment. No #18. |

## EPIC Agent (do not file from this agent)

In flight: reopen #17 + #16. Queue: reopen #15. No tickets on #18/#12/#13/#14/#33/#48/#38/#50-as-parent.

## Must not build this wave

F13-F15 parked (SaaS/billing/RTK/skills). F23 Containers. F27-F30 export/AirDrop. Usage $/unit. Vendor OmniRoute/OpenShip runtimes. Analog BAN. Palantir P1. HT1-HT5 rebuild.

---
keywords: PRD, F33, standalone, Electron, localhost, tray, palette, durability, exFAT, :3010, F20, #17, #56, P-CTL-2
main_idea: Mode B F33. Standalone-not-localhost splits hide-loopback (F20, mint later) vs no-HTTP installer (PRD amendment). Do not reopen #17/#18. Palette Exit and child respawn have no live parent. Control launchers stay P-CTL-2 + DR unaccepted.
date: 2026-09-09
kind: prd_intake
product: OpenVault
---

# F33 PRD intake -- standalone apps + OpenVault stay-alive

PREFLIGHT: PARTIAL. Reused OpenVault F20 desktop, F29 Control desk + auto-host STOP, F32 GUI #17 wait live openvault app; Cortex F3 no UI organ; Control P-CTL-2; 2026-09-08_3010-turbopack-exfat.md.

## Nearness (artifacts, no percent)

Customer seat is a window that does not feel like a browser tab.

- `D:\OpenVault\apps\shell\electron\main-openvault.js`: `WEB_URL` is `http://127.0.0.1:3010`. Window `loadURL(WEB_URL)`. Close HIDES unless `app.isQuitting`. Tray menu has Open / Open in Browser / Quit (Quit sets `isQuitting`). Child API (`uv run openmw console :5000`) and web (`npm run start|dev`) log exit; **no respawn**. No command palette.
- `D:\OpenVault\STATUS.md`: F32 shipped `#15` `#16` `#17` CLOSED. Electron OpenVault + `next start`. UI still named as `http://127.0.0.1:3010/` and `openvault app`.
- Live gh 2026-09-09: OpenVault `#17` CLOSED, `#56` CLOSED (`openvault app` loads UI after API `:5000`), `#55` CLOSED (HTML restore). PRD wave table still said `#17` OPEN until `#56` -- clerk lag, not a live parent.
- ExFAT hang class already on disk: `docs/subagents_findings/2026-09-08_3010-turbopack-exfat.md` (Turbopack os error 5; webpack or `next start`; wait for `text/html` not TCP).
- DMS: no Electron. `D:\DMS\scripts\windows\Start-DMSStack.ps1` + `Start-DMS.bat` + `Install-DesktopShortcut.ps1` ("DMS Demo") opens the browser at `:3000`.
- Cortex: no Cortex.exe. See Cortex finding `2026-09-09_prd-cortex-app-missing.md`.
- Analog: TAS-OPENVAULT live Electron. DISTILL stay-resident into `main-openvault.js`. Frozen OmniRoute Electron is study-only (DR-0003).

## Routing

| Item | Maps to | Call |
|---|---|---|
| Hide 127.0.0.1 in the OpenVault window | F20 desktop; no live epic | Mint later (Epic Agent). Not a PRD widen. |
| Command-palette Exit; stay up until then | F20; tray-Quit already | Same new parent. Do not reopen `#17` (web pages EARS) or `#18` (HT CLEARED). |
| API/web child crash, long session, exFAT | F20 + leftover of `#55` | Same parent: respawn + HTML-ready. Not Cloudflare Containers (F23). |
| Packaged installer / no local HTTP servers | **NO epic** | **PRD amendment NEEDS-YOU.** FAQ assumes `:5000` / `:3010`. |
| DMS.exe | DMS F90 | STOP unless founder signs. Shortcut already exists. |
| Control/Crew open-all-apps | Control P-CTL-2 | STOP until DR-PROPOSED-control-loopback-launchers accepted. Writer `netie-controlagent`. R-0015. `/v1/run` stays 405. |

## What may be built this wave vs STOP

**MAY (after founder pick A = hide URL, and Epic Agent mints one F20 parent):** OpenVault Electron stay-resident -- palette Exit, hide address, respawn API/web children, keep webpack/`next start` exFAT rule. Visible. Pair with existing GRANT queue, do not seat over C7.

**STOP:** Cortex.exe (Cortex F3/F34). Control spawning Cursor/Grok. Control POST `/v1/run`. DMS Electron unsigned. Reopen `#18`. Cloudflare Containers (F23). Auto-host-all (F29). Second orchestrator. Palantir P1. n8n.

## EPIC Agent (do not file from this agent)

0 tickets from PRD Agent. After NEEDS-YOU A: one parent epic, contract none, repo Netie-AI/OpenVault, depends on none. File contention: `apps/shell/electron/main-openvault.js` only.

# Agent lanes — Claude ↔ Cursor (stop clobbering)

> Shared lock file. **Claim a lane before editing hot files.** Release when done.
> Evidence beats claims (see `.cursor/skills/claude-cursor-gatekeeper/SKILL.md`).

Last updated: 2026-07-24 · branch `feat/openfree-token-budget`

---

## Roles

| Agent | Owns | Does not |
|-------|------|----------|
| **Cursor** | Execute patches, run `uv run pytest` / mypy, start console, paste evidence | Invent architecture without Claude when plan is ambiguous |
| **Claude** | Review diffs, plan next gates, architecture calls, parking-lot prioritization | Rewrite Cursor's same files in parallel without claiming the lane |

---

## Hot files (must claim)

| Lane ID | Paths | Current owner | Status |
|---------|-------|---------------|--------|
| `ship-deploy` | `OpenMW/openmw/openvault/ship/*`, ship tests | — | **free** — released after local engine + GitHub auth (2026-07-24) |
| `webui-deploy` | ~~`OpenMW/webui/index.html`~~ deleted — use `apps/web` | — | **gone** |
| `webui-build` | AirGPT Build/RAG tab (other repo) | — | free — **do not edit from OpenVault chat** |
| `vault-openfree` | `OpenMW/openmw/openvault/vault/ratelimit.py`, `proxy.py` | — | free |
| `vault-ui` | `apps/web/src/app/vault/*`, `apps/web/src/components/vault/*`, `apps/shell/electron/*` ClipDrop | — | **free** — ClipDrop A1–A4 shipped |
| `route-queue` | `OpenMW/openmw/openvault/route/attempt.py`, `vault/proxy.py`, `vault/fallback.py` park | **Cursor** | **active** — Slice 1 done; Slice 2 blocked on Claude Ask A |
| `mesh-contract` | `OpenMW/openmw/openvault/mesh/*`, `tests/test_contract.py` | — | free |
| `docs-status` | `STATUS.md`, `PARKINGLOT.md`, `PRODUCT_ROLES.md`, `AGENT_LANES.md` | **Cursor** | **active** — queue design + Claude asks |
| `webui-build` | AirGPT Build/RAG tab (`D:\AirGPT`) | **Claude** | **active** — Space 5 verify; Cursor stays out of `index.html` |

**How to claim:** edit this table → set Current owner + Status=`active` + one-line intent.  
**How to release:** Status=`free`, owner=`—`, note evidence (commit hash or "uncommitted").

---

## Message to Claude

1. Read [`docs/ASKS_CLAUDE_QUEUES_RAG.md`](docs/ASKS_CLAUDE_QUEUES_RAG.md) — long asks A–E.
2. Desk-review Slice 1 (`route/attempt.py` + park) — 429 no longer opens hop circuits.
3. Live-verify AirGPT Space 5 after purge (Ask C). You own `index.html` this session.
4. Do **not** ask Cursor to rebuild toast/View/Docs/authority — already done.
5. Answer Ask A before Cursor starts Q0/Q1 queues.

---

## Shipped this release (steal FreeBuild + AWS skills)

| Module | Job |
|--------|-----|
| `ship/engine.py` | In-process ship engine (primary — not remote client) |
| `ship/github_auth.py` | gh CLI / PAT connect — FreeBuild local-auth steal |
| `ship/library.py` | Folder / URL / upload session / clone |
| `ship/openship_client.py` | Optional remote only (`prefer_remote_openship`) |
| `vendor/openship` | Reference clone |
| `vendor/awslabs-agent-plugins/plugins/deploy-on-aws/skills` | deploy + architecture-diagram + elastic-beanstalk |

APIs: `/api/ship/library*`, `/api/ship/github/*`, `/api/ship/engine`, `/api/deploy/one-press` → engine first.

---

## Message to Claude

Lanes **released**. Review engine + github_auth. Next priorities:

1. SSH executor for `vps_ssh` (Hetzner/any) — steal FreeBuild `ssh-client.ts` patterns into Python.
2. Optional: enable Cursor MCP `awsiac` / `awsknowledge` / `awspricing` for Lambda/IaC generation from `aws_guide`.
3. OmniRoute-style Keys UI (still open).
4. Do not reintroduce remote-only FreeBuild client as the only path.

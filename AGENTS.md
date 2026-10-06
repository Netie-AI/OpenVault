# AGENTS.md

Binding rules for every agent. Do not copy them into CLAUDE.md. Current truth: STATUS.md.

## Rules

1. One vault: `E:\OpenVault\.openvault`. No second vault and no second key store.
2. HUMAN_STOP: do not bind `:5000` on a public or LAN interface.
3. Refs only. Agents close nothing. Write `Refs` and the issue. Never Closes, Fixes, or Resolves.
4. Auth and key paths go through R-0003 (a different agent verifies) and Gating (founder gate).
5. Product boundaries: [PRODUCT_ROLES.md](PRODUCT_ROLES.md). Leave that file's content alone.
6. Core product, founder 2026-10-06 11:59 MYT: Rust console (`OpenMW/rust`), Electron `apps/shell`, `ship/` FreeBuild hosting, and product-role code.
7. Building the product comes before cleanup.
8. GitHub issues are the ticket list. Do not keep a markdown backlog.
9. A feature or defect you are told about goes to the PRD agent before it is built.

## Fast lane

`tier:fast` is docs, tests, UI/console/Electron UI, and #137 dead-code cleanup. Dead-code removal keeps an attic copy and needs a green import check.

Merge on CI 9/9 plus one second-model AGREE that names the model family. R-0003 and Gating run after the merge. Revert immediately if they fail.

Full tier is auth, HttpGuard, `admin_token`, keys, custody, unseal, FreeRoute routing, prove-host redeploys, and #135. It stays serial before merge: PR Bot CLEAR, then Security, then R-0003 and Gating.

The author labels each PR. When unsure, it is full.

## Pointers

| Need | Where |
|------|--------|
| What is true now | STATUS.md (60 lines max) |
| Claude Code entry | CLAUDE.md |
| Product roles | PRODUCT_ROLES.md |
| Map | docs/ACTIVE.md |
| History | CHANGELOG.md |
| Deferred | PARKING_LOT.md |
| Decisions | docs/decisions/ |
| Human-test gates | Epic 18, HT1-HT5 cleared. Humans perform them. Agents record them. |
| Removed long copies | docs/archive/ |

Code rules for `uv`, mypy, and `nvme_sentinel/` stay in CLAUDE.md.

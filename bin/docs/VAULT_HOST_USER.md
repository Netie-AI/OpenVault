# Host vs User vault access (OpenVault · Hub · AirGPT)

Product rule: **OpenVault is a host-side console.** Users on other PCs do not get the full vault UI.

## Roles

| Role | Where | Vault UI | What they get |
|---|---|---|---|
| **Host** | Machine running OpenVault + Electron | Full `/vault` | Create/rotate/revoke keys, roles, precheck, seed |
| **User / guest** | Phone / other PC / AirGPT client | **No vault page** | Only what Hub exposes via a **share grant** |
| **Hub / enterprise** | Supervision UI | Read/inspect grants | Same interface for hosts to approve shares |

## Share layer (to build)

One purpose-built API surface (not “open the vault over the LAN”):

- `POST /api/vault/shares` — host creates a grant: `{ functions: [...], expires, peer }`
- Allowed functions only (examples): `chat.completions`, `models.list`, `precheck.status`
- Denied by default: `GET /api/keys/{id}/secret`, key CRUD, deploy spawn routes
- Hub + AirGPT consume the grant; Cortex already surfaces engine status — **storage of truth stays in OpenVault**

Custody audit: every share mint / use / revoke is logged (secrets were never audited — that hole stays P0).

## Not in this pass

Passkeys / email 2FA login for host console — local desktop is loopback-first; remote Hub auth is a separate track once shares exist.

# OpenVault account custody

Operators keep tenant signup, private relay, and API keys under the Netie / OpenVault platform.

## Preferred signup

| Method | When | Custody |
|--------|------|---------|
| **Netie email** (`*@netie.ai`) | Default / best | Full — mailbox, relay, keys, deploys under us |
| External email | Customer insists | Account linked; still store keys in OpenVault |
| Google account | OAuth-linked email | Same as external email; provider recorded as `google` |

Private relay addresses look like `xxxxxxxxxxxx@relay.netie.ai` and map 1:1 to the account.

## Operator powers

For any account (`GET /api/accounts/{id}`):

- Create and encrypt-store provider keys (`POST /api/accounts/{id}/keys`)
- Precheck / fallback using the shared vault
- Revoke (`POST /api/keys/{id}/revoke`) or rotate (`POST /api/keys/{id}/rotate`)
- **Incident kill**: `POST /api/accounts/{id}/incident` disables every key, marks the account `compromised`, and optionally mints replacement cloud secrets (or returns `needs_register`)

## APIs

| Route | Purpose |
|-------|---------|
| `POST /api/accounts` | Create (prefer `auth_provider=netie_email`) |
| `GET /api/accounts` | List |
| `GET /api/accounts/{id}` | Operator bundle + keys |
| `POST /api/accounts/{id}/relay` | Re-allocate private relay |
| `POST /api/accounts/{id}/keys` | Save a managed key |
| `POST /api/accounts/{id}/incident` | Kill keys + optional cloud replace |
| `POST /api/keys/{id}/revoke` | Soft-kill one key |
| `POST /api/keys/{id}/rotate` | Replace secret, keep audit trail |

## UI

**Accounts** tab → create Netie email → save keys → **Incident: kill + replace**.

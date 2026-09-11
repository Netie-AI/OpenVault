---
keywords: PRD, F34, Control-F12, providers, HT5, MCP-credentials, sale, HARD_DENY, #18
main_idea: OpenVault F34 ack of Control F12. Free API register stays providers.py. Leak is HT5 CLEARED. MCP credentials stay OpenVault, not Control. Sale stays F14/F19. Do not reopen #18.
date: 2026-09-09
kind: prd_intake
product: OpenVault
---

# F34 PRD intake -- Control Apps dump (keys / MCP / sale / leak)

PREFLIGHT: PARTIAL. Reused OpenVault F14/F19/F27/F28/F33, DR-0012, HT5 #18 CLOSED, Control F12.

## Nearness

- D:\\OpenVault\\OpenMW\\openmw\\openvault\\vault\\providers.py PROVIDER_CATALOG is the free/freemium register.
- Control already GET OpenVault /api/healthz + /api/usage (priced=false). Keys never leave OpenVault. Control /v1/secrets 405.
- HT5 CLEARED 2026-09-04; epic #18 CLOSED. HARD_DENY export_secrets (F27/F28). Control must not render key values.
- DR-0012: OpenVault holds MCP credentials + leave gate, not schemas. Netie-KB :8030 is the skill registry. Cortex stirs invoke.
- Sale: F14/F19 experience packs, Stripe simulate. Control is not a customer SKU.

## Routing

| Item | Call |
|---|---|
| Free API register on Control | KEEP providers.py. Control display only. |
| Leak check as Control product | REFUSE. HT5 already CLEARED. Do not reopen #18. Cross-space leak is DMS EPIC-003. |
| MCP store in Control | REFUSE. Credentials stay here; schemas KB; invoke Cortex. |
| Sale / marketplace on Control | REFUSE Control SKU. KEEP F14/F19. Customer store is AirGPT F90 NEEDS-YOU. |
| Signed-in secrets in Control | REFUSE. Cortex GRANT #202-#205 QUEUED. |

**Ack only. No OpenVault ticket.** Serves: Control F12.

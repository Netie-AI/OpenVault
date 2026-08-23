# PRODUCT_ROLES — Netie surface contract

**Canonical split.** Do not grow a third orchestrator or a second key vault.
Shared across Cortex · OpenVault · AirGPT · FreeIDE. Keep this file identical.

---

## Naming (2026-07-27)

The products are the **Free\*** family. OpenVault keeps its name — it is the
custody root, and its identifiers are load-bearing.

| Was | Now | Scope of the rename |
|-----|-----|---------------------|
| OpenIDE | **FreeIDE** | display + routes |
| OpenShip | **FreeBuild** | display + routes |
| OpenFree | **FreeRoute** | display + routes |
| OpenVault | **OpenVault** (unchanged) | — |

Renamed: product names in docs and UI, and the canonical URL paths
(`/api/freeide/*`, `/api/freebuild*`, `/api/freeroute/*`). The old paths stay
registered as hidden aliases so shipped clients keep working; they are absent
from the OpenAPI schema and are pinned by `tests/test_access_routing.py`.

**Deliberately not renamed** — these are storage and wire identifiers, and
changing them breaks live installs: Python modules (`openmw.openvault.ship`,
`ship/openship.py`), class names (`OpenShipPlan`, `OpenShipClient`), env vars
(`OPENSHIP_API_TOKEN`, `OMNIROUTE_API_KEY`, `OPENVAULT_HOME`, `OPENVAULT_URL`),
the data directory `~/.openvault`, and the reveal header `X-OpenVault-Reveal`.
OmniRoute stays as attribution — FreeRoute is OmniRoute-inspired, not a rename
of it.

---

## Roles

| Surface | Job | Not its job |
|---------|-----|-------------|
| **Cortex** | Central brain — MoE, pick architecture (DAG / sequential / LangGraph-style / minimal / RAG / memory / computer-control), orchestrate, optimize | Storing keys, one-click deploy UX |
| **OpenVault** | Safe manager + final shipper — where things live, model keys, one-click connect APIs + local ground models, gating, **FreeBuild** deploy/host (Vercel-easy, cheap/free tiers, mail), free-gateway routing for **FreeRoute** | Running the agent loop itself |
| **FreeIDE** | Standalone coding app — activates coding expert slice of brain (TSX/canvas, tools, web search, FS, PRs) | Being the host/deploy console |
| **AirGPT** | Standalone host shell / control plane — phone, settings, pairing, apps hub; thin bridge to Cortex + OpenVault | Owning orchestration forever; owning the Windows front-door chat UI |
| **Netie Space** | Windows front door — file preview + file-named chats; starts Cortex + OpenVault + AirGPT backend together | Replacing OpenVault custody or Cortex brains |

---

## Safe path (manager + shipper)

```
App (FreeIDE / AirGPT / …)
    → asks Cortex (orchestration only)
        → Cortex plans / MoE / architecture preset
            → OpenVault: resolve where thing lives + keys + gate
                → retrieve / run / deploy
            ← OpenVault ships only what passed gate
        ← Cortex continues under ledger / write gate
```

**Omni-retrieve without OpenVault as gate = unsafe.**
Cortex thinks; OpenVault knows location + keys + “may this leave / deploy?”

---

## Rule of thumb

| Need | Go to |
|------|--------|
| Brains / architecture / MoE / agent loop | **Cortex** |
| Keys / where-is-it / connect / deploy / host / gate | **OpenVault** |
| Code workspace / PRs / FS tools | **FreeIDE** |
| Phone / settings chrome / pairing / apps hub | **AirGPT** |
| Windows file preview + file-named chat UI | **Netie Space** |

---

## Already half-there (honest)

- **AirGPT** has `/api/openvault/*` + Key Vault UI + **FreeRoute** (our free gateway; OmniRoute-inspired) + `/api/hosting*` — these must stay **thin clients** of OpenVault (and Cortex for engine brains), not a second custody/orchestrator home.
- **FreeBuild** is owned by OpenVault (`openmw.openvault.ship`) — AirGPT/FreeIDE request ship; OpenVault plans/gates/executes. Do not re-home FreeBuild under AirGPT.
- **OpenVault** today also ships NVMe/mesh/profiler measurement; the **product target** for this contract is keys + gate + connect + deploy/host (baby-easy). Measurement stays adjacent, not a competing product story.
- **Cortex** must MoE-pick architecture presets (DAG vs LangGraph vs minimal vs RAG…). Do **not** add a third orchestrator alongside `dag_runner` / AirGPT queue — extend the surviving Cortex path.

---

## Ownership locks

1. **Keys SoT** = OpenVault encrypted vault (`openmw console` / `/api/keys*`). AirGPT `env.local` and Netie `user.env` are at most offline caches synced from OpenVault — never a second vault.
   **Passwords and payment cards** live in the same vault under `/api/secrets*` (`vault/secrets.py`) — same `keys.db`, same master key, same reveal gate. Cards never leave OpenVault: no shell caches a PAN to disk, and CVV is never stored at all. See [`docs/SECRETS_CUSTODY.md`](docs/SECRETS_CUSTODY.md).
2. **Architecture preset SoT** = Cortex (`architecture_preset` on engine config). OpenVault may persist *model slot* preferences (`/api/orchestration/selection`) but does not pick DAG vs LangGraph.
3. **Deploy / leave-machine gate** = OpenVault. Cortex/AirGPT/FreeIDE request; OpenVault allows or denies.
4. **Coding expert activation** = FreeIDE asks Cortex; FreeIDE does not host deploy console UX.
5. **No third orchestrator. No second key vault.**
6. **Access routing** = OpenVault (`/api/access/*`, `route/access.py`). It answers
   *where does this live, who owns it, and may this caller go* — for memory, provider
   APIs, components, runtimes, models, and the Free\* services. It returns a location
   plus a gate verdict and **never the content**. Memory itself stays in Cortex
   (`/api/memory/*`); a resolve that started returning memory rows would be the second
   store lock 5 forbids.

When in doubt: brains → Cortex · custody/ship → OpenVault · code → FreeIDE · shell → AirGPT.

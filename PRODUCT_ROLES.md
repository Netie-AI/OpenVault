# PRODUCT_ROLES — Netie surface contract

**Canonical split.** Do not grow a third orchestrator or a second key vault.
Shared across Cortex · OpenVault · AirGPT · OpenIDE. Keep this file identical.

---

## Roles

| Surface | Job | Not its job |
|---------|-----|-------------|
| **Cortex** | Central brain — MoE, pick architecture (DAG / sequential / LangGraph-style / minimal / RAG / memory / computer-control), orchestrate, optimize | Storing keys, one-click deploy UX |
| **OpenVault** | Safe manager + final shipper — where things live, model keys, one-click connect APIs + local ground models, gating, **OpenShip** deploy/host (Vercel-easy, cheap/free tiers, mail), free-gateway routing for **OpenFree** | Running the agent loop itself |
| **OpenIDE** | Standalone coding app — activates coding expert slice of brain (TSX/canvas, tools, web search, FS, PRs) | Being the host/deploy console |
| **AirGPT** | Standalone host shell / control plane — phone, settings, pairing, apps hub; thin bridge to Cortex + OpenVault | Owning orchestration forever |

---

## Safe path (manager + shipper)

```
App (OpenIDE / AirGPT / …)
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
| Code workspace / PRs / FS tools | **OpenIDE** |
| Phone / settings chrome / pairing / apps hub | **AirGPT** |

---

## Already half-there (honest)

- **AirGPT** has `/api/openvault/*` + Key Vault UI + **OpenFree** (our free gateway; OmniRoute-inspired) + `/api/hosting*` — these must stay **thin clients** of OpenVault (and Cortex for engine brains), not a second custody/orchestrator home.
- **OpenShip** is owned by OpenVault (`openmw.openvault.ship`) — AirGPT/OpenIDE request ship; OpenVault plans/gates/executes. Do not re-home OpenShip under AirGPT.
- **OpenVault** today also ships NVMe/mesh/profiler measurement; the **product target** for this contract is keys + gate + connect + deploy/host (baby-easy). Measurement stays adjacent, not a competing product story.
- **Cortex** must MoE-pick architecture presets (DAG vs LangGraph vs minimal vs RAG…). Do **not** add a third orchestrator alongside `dag_runner` / AirGPT queue — extend the surviving Cortex path.

---

## Ownership locks

1. **Keys SoT** = OpenVault encrypted vault (`openmw console` / `/api/keys*`). AirGPT `env.local` is at most an offline cache synced from OpenVault — never a second vault.
2. **Architecture preset SoT** = Cortex (`architecture_preset` on engine config). OpenVault may persist *model slot* preferences (`/api/orchestration/selection`) but does not pick DAG vs LangGraph.
3. **Deploy / leave-machine gate** = OpenVault. Cortex/AirGPT/OpenIDE request; OpenVault allows or denies.
4. **Coding expert activation** = OpenIDE asks Cortex; OpenIDE does not host deploy console UX.
5. **No third orchestrator. No second key vault.**

When in doubt: brains → Cortex · custody/ship → OpenVault · code → OpenIDE · shell → AirGPT.

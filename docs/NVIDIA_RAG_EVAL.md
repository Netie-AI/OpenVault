# NVIDIA NIM catalog note (OpenVault EPIC-OV-NVIDIA / issue #12)

**Date:** 2026-08-03  
**Change:** `ProviderSpec id=nvidia` added to `PROVIDER_CATALOG` (base `https://integrate.api.nvidia.com/v1`).  
Env aliases: `NVIDIA_API_KEY`, `NVIDIA_NIM_API_KEY`, `FREENVIDIA_API_KEY`.  
Key shape `nvapi-` already inferred in `inferProvider.ts`.

## Fit for AirGPT Excel/RAG retrieval + reasoning

| Use | Fit | Notes |
|-----|-----|-------|
| Structured Top-N / KPI (hybrid SQL) | N/A | Deterministic pandas lane — model-agnostic |
| Ontology bind + short logic narration | Good | 8B–70B instruct on NIM is enough |
| Messy multi-sheet free-form Q | Good (70B / Nemotron) | Prefer larger instruct; verify latency/cost |
| Salary amend confirm copy | Good | Small model OK; governance is the confirm gate |
| vs Mistral small | Mistral fine for cheap narration; NVIDIA 70B stronger on multi-hop reasoning |

**Recommend:** catalog + probe now; AirGPT consume after vault-first / P7 unlock. Do not make NVIDIA default for demo until probe passes on founder key.

## Probe

```powershell
# After OpenVault restart, paste nvapi- key in Key Vault UI — should not say "not in the catalog"
# Optional: curl integrate.api.nvidia.com/v1/models with Bearer nvapi-…
```

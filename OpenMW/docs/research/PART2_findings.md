# PART 2 Research — R-2 Model Size / VRAM Mapping

## Core VRAM formula (2026 consensus)

```
weights_gb = (params_B × quant_bits / 8) × overhead_factor
kv_cache_gb = (2 × layers × kv_heads × head_dim × ctx_tokens × bytes_per_kv) / 1e9
total_vram_gb = weights_gb + kv_cache_gb + framework_overhead_gb
```

**OpenMW PART 2 implementation formula (from master plan):**

```
vram_needed = (params_B × quant_bits / 8) × 1.4
            + (ctx_tokens / 1024 × kv_mb_per_1k)
```

Where `kv_mb_per_1k ≈ 144 MB` for 8B-class models (GQA, FP16 KV), scaling with architecture.

### Overhead factor

Sources disagree on multiplier: 1.15–1.25 (CraftRigs) vs **1.4** (OpenMW plan, conservative for vLLM/CUDA scratch).
PART 2 will use **1.4** as specified — better to under-recommend than OOM.

### Reference data points (Q4_K_M ≈ 4.5 bits effective)

| Model class | Q4_K_M weights | FP16 weights | Notes |
|-------------|----------------|--------------|-------|
| 7–8B | ~5.5 GB | ~14 GB | `(8 × 4.5/8) × 1.4 ≈ 6.3`; plan cites ~5.5 with tighter quant |
| 14B | ~10–11 GB | ~28 GB | Sweet spot RTX 4080/4090 @ 60–70 tok/s |
| 70B | ~39 GB | ~140 GB | Needs dual 24 GB or Apple unified ≥128 GB |

### KV cache overhead

For Llama-3.1 8B (32 layers, 8 kv_heads, head_dim 128, FP16 KV):

```
kv_per_token_bytes = 2 × 32 × 8 × 128 × 2 = 131,072 bytes ≈ 128 KB/token
```

| Context | 8B KV (FP16) | MB per 1024 tokens |
|---------|--------------|-------------------|
| 4K | ~0.5 GB | ~128 MB |
| 8K | ~1.0 GB | ~128 MB |
| 32K | ~4.0 GB | ~128 MB |

Plan's **144 MB / 1024 tokens** for 8B aligns with measured GQA tables (InsiderLLM, llm-vram-calculator).

KV quant (Q8/Q4 cache) cuts KV term 50–75% — ties to existing `kv_quant.py` / `key_channel_quant.py`.

## 5-tier routing table (PART 2)

`tier` (min_tier) = lowest hardware tier the model can run on with GPU/CPU/NVMe offload.
`comfortable_tier` = tier where the model fits entirely on GPU per `estimate_vram_gb()` at
Q4_K_M and 4096 ctx (see `models.json`).

| Tier | VRAM range | FP16 fits (comfortable) | Q4_K_M fits (comfortable) | Target tok/s | Example models (min → comfortable) |
|------|------------|-------------------------|---------------------------|--------------|-------------------------------------|
| **NANO** | 0–6 GB (CPU) | 3B | 7B | 8–20 | Phi-4-Mini, Gemma-3-2B |
| **SMALL** | 6–12 GB | 3B | 7–8B | 40–60 | Qwen3.5-9B, Llama-3.3-8B |
| **MID** | 12–16 GB | 7B | 13–14B | 55–70 | qwen2.5-14b, llama-3.1-13b; Mistral-24B runs here **with offload** (comfortable: LARGE) |
| **LARGE** | 16–24 GB | 13B | 22–27B | 35–50 | Codestral-22B; Qwen3-32B / Yi-34B run with offload (comfortable: XLARGE) |
| **XLARGE** | 24+ GB discrete **or** ≥48 GB unified | 27B | up to ~32B on GPU | 15–40 | Llama-3.3-70B needs **≥48 GB unified or dual-GPU / heavy offload** — not a single 24 GB card at Q4 |

**Registry overrides (comfortable_tier > tier):** `mistral-small-3.1-24b` MID→LARGE;
`qwen3-32b` LARGE→XLARGE; `yi-34b` LARGE→XLARGE. All other entries: `comfortable_tier == tier`.

## Offload split (PART 2 preview)

```
gpu_layers = floor(gpu_vram_gb / layer_vram_gb)
remaining → cpu_ram_layers → nvme_layers
```

Layer VRAM derived from `params_B / num_layers × quant_bytes × overhead`.

## Sources

- CraftRigs VRAM calculator, PCPARTGUIDE 2026 table, collindjohnson/llm-vram-calculator (GQA-exact KV)
- InsiderLLM KV cache optimization guide (per-model tables)
- OpenMW master plan PART 2 spec

---

## Appendix — PART 2 PRE-FLIGHT (implementation)

Implemented in `openmw/model_router.py` + `openmw/data/models.json`.

| Item | Decision |
|------|----------|
| **VRAM formula** | `vram_needed = (params_B × quant_bits / 8) × 1.4 + (ctx_tokens / 1024 × kv_mb_per_1k)`; `kv_mb_per_1k = 144 × params_B / 8` |
| **Quant bits** | Q4_K_M=4.5, Q5_K_M=5.5, Q8_0=8, FP16=16 (effective bits/param) |
| **Offload split** | `gpu_layers = floor((gpu_vram − kv − 1 GB) / layer_vram)`; remainder → CPU RAM → NVMe |
| **Unified memory** | Apple pool = `system_ram_gb × 0.9`; all fit layers on accelerator, overflow → NVMe |
| **CPU-only** | `gpu_layers=0`; RAM first (`ram × 0.75`), then NVMe |
| **KV quant bits** | Recommended: value=4, key=2 (`KvQuantConfig` defaults); else FP16 (16/16) |
| **Registry** | 20 models, 4 per tier; fields: `tier` (min), `comfortable_tier`, `params_B`, `layers`, `quant_options`, `download_url`, `license` |
| **Default ctx** | 4096 tokens (configurable via `ModelRouter(ctx_tokens=…)`) |

### Issues flagged for PART 3

- Throughput estimate is tier-heuristic only — no measured tok/s calibration yet.
- MoE models use active-param `params_B`; expert count not modeled separately.
- Layer split ignores PCIe/NVMe latency; PART 3 prefetch window should refine `nvme_layers` cost.
- Registry URLs are HuggingFace landing pages — PART 3+ may need pinned GGUF filenames + SHA256.

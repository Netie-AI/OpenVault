# PART 3 Research — R-4 PowerInfer + R-5 Flash Windowing

## R-4 — PowerInfer (arXiv:2312.12456)

### Mechanism

PowerInfer exploits **activation locality**: a power-law distribution where ~10–20% of neurons ("hot")
activate on most tokens; the rest ("cold") are input-dependent.

**Offline phase:**

1. Profile neuron activation frequencies on calibration data.
2. Solve an ILP placement policy — hot neurons → GPU, cold → CPU/DRAM.
3. Build per-layer **online predictors** (small MLPs) to forecast active neurons per token.

**Runtime:**

- GPU executes hot + predicted-active cold neurons locally.
- Unpredicted cold neurons computed on CPU; results streamed via PCIe only when needed.
- Extends llama.cpp (~4,200 LOC C++/CUDA); neuron tables ~9 MB for OPT-175B scale.

### Python API?

**No runtime Python API.** Inference is C++/CUDA binaries (`main`, `server`).

Python appears only in **offline tooling**:

```bash
python3 PowerInfer/solver/solve.py \
  --model ./model.gguf \
  --output ./predictors/model \
  --target-gpu-layers 20 \
  --gpu-memory-gb 16
```

Serving uses `./build/bin/server --predictor-path ...` (OpenAI-compatible HTTP).

### Implication for OpenMW PART 3

OpenMW must **reimplement a simplified predictor** in numpy (or optional torch) for the
`SparsityPrefetchConfig` calibration pass — not wrap PowerInfer directly. Algorithm reference:

- Calibration: 512 sample tokens, mark neurons active in >80% of samples as "hot".
- Runtime: predict active subset, prefetch weight chunks for predicted neurons only.

## R-5 — LLM in a Flash (arXiv:2312.11514)

### Mechanism

Apple Research technique for models exceeding DRAM:

1. **Windowing** — reuse recently activated neurons; only fetch new activations from flash.
2. **Row-column bundling** — align reads to flash page boundaries (128 KB typical) for sequential bandwidth.
3. **Sparsity-aware loading** — skip inactive weight regions.

Reported: run models **2× DRAM size**; 4–5× (CPU) / 20–25× (GPU) vs naive flash loading.

### MLX implementation status

**Not in upstream `ml-explore/mlx` core.** Official stack is `mlx-lm` (in-memory / quant models on Apple Silicon).

Community implementation: **`matt-k-wong/mlx-flash`** — Flash weight streaming for MLX inspired by the paper:

```python
from mlx_flash import FlashConfig, FlashManager
manager = FlashManager(FlashConfig(ram_budget_gb=2.0))
model, tokenizer = manager.load("mlx-community/Meta-Llama-3-70B-Instruct-4bit")
```

OpenMW PART 3 Strategy A (flash-window prefetch) will implement the **bundling + LRU window**
pattern in Python for LMCache disk config — aligned with `prefetch_naive.py` extension,
not dependent on mlx-flash (CUDA/Linux primary path).

### Flash window parameters (PART 3 preview)

```
window_size = f(nvme_seq_read_gbps, gpu_bandwidth_gbps)
chunk_size  = 128 KiB  # NVMe page alignment default
```

## Combined prefetch strategy map

| Strategy | Source paper | OpenMW module | Dependency |
|----------|--------------|---------------|------------|
| Sequential disk prefetch | LMCache baseline | `prefetch_naive.py` | Done |
| Heuristic overlay | Internal | `prefetch_heuristic.py` | Done |
| Flash window | LLM-in-a-Flash | `prefetch_flash.py` (PART 3) | numpy only |
| Neuron sparsity | PowerInfer | `prefetch_sparsity.py` (PART 3) | numpy calibration |

## Sources

- arXiv:2312.12456 (PowerInfer), arXiv:2312.11514 (LLM in a Flash)
- github.com/SJTU-IPADS/PowerInfer, powerinfer.ai
- Apple ML Research blog, matt-k-wong/mlx-flash, ml-explore/mlx-lm

## PART 3 Implementation Appendix (2026-06-18)

### PRE-FLIGHT decisions

| Item | Decision |
|------|----------|
| Calibration runtime | Pure **numpy** — no torch dependency for `SparsityPrefetcher` calibration |
| `FlashWindowConfig` | `window_size`, `chunk_size_kb` (128), `lru_k`, `nvme_page_kb`, `prefetch_ahead_chunks` |
| `SparsityPrefetchConfig` | `hot_threshold` (0.80), `calibration_tokens` (512), `layer_count`, `prefetch_batch_size` |
| LMCache integration | `lmcache_disk_config()` adds `flash_window` / `sparsity_prefetch` keys **only when enabled** |

### Modules shipped

- `openmw/prefetch_flash.py` — `FlashWindowPrefetcher`, LRU-K hot window, bandwidth-derived `window_size`
- `openmw/prefetch_sparsity.py` — `SparsityPrefetcher`, `HotNeuronIndex`, numpy calibration pass
- `openmw/prefetch_naive.py` — extended `lmcache_disk_config(flash=, sparsity=)`

### Window-size formula

```
window_size = clamp(ceil(gpu_bandwidth_gbps / nvme_seq_read_gbps * prefetch_ahead_chunks), 4, 128)
```

Explicit `FlashWindowConfig.window_size` overrides profile-derived value.

### Issues for PART 4

- **Runtime hook**: modules are config-only; LMCache/vLLM connector must consume `flash_window` / `sparsity_prefetch` sections at inference time.
- **Real calibration data**: tests use synthetic activations; production needs model-weight–specific calibration token stream.
- **Chunk ↔ neuron mapping**: sparsity prefetch returns neuron ids; PART 4 must map neurons to NVMe byte ranges in weight tensors.
- **Strategy selection**: `ModelRouter` does not yet pick flash vs sparsity vs heuristic; needs offload-tier policy.
- **IORing overlap**: Windows `windows_ioring_spike` is exploratory; flash-window async I/O may need OS-specific backend.

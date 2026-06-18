# PART 5 Research — R-3 Unsloth + vLLM Integration

## Integration surface

Unsloth does **not** embed vLLM inside `FastLanguageModel` as a generic HF model wrapper.
The supported path is **export merged weights → `vllm serve`**.

### (a) `fast_inference=True`

When set in `FastLanguageModel.from_pretrained(...)`, Unsloth loads a **vLLM engine in-process**
for high-throughput inference during RL rollouts (GRPO/PPO), sharing weight memory with the
training LoRA adapter.

```python
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen3-8B-Base",
    max_seq_length=2048,
    load_in_4bit=False,
    fast_inference=True,
    gpu_memory_utilization=0.95,
)
```

**Constraints:**

- Requires CUDA GPU; unusable on CPU-only machines.
- Cannot pass a live HF model object to `vllm.LLM(model=llm)` — save to disk first (vLLM #9509).

### (b) `for_inference()` / `for_training()`

Mode switches that reconfigure the model for inference vs training kernels:

- Call `FastLanguageModel.for_inference(model)` before generation benchmarks.
- Call `FastLanguageModel.for_training(model)` before resuming fine-tune / next RL epoch.
- After GRPO/RL, **always** call `for_training()` before the next training step.

Unsloth native inference (non-vLLM) is ~2× faster than stock HF when `for_inference()` is set.

### (c) `UNSLOTH_VLLM_STANDBY`

Environment variable (set **before** any Unsloth import):

```python
import os
os.environ["UNSLOTH_VLLM_STANDBY"] = "1"
```

**Purpose:** In RL loops alternating inference → training → inference, reclaim vLLM KV cache
memory during training while **preserving shared weight storage**. Unsloth's extension of vLLM
"sleep mode" — deletes KV cache, not LoRA weights.

**When to set:** GRPO/PPO/RL training with `fast_inference=True`. Pair with
`gpu_memory_utilization=0.95` (standby intentionally targets high util for faster rollouts).

**Caveats:** Known OOM bugs on some model/GPU combos; requires recent unsloth main (2026.1+).
Also available as `unsloth_vllm_standby=True` kwarg to `from_pretrained`.

### (d) LoRA + vLLM serving

**Merged weights (simplest):**

```python
model.save_pretrained_merged("finetuned", tokenizer, save_method="merged_16bit")
# vllm serve finetuned
```

**LoRA adapters (hot-swap):**

```bash
vllm serve base_model --enable-lora --lora-modules adapter_name=path_to_adapter
```

Runtime load: `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` + `/v1/load_lora_adapter`.

**Constraints:**

- Vision LoRA layers **cannot** be served via vLLM — use transformers backend.
- Adapter rank must match `max_lora_rank` at load time.

### Export to GGUF (OpenMW pipeline)

```python
model.save_pretrained_gguf(path, tokenizer, quantization_method="q4_k_m")
```

## OpenMW PART 5 wrapper plan

| Function | Backing |
|----------|---------|
| `unsloth_load()` | `FastLanguageModel.from_pretrained` + optional `fast_inference` |
| `unsloth_finetune()` | SFTTrainer / TRL with `for_training()` |
| `unsloth_to_vllm()` | `save_pretrained_merged(..., "merged_16bit")` + serve instructions |

All Unsloth imports behind `try: import unsloth` → `UnslothNotAvailable`.

## Sources

- unsloth.ai/docs — vLLM guide, fine-tuning guide, memory-efficient RL (Standby)
- HuggingFace TRL unsloth integration docs
- vllm-project/vllm#9509, unslothai/unsloth#3302/#3512/#3542

---

## Appendix — PART 5 PRE-FLIGHT (implementation)

| Item | Decision |
|------|----------|
| **Optional dep** | Unsloth is **not** a required dependency. All imports behind `try: import unsloth` → `UnslothNotAvailable`. Install manually on CUDA hosts: `uv pip install unsloth`. |
| **LoRA defaults** | `lora_r=16`, `lora_alpha=16`, `lora_dropout=0`, `target_modules=["q_proj","v_proj"]` in `TrainingConfig` |
| **Training data** | Alpaca-style JSON list: `{"instruction": "...", "output": "..."}` |
| **VRAM gate** | `validate_training_profile()` requires ≥ 8 GB effective VRAM for ~7B @ 4-bit LoRA |
| **Export** | `export_gguf(..., quant="q4_k_m")` → `model.save_pretrained_gguf` → `register_local()` |
| **vLLM path** | `unsloth_to_vllm()` → `save_pretrained_merged(..., "merged_16bit")` + `vllm serve` hint |
| **RL standby** | Document `UNSLOTH_VLLM_STANDBY=1` (or `unsloth_vllm_standby=True`) before import; pair with `for_training()` / `for_inference()` mode switches |
| **Tests** | Fully mocked Unsloth/TRL; `pytest tests/test_unsloth_bridge.py` (no GPU) |

### Modules shipped

- `openmw/training_config.py` — `TrainingConfig` pydantic model
- `openmw/unsloth_bridge.py` — `unsloth_load`, `unsloth_finetune`, `export_gguf`, `unsloth_to_vllm`, `validate_training_profile`

### Training pipeline (supported)

1. `DeviceProfile` → `validate_training_profile(profile)` (≥ 8 GB VRAM)
2. `ModelRouter.route(profile, model_id)` → select base model
3. `unsloth_load(model_id, load_in_4bit=True)`
4. `unsloth_finetune(session, dataset_path, config=TrainingConfig())`
5. `export_gguf(session, output_path, quant="q4_k_m")`
6. `model_manager.register_local(model_id, "Q4_K_M", output_path)`

### Issues for PART 6

- **vLLM launcher**: merged export path documented but not wired into an OpenMW serve CLI yet.
- **Real GPU smoke test**: CI has no CUDA; LoRA fine-tune correctness unverified on hardware.
- **Dataset validation**: only Alpaca `{instruction, output}` supported; no ShareGPT / chat-template variants.
- **HF base-model IDs**: registry stores GGUF URLs; Unsloth load expects HF model ids (caller must map).
- **TRL / datasets**: pulled in only at fine-tune runtime when Unsloth is installed — not declared as OpenMW deps.

# PART 4 Research — R-6 Model Auto-Download + GGUF Manager

## Hugging Face download stack

OpenMW uses **`huggingface_hub`** (`hf_hub_download`, `HfApi`) for GGUF retrieval:

- **Resume**: hub client resumes partial downloads via HTTP range requests and local cache
  metadata (etag / commit hash). No custom resume logic required.
- **Progress**: callback receives `0.0` before download and `100.0` after verification; hub
  tqdm remains enabled in the terminal when no callback is supplied.
- **Integrity**: LFS siblings expose `BlobLfsInfo.sha256`; post-download hash is compared
  before the file is promoted into `~/.openmw/models/`.

## Storage layout

```
~/.openmw/models/<model_id>/<quant_level>.gguf
~/.openmw/models/<model_id>/metadata.json
```

`metadata.json` tracks per-quant `hf_filename`, `repo_id`, `sha256`, `size_bytes`, and
`downloaded_at`. `register_local()` copies user-supplied GGUFs into the same layout.

## License gate

| License class | Behaviour |
|---------------|-----------|
| MIT, Apache-2.0 | Download without prompting |
| Llama, Gemma, Mistral, DeepSeek, … | Raise `LicenseGateError` with HF_TOKEN guidance |

Registry `license` field is checked **before** any network I/O. `GatedRepoError` from the
hub is mapped to the same error for defence in depth.

## Auto-select policy

`auto_select_and_download(profile)`:

1. `ModelRouter.tier_for_profile(profile)` → hardware tier
2. Tier-default model id (Apache/MIT preference: SMALL → `qwen3.5-9b`, XLARGE → `qwen2.5-72b`)
3. `ModelRouter.route(profile, model_id)` → quant level + offload plan
4. `download()` if the quant file is missing or fails verification

## GGUF filename resolution

Registry `download_url` values are repo landing pages. At download time:

1. `HfApi.list_repo_files(repo_id)` → `*.gguf` siblings
2. Case-insensitive match on quant token (`Q4_K_M`, `q4_k_m`, …)
3. Size + SHA-256 from `repo_info(files_metadata=True)`

**Gap (PART 5+)**: repos with multiple quant builds per file naming scheme may need pinned
`hf_filename` overrides in `models.json`.

## Disk space pre-check

`shutil.disk_usage` on the models root; requires `file_size + 512 MiB` headroom before
starting the hub download.

## Sources

- huggingface/huggingface_hub — `hf_hub_download`, `HfApi.repo_info`, LFS metadata
- OpenMW master plan PART 4 spec
- PART 2 registry (`openmw/data/models.json`)

---

## Appendix — PART 4 PRE-FLIGHT (implementation)

| Item | Decision |
|------|----------|
| **Library** | `huggingface_hub>=1.20` via `uv add huggingface_hub` |
| **Integrity** | SHA-256 of local GGUF vs LFS `BlobLfsInfo.sha256`; mocked in tests |
| **Storage** | `~/.openmw/models/<model_id>/<quant_level>.gguf` + `metadata.json` |
| **Resume** | Delegated to `huggingface_hub` HTTP range resume |
| **Progress** | Optional `Callable[[float], None]` (0–100); `0` at start, `100` on completion |
| **License gate** | MIT/Apache silent; gated licenses → `LicenseGateError` |
| **Disk check** | `required_bytes + 512 MiB` free before download |
| **Auto-select** | Tier-default model + `ModelRouter.route()` quant |
| **Tests** | All HF/network calls mocked; `pytest tests/test_model_manager.py` |

### Modules shipped

- `openmw/model_manager.py` — `download`, `verify`, `list_local`, `delete`,
  `auto_select_and_download`, `register_local`

### Issues for PART 5

- **Pinned filenames**: registry URLs only; multi-file repos need explicit `hf_filename` per quant.
- **HF token flow**: no CLI helper to run `huggingface-cli login`; WebUI must surface token setup.
- **Inference hook**: downloaded paths are not yet wired into vLLM/LMCache launcher (PART 5+).
- **Chunk ↔ neuron mapping** (from PART 3): still open — sparsity prefetch needs GGUF tensor layout.
- **Strategy selection**: router does not yet choose flash vs sparsity prefetch for offload tiers.

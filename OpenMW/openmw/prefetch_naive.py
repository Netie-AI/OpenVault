"""Phase-1 naive prefetch: fetch next N KV blocks during decode."""

from __future__ import annotations

from dataclasses import dataclass

from openmw.kv_quant import KvQuantConfig
from openmw.prefetch_flash import FlashWindowConfig
from openmw.prefetch_sparsity import SparsityPrefetchConfig


@dataclass(frozen=True)
class NaivePrefetchConfig:
    """Configuration for sequential next-block prefetch."""

    enabled: bool = False
    prefetch_blocks: int = 4
    block_size_bytes: int = 256 * 1024


def lmcache_disk_config(
    config: NaivePrefetchConfig,
    cache_dir: str,
    *,
    kv_quant: KvQuantConfig | None = None,
    flash: FlashWindowConfig | None = None,
    sparsity: SparsityPrefetchConfig | None = None,
) -> dict[str, object]:
    """Return LMCache-compatible disk backend config dict (template)."""
    result: dict[str, object] = {
        "backend": "disk",
        "path": cache_dir,
        "prefetch": {
            "enabled": config.enabled,
            "lookahead_blocks": config.prefetch_blocks,
            "block_size_bytes": config.block_size_bytes,
        },
    }
    if kv_quant is not None and kv_quant.enabled:
        result["kv_quant"] = {
            "enabled": True,
            "bits": kv_quant.bits,
            "backend": kv_quant.backend,
            "target": "value_cache",
        }
    if kv_quant is not None and kv_quant.key_quant_enabled:
        result["key_quant"] = {
            "enabled": True,
            "bits": kv_quant.k_bits,
            "group_size": kv_quant.group_size,
            "residual_length": kv_quant.residual_length,
            "target": "key_cache",
            "backend": "kivi-clean-room",
        }
    if flash is not None and flash.enabled:
        result["flash_window"] = {
            "enabled": True,
            "window_size": flash.window_size,
            "chunk_size_kb": flash.chunk_size_kb,
            "nvme_page_kb": flash.nvme_page_kb,
            "lru_k": flash.lru_k,
            "prefetch_ahead_chunks": flash.prefetch_ahead_chunks,
        }
    if sparsity is not None and sparsity.enabled:
        result["sparsity_prefetch"] = {
            "enabled": True,
            "hot_threshold": sparsity.hot_threshold,
            "calibration_tokens": sparsity.calibration_tokens,
            "layer_count": sparsity.layer_count,
            "prefetch_batch_size": sparsity.prefetch_batch_size,
        }
    return result


def vllm_offload_env(config: NaivePrefetchConfig) -> dict[str, str]:
    """Environment variables for vLLM OffloadingConnector + disk tier."""
    env: dict[str, str] = {
        "VLLM_CPU_OFFLOAD_GB": "8",
    }
    if config.enabled:
        env["OPENMW_NAIVE_PREFETCH"] = "1"
        env["OPENMW_PREFETCH_BLOCKS"] = str(config.prefetch_blocks)
    return env

"""Phase-1 naive prefetch: fetch next N KV blocks during decode."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NaivePrefetchConfig:
    """Configuration for sequential next-block prefetch."""

    enabled: bool = False
    prefetch_blocks: int = 4
    block_size_bytes: int = 256 * 1024


def lmcache_disk_config(config: NaivePrefetchConfig, cache_dir: str) -> dict[str, object]:
    """Return LMCache-compatible disk backend config dict (template)."""
    return {
        "backend": "disk",
        "path": cache_dir,
        "prefetch": {
            "enabled": config.enabled,
            "lookahead_blocks": config.prefetch_blocks,
            "block_size_bytes": config.block_size_bytes,
        },
    }


def vllm_offload_env(config: NaivePrefetchConfig) -> dict[str, str]:
    """Environment variables for vLLM OffloadingConnector + disk tier."""
    env: dict[str, str] = {
        "VLLM_CPU_OFFLOAD_GB": "8",
    }
    if config.enabled:
        env["OPENMW_NAIVE_PREFETCH"] = "1"
        env["OPENMW_PREFETCH_BLOCKS"] = str(config.prefetch_blocks)
    return env

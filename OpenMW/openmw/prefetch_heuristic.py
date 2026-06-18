"""Phase-2 heuristic prefetch config (Comet/SpeCache-inspired, config-only)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HeuristicPrefetchConfig:
    """Heuristic prefetch using attention hotspot signal (research track)."""

    enabled: bool = False
    hotspot_threshold: float = 0.75
    max_prefetch_blocks: int = 8
    cooldown_ms: int = 50


def lmcache_heuristic_overlay(base: dict[str, object], config: HeuristicPrefetchConfig) -> dict[str, object]:
    """Merge heuristic prefetch settings into LMCache config."""
    merged = dict(base)
    raw_prefetch = merged.get("prefetch")
    prefetch: dict[str, object] = dict(raw_prefetch) if isinstance(raw_prefetch, dict) else {}
    prefetch.update(
        {
            "heuristic_enabled": config.enabled,
            "hotspot_threshold": config.hotspot_threshold,
            "max_blocks": config.max_prefetch_blocks,
            "cooldown_ms": config.cooldown_ms,
        }
    )
    merged["prefetch"] = prefetch
    return merged

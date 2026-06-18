"""Phase-3 flash-window prefetch: NVMe page-aligned chunk streaming (LLM-in-a-Flash)."""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

from openmw.device_profile import DeviceProfile

_DEFAULT_CHUNK_SIZE_KB = 128
_DEFAULT_NVME_PAGE_KB = 128
_DEFAULT_LRU_K = 2
_DEFAULT_PREFETCH_AHEAD_CHUNKS = 2
_MIN_WINDOW_SIZE = 4
_MAX_WINDOW_SIZE = 128


@dataclass(frozen=True)
class FlashWindowConfig:
    """Flash-window prefetch aligned to NVMe page boundaries."""

    enabled: bool = False
    window_size: int | None = None
    chunk_size_kb: int = _DEFAULT_CHUNK_SIZE_KB
    lru_k: int = _DEFAULT_LRU_K
    nvme_page_kb: int = _DEFAULT_NVME_PAGE_KB
    prefetch_ahead_chunks: int = _DEFAULT_PREFETCH_AHEAD_CHUNKS


def compute_window_size(
    nvme_seq_read_gbps: float,
    gpu_bandwidth_gbps: float,
    *,
    prefetch_ahead_chunks: int = _DEFAULT_PREFETCH_AHEAD_CHUNKS,
    min_window: int = _MIN_WINDOW_SIZE,
    max_window: int = _MAX_WINDOW_SIZE,
) -> int:
    """Derive hot-window depth from NVMe vs GPU bandwidth ratio."""
    nvme = max(nvme_seq_read_gbps, 0.1)
    gpu = max(gpu_bandwidth_gbps, 0.1)
    ratio = gpu / nvme
    raw = int(math.ceil(ratio * prefetch_ahead_chunks))
    return max(min_window, min(max_window, raw))


def resolve_window_size(config: FlashWindowConfig, profile: DeviceProfile) -> int:
    """Return explicit window_size or compute from *profile* bandwidths."""
    if config.window_size is not None:
        return max(1, config.window_size)
    return compute_window_size(
        profile.nvme_seq_read_gbps,
        profile.gpu_bandwidth_gbps,
        prefetch_ahead_chunks=config.prefetch_ahead_chunks,
    )


def chunk_count_for_bytes(total_bytes: int, chunk_size_kb: int) -> int:
    """Return the number of NVMe-aligned chunks covering *total_bytes*."""
    if total_bytes < 0:
        raise ValueError(f"total_bytes must be >= 0, got {total_bytes}")
    if chunk_size_kb < 1:
        raise ValueError(f"chunk_size_kb must be >= 1, got {chunk_size_kb}")
    chunk_bytes = chunk_size_kb * 1024
    if total_bytes == 0:
        return 0
    return int(math.ceil(total_bytes / chunk_bytes))


def chunk_byte_range(chunk_index: int, chunk_size_kb: int) -> tuple[int, int]:
    """Return [start, end) byte offsets for a chunk index."""
    if chunk_index < 0:
        raise ValueError(f"chunk_index must be >= 0, got {chunk_index}")
    chunk_bytes = chunk_size_kb * 1024
    start = chunk_index * chunk_bytes
    return start, start + chunk_bytes


class _LruKTracker:
    """LRU-K hot-chunk tracker (K access timestamps per chunk)."""

    def __init__(self, *, k: int, capacity: int) -> None:
        if k < 1:
            raise ValueError(f"lru_k must be >= 1, got {k}")
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._k = k
        self._capacity = capacity
        self._access_counts: dict[int, list[int]] = {}
        self._order: OrderedDict[int, None] = OrderedDict()
        self._tick = 0

    def touch(self, chunk_id: int) -> None:
        """Record one access to *chunk_id*."""
        self._tick += 1
        history = self._access_counts.setdefault(chunk_id, [])
        history.append(self._tick)
        if len(history) > self._k:
            history.pop(0)
        if chunk_id in self._order:
            self._order.move_to_end(chunk_id)
        else:
            self._order[chunk_id] = None
            while len(self._order) > self._capacity:
                evicted = next(iter(self._order))
                del self._order[evicted]
                self._access_counts.pop(evicted, None)

    def hot_chunks(self) -> list[int]:
        """Return chunk ids with at least K recorded accesses, LRU order."""
        hot = [cid for cid, hist in self._access_counts.items() if len(hist) >= self._k]
        return [cid for cid in self._order if cid in hot]


class FlashWindowPrefetcher:
    """Page-aligned weight chunk prefetcher with LRU-K hot window."""

    def __init__(
        self,
        config: FlashWindowConfig,
        profile: DeviceProfile,
        *,
        total_weight_bytes: int,
    ) -> None:
        self._config = config
        self._chunk_size_kb = config.chunk_size_kb
        self._window_size = resolve_window_size(config, profile)
        self._total_chunks = chunk_count_for_bytes(total_weight_bytes, self._chunk_size_kb)
        window_capacity = max(self._window_size * 2, self._window_size + 4)
        self._tracker = _LruKTracker(k=config.lru_k, capacity=window_capacity)
        self._prefetch_queue: list[int] = []

    @property
    def window_size(self) -> int:
        """Configured or profile-derived hot-window depth."""
        return self._window_size

    @property
    def total_chunks(self) -> int:
        """Number of NVMe-aligned chunks for the weight tensor."""
        return self._total_chunks

    def on_chunk_requested(self, chunk_id: int) -> list[int]:
        """Record a GPU chunk request and return chunk ids to prefetch."""
        if chunk_id < 0 or chunk_id >= self._total_chunks:
            raise IndexError(f"chunk_id {chunk_id} out of range [0, {self._total_chunks})")
        self._tracker.touch(chunk_id)
        ahead: list[int] = []
        for offset in range(1, self._window_size + 1):
            nxt = chunk_id + offset
            if nxt < self._total_chunks:
                ahead.append(nxt)
        self._prefetch_queue = ahead
        return list(ahead)

    def prefetch_queue(self) -> list[int]:
        """Return the most recently computed prefetch chunk ids."""
        return list(self._prefetch_queue)

    def hot_window(self) -> list[int]:
        """Return LRU-K hot chunks currently tracked in the window."""
        return self._tracker.hot_chunks()

    def lmcache_section(self, profile: DeviceProfile) -> dict[str, object]:
        """Return LMCache flash-window config section."""
        return {
            "enabled": True,
            "window_size": resolve_window_size(self._config, profile),
            "chunk_size_kb": self._config.chunk_size_kb,
            "nvme_page_kb": self._config.nvme_page_kb,
            "lru_k": self._config.lru_k,
            "prefetch_ahead_chunks": self._config.prefetch_ahead_chunks,
        }

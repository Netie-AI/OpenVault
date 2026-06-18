"""Flash-window prefetch tests — mocked hardware, no GPU/NVMe."""

from __future__ import annotations

import pytest

from openmw.device_profile import DeviceProfile
from openmw.prefetch_flash import (
    FlashWindowConfig,
    FlashWindowPrefetcher,
    chunk_byte_range,
    chunk_count_for_bytes,
    compute_window_size,
    resolve_window_size,
)
from openmw.prefetch_naive import NaivePrefetchConfig, lmcache_disk_config
from openmw.prefetch_sparsity import SparsityPrefetchConfig


def _mock_profile(
    *,
    nvme_gbps: float = 3.5,
    gpu_gbps: float = 1008.0,
) -> DeviceProfile:
    return DeviceProfile(
        gpu_name="NVIDIA GeForce RTX 4090",
        gpu_vram_gb=24.0,
        gpu_bandwidth_gbps=gpu_gbps,
        system_ram_gb=32.0,
        cpu_cores=16,
        nvme_model="Mock NVMe",
        nvme_seq_read_gbps=nvme_gbps,
        nvme_endurance_tbw=600.0,
    )


class TestWindowSize:
    def test_compute_window_size_scales_with_bandwidth_ratio(self) -> None:
        small = compute_window_size(7.0, 100.0, prefetch_ahead_chunks=2)
        large = compute_window_size(3.5, 1008.0, prefetch_ahead_chunks=2)
        assert small < large
        assert 4 <= small <= 128
        assert 4 <= large <= 128

    def test_resolve_window_size_uses_explicit_override(self) -> None:
        cfg = FlashWindowConfig(enabled=True, window_size=16)
        assert resolve_window_size(cfg, _mock_profile()) == 16

    def test_resolve_window_size_from_profile(self) -> None:
        cfg = FlashWindowConfig(enabled=True)
        size = resolve_window_size(cfg, _mock_profile(nvme_gbps=3.5, gpu_gbps=1008.0))
        assert size == compute_window_size(3.5, 1008.0, prefetch_ahead_chunks=2)


class TestChunkAlignment:
    def test_chunk_count_for_bytes(self) -> None:
        assert chunk_count_for_bytes(0, 128) == 0
        assert chunk_count_for_bytes(128 * 1024, 128) == 1
        assert chunk_count_for_bytes(128 * 1024 + 1, 128) == 2

    def test_chunk_byte_range(self) -> None:
        start, end = chunk_byte_range(2, 128)
        assert start == 2 * 128 * 1024
        assert end - start == 128 * 1024

    def test_chunk_count_rejects_negative_bytes(self) -> None:
        with pytest.raises(ValueError):
            chunk_count_for_bytes(-1, 128)


class TestFlashWindowPrefetcher:
    def test_prefetch_ahead_on_request(self) -> None:
        profile = _mock_profile()
        cfg = FlashWindowConfig(enabled=True, window_size=4)
        prefetcher = FlashWindowPrefetcher(cfg, profile, total_weight_bytes=10 * 128 * 1024)
        ahead = prefetcher.on_chunk_requested(0)
        assert ahead == [1, 2, 3, 4]
        assert prefetcher.prefetch_queue() == [1, 2, 3, 4]

    def test_prefetch_clamped_at_end(self) -> None:
        profile = _mock_profile()
        cfg = FlashWindowConfig(enabled=True, window_size=4)
        prefetcher = FlashWindowPrefetcher(cfg, profile, total_weight_bytes=3 * 128 * 1024)
        ahead = prefetcher.on_chunk_requested(2)
        assert ahead == []

    def test_lru_k_hot_window(self) -> None:
        profile = _mock_profile()
        cfg = FlashWindowConfig(enabled=True, window_size=2, lru_k=2)
        prefetcher = FlashWindowPrefetcher(cfg, profile, total_weight_bytes=8 * 128 * 1024)
        prefetcher.on_chunk_requested(0)
        prefetcher.on_chunk_requested(0)
        assert 0 in prefetcher.hot_window()

    def test_invalid_chunk_raises(self) -> None:
        profile = _mock_profile()
        prefetcher = FlashWindowPrefetcher(
            FlashWindowConfig(enabled=True, window_size=2),
            profile,
            total_weight_bytes=128 * 1024,
        )
        with pytest.raises(IndexError):
            prefetcher.on_chunk_requested(1)

    def test_lmcache_section(self) -> None:
        profile = _mock_profile()
        cfg = FlashWindowConfig(enabled=True, window_size=8, chunk_size_kb=128)
        prefetcher = FlashWindowPrefetcher(cfg, profile, total_weight_bytes=4 * 128 * 1024)
        section = prefetcher.lmcache_section(profile)
        assert section["enabled"] is True
        assert section["window_size"] == 8
        assert section["chunk_size_kb"] == 128


class TestLmcacheDiskConfig:
    def test_default_shape_unchanged(self) -> None:
        base = lmcache_disk_config(NaivePrefetchConfig(), "/tmp/kv")
        assert set(base.keys()) == {"backend", "path", "prefetch"}
        assert "flash_window" not in base
        assert "sparsity_prefetch" not in base

    def test_flash_key_only_when_enabled(self) -> None:
        disabled = lmcache_disk_config(
            NaivePrefetchConfig(),
            "/tmp/kv",
            flash=FlashWindowConfig(enabled=False),
        )
        assert "flash_window" not in disabled

        enabled = lmcache_disk_config(
            NaivePrefetchConfig(),
            "/tmp/kv",
            flash=FlashWindowConfig(enabled=True, window_size=12),
        )
        flash = enabled["flash_window"]
        assert isinstance(flash, dict)
        assert flash["enabled"] is True
        assert flash["window_size"] == 12
        assert flash["chunk_size_kb"] == 128

    def test_sparsity_key_only_when_enabled(self) -> None:
        disabled = lmcache_disk_config(
            NaivePrefetchConfig(),
            "/tmp/kv",
            sparsity=SparsityPrefetchConfig(enabled=False),
        )
        assert "sparsity_prefetch" not in disabled

        enabled = lmcache_disk_config(
            NaivePrefetchConfig(),
            "/tmp/kv",
            sparsity=SparsityPrefetchConfig(enabled=True, layer_count=24),
        )
        sparsity = enabled["sparsity_prefetch"]
        assert isinstance(sparsity, dict)
        assert sparsity["enabled"] is True
        assert sparsity["layer_count"] == 24
        assert sparsity["hot_threshold"] == 0.80

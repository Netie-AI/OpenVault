"""Device profile detection tests — fully mocked, no GPU/NVMe required."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openmw.device_profile import (
    DeviceProfile,
    _CachedProfileEnvelope,
    _detect_uncached,
    _load_cache,
    _save_cache,
    detect,
    read_boot_id,
)


def _mock_ram(total_bytes: int = 16 * 1024**3) -> MagicMock:
    mem = MagicMock()
    mem.total = total_bytes
    return mem


@pytest.fixture
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / "device_profile.json"


class TestNvidiaPath:
    @patch("openmw.device_profile._estimate_endurance_tbw", return_value=600.0)
    @patch("openmw.device_profile._select_primary_nvme", return_value=("Samsung 990 PRO", "/dev/nvme0"))
    @patch("openmw.device_profile._probe_apple_silicon", return_value=(None, False))
    @patch("openmw.device_profile._probe_amd_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile._probe_nvidia_gpu", return_value=("NVIDIA GeForce RTX 4090", 24.0))
    @patch("openmw.device_profile.psutil.cpu_count", return_value=16)
    @patch("openmw.device_profile.psutil.virtual_memory")
    def test_detect_nvidia_profile(
        self,
        mock_mem: MagicMock,
        *_mocks: object,
    ) -> None:
        mock_mem.return_value = _mock_ram()
        profile = _detect_uncached(benchmark_fn=lambda **_: 7.0)

        assert isinstance(profile, DeviceProfile)
        assert profile.gpu_name == "NVIDIA GeForce RTX 4090"
        assert profile.gpu_vram_gb == 24.0
        assert profile.gpu_bandwidth_gbps == 1008.0
        assert profile.cpu_inference_mode is False
        assert profile.unified_memory is False
        assert profile.nvme_model == "Samsung 990 PRO"
        assert profile.nvme_seq_read_gbps == 7.0
        assert profile.nvme_endurance_tbw == 600.0


class TestNoGpuPath:
    @patch("openmw.device_profile._estimate_endurance_tbw", return_value=0.0)
    @patch("openmw.device_profile._select_primary_nvme", return_value=(None, None))
    @patch("openmw.device_profile._probe_apple_silicon", return_value=(None, False))
    @patch("openmw.device_profile._probe_amd_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile._probe_nvidia_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile.psutil.cpu_count", return_value=8)
    @patch("openmw.device_profile.psutil.virtual_memory")
    def test_detect_cpu_only_profile(
        self,
        mock_mem: MagicMock,
        *_mocks: object,
    ) -> None:
        mock_mem.return_value = _mock_ram(32 * 1024**3)
        profile = _detect_uncached(benchmark_fn=lambda **_: 3.5)

        assert profile.gpu_name is None
        assert profile.gpu_vram_gb == 0.0
        assert profile.cpu_inference_mode is True
        assert profile.gpu_bandwidth_gbps == 50.0
        assert profile.system_ram_gb == 32.0
        assert profile.cpu_cores == 8


class TestApplePath:
    @patch("openmw.device_profile._estimate_endurance_tbw", return_value=0.0)
    @patch("openmw.device_profile._select_primary_nvme", return_value=("APPLE SSD AP1024", "/dev/disk0"))
    @patch("openmw.device_profile._probe_nvidia_gpu", return_value=(None, 0.0))
    @patch(
        "openmw.device_profile._probe_apple_silicon",
        return_value=("Apple M3 Max", True),
    )
    @patch("openmw.device_profile.psutil.cpu_count", return_value=12)
    @patch("openmw.device_profile.psutil.virtual_memory")
    def test_detect_apple_unified_memory(
        self,
        mock_mem: MagicMock,
        *_mocks: object,
    ) -> None:
        mock_mem.return_value = _mock_ram(64 * 1024**3)
        profile = _detect_uncached(benchmark_fn=lambda **_: 5.5)

        assert profile.gpu_name == "Apple M3 Max"
        assert profile.unified_memory is True
        assert profile.gpu_vram_gb == profile.system_ram_gb == 64.0
        assert profile.cpu_inference_mode is False
        assert profile.gpu_bandwidth_gbps == 100.0


class TestCache:
    def test_cache_write_and_read(self, cache_path: Path) -> None:
        profile = DeviceProfile(
            gpu_name="NVIDIA GeForce RTX 4080",
            gpu_vram_gb=16.0,
            gpu_bandwidth_gbps=717.0,
            system_ram_gb=32.0,
            cpu_cores=12,
            nvme_model="WD_BLACK SN850X",
            nvme_seq_read_gbps=6.8,
            nvme_endurance_tbw=1200.0,
        )
        envelope = _CachedProfileEnvelope(
            boot_id="test-boot-id",
            detected_at="2026-06-18T00:00:00+00:00",
            profile=profile,
        )
        _save_cache(cache_path, envelope)

        loaded = _load_cache(cache_path)
        assert loaded is not None
        assert loaded.boot_id == "test-boot-id"
        assert loaded.profile == profile

    @patch("openmw.device_profile.read_boot_id", return_value="stable-boot")
    @patch("openmw.device_profile._detect_uncached")
    def test_detect_skips_redetection_on_cache_hit(
        self,
        mock_detect: MagicMock,
        _mock_boot: MagicMock,
        cache_path: Path,
    ) -> None:
        cached_profile = DeviceProfile(
            gpu_name="cached-gpu",
            gpu_vram_gb=8.0,
            gpu_bandwidth_gbps=360.0,
            system_ram_gb=16.0,
            cpu_cores=8,
            nvme_model="cached-nvme",
            nvme_seq_read_gbps=4.0,
            nvme_endurance_tbw=300.0,
        )
        _save_cache(
            cache_path,
            _CachedProfileEnvelope(
                boot_id="stable-boot",
                detected_at="2026-06-18T00:00:00+00:00",
                profile=cached_profile,
            ),
        )

        result = detect(cache_path=cache_path)
        assert result == cached_profile
        mock_detect.assert_not_called()

    @patch("openmw.device_profile.read_boot_id", return_value="new-boot")
    @patch("openmw.device_profile._detect_uncached")
    def test_detect_refreshes_when_boot_id_changes(
        self,
        mock_detect: MagicMock,
        _mock_boot: MagicMock,
        cache_path: Path,
    ) -> None:
        stale = DeviceProfile(
            gpu_name="stale",
            gpu_vram_gb=1.0,
            gpu_bandwidth_gbps=1.0,
            system_ram_gb=8.0,
            cpu_cores=4,
            nvme_model=None,
            nvme_seq_read_gbps=1.0,
            nvme_endurance_tbw=0.0,
        )
        _save_cache(
            cache_path,
            _CachedProfileEnvelope(
                boot_id="old-boot",
                detected_at="2026-06-17T00:00:00+00:00",
                profile=stale,
            ),
        )
        fresh = stale.__class__(
            gpu_name="fresh",
            gpu_vram_gb=24.0,
            gpu_bandwidth_gbps=1008.0,
            system_ram_gb=32.0,
            cpu_cores=16,
            nvme_model="fresh-nvme",
            nvme_seq_read_gbps=7.0,
            nvme_endurance_tbw=600.0,
        )
        mock_detect.return_value = fresh

        result = detect(cache_path=cache_path)
        assert result.gpu_name == "fresh"
        mock_detect.assert_called_once()

        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        assert payload["boot_id"] == "new-boot"

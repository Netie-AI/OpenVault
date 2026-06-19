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
    _with_timeout,
    detect,
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
    @patch(
        "openmw.device_profile._select_primary_nvme", return_value=("Samsung 990 PRO", "/dev/nvme0")
    )
    @patch("openmw.device_profile._probe_apple_silicon", return_value=(None, False))
    @patch("openmw.device_profile._probe_amd_gpu", return_value=(None, 0.0))
    @patch(
        "openmw.device_profile._probe_nvidia_gpu", return_value=("NVIDIA GeForce RTX 4090", 24.0)
    )
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
    @patch(
        "openmw.device_profile._select_primary_nvme",
        return_value=("APPLE SSD AP1024", "/dev/disk0"),
    )
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


class TestHardwareProbeTimeout:
    """PART 12: a hung native call (e.g. Windows DeviceIoControl with no timeout
    or overlapped I/O) must not block detect() indefinitely. See MASTER_HANDOFF.md
    PART 12 hang investigation — confirmed root cause was an unbounded ctypes call."""

    def test_with_timeout_returns_default_on_hang(self) -> None:
        import time

        def _hangs_forever() -> str:
            time.sleep(10.0)
            return "should never be reached"

        t0 = time.monotonic()
        result = _with_timeout(_hangs_forever, timeout_s=0.2, default="DEGRADED")
        elapsed = time.monotonic() - t0

        assert result == "DEGRADED"
        assert elapsed < 1.0, f"timeout wrapper did not bound the call: took {elapsed:.2f}s"

    def test_with_timeout_returns_real_result_when_fast(self) -> None:
        def _fast() -> str:
            return "real"

        assert _with_timeout(_fast, timeout_s=5.0, default="DEGRADED") == "real"

    def test_with_timeout_returns_default_on_exception(self) -> None:
        def _raises() -> str:
            raise RuntimeError("simulated driver error")

        assert _with_timeout(_raises, timeout_s=5.0, default="DEGRADED") == "DEGRADED"

    @patch("openmw.device_profile._estimate_endurance_tbw")
    @patch("openmw.device_profile._select_primary_nvme", return_value=("Boot NVMe", "/dev/nvme0"))
    @patch("openmw.device_profile._probe_apple_silicon", return_value=(None, False))
    @patch("openmw.device_profile._probe_amd_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile._probe_nvidia_gpu", return_value=("RTX 4090", 24.0))
    @patch("openmw.device_profile._probe_cpu_cores", return_value=16)
    @patch("openmw.device_profile._probe_system_ram_gb", return_value=64.0)
    @patch("openmw.device_profile._HARDWARE_PROBE_TIMEOUT_S", 0.2)
    def test_detect_uncached_survives_hung_endurance_probe(
        self,
        _mock_ram: MagicMock,
        _mock_cores: MagicMock,
        _mock_nvidia: MagicMock,
        _mock_amd: MagicMock,
        _mock_apple: MagicMock,
        _mock_select_nvme: MagicMock,
        mock_endurance: MagicMock,
    ) -> None:
        """End-to-end: _detect_uncached() must complete even if the endurance
        probe (which calls read_smart() -> DeviceIoControl on Windows) hangs."""
        import time

        def _hangs(_device_path: str | None) -> float:
            time.sleep(10.0)
            return 999.0  # never reached

        mock_endurance.side_effect = _hangs

        t0 = time.monotonic()
        profile = _detect_uncached(benchmark_fn=lambda **_kwargs: 3.5)
        elapsed = time.monotonic() - t0

        assert elapsed < 2.0, f"_detect_uncached did not survive the hung probe: {elapsed:.2f}s"
        assert profile.nvme_endurance_tbw == 0.0  # honest degraded value, not the hung 999.0
        assert profile.gpu_name == "RTX 4090"  # rest of detection still completed normally

    @patch("openmw.device_profile._estimate_endurance_tbw", return_value=600.0)
    @patch("openmw.device_profile._select_primary_nvme", return_value=("Boot NVMe", "/dev/nvme0"))
    @patch("openmw.device_profile._probe_apple_silicon", return_value=(None, False))
    @patch("openmw.device_profile._probe_amd_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile._probe_cpu_cores", return_value=16)
    @patch("openmw.device_profile._probe_system_ram_gb", return_value=64.0)
    @patch("openmw.device_profile._HARDWARE_PROBE_TIMEOUT_S", 0.2)
    def test_detect_uncached_survives_hung_nvidia_probe(
        self,
        _mock_ram: MagicMock,
        _mock_cores: MagicMock,
        _mock_amd: MagicMock,
        _mock_apple: MagicMock,
        _mock_select_nvme: MagicMock,
        _mock_endurance: MagicMock,
    ) -> None:
        """PART 12b: real-hardware report showed openmw doctor still hung (165s,
        killed) after the endurance-probe fix alone. Root cause: _probe_nvidia_gpu()
        calls raw NVML (pynvml.nvmlInit() etc.) with zero timeout - a stalled GPU
        driver can hang nvmlInit() indefinitely. This test proves that path is now
        bounded too."""
        import time

        with patch("openmw.device_profile._probe_nvidia_gpu") as mock_nvidia:

            def _hangs() -> tuple[str | None, float]:
                time.sleep(10.0)
                return ("never reached", 999.0)

            mock_nvidia.side_effect = _hangs

            t0 = time.monotonic()
            profile = _detect_uncached(benchmark_fn=lambda **_kwargs: 3.5)
            elapsed = time.monotonic() - t0

        assert elapsed < 2.0, f"_detect_uncached did not survive a hung NVML probe: {elapsed:.2f}s"
        # NVML timed out -> falls through to AMD probe (also mocked None) -> CPU-only
        assert profile.gpu_name is None
        assert profile.cpu_inference_mode is True

    @patch("openmw.device_profile._estimate_endurance_tbw", return_value=600.0)
    @patch("openmw.device_profile._select_primary_nvme", return_value=("Boot NVMe", "/dev/nvme0"))
    @patch("openmw.device_profile._probe_apple_silicon", return_value=(None, False))
    @patch("openmw.device_profile._probe_amd_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile._probe_nvidia_gpu", return_value=("RTX 4090", 24.0))
    @patch("openmw.device_profile._probe_cpu_cores", return_value=16)
    @patch("openmw.device_profile._probe_system_ram_gb", return_value=64.0)
    @patch("openmw.device_profile._BENCHMARK_DURATION_S", 0.01)
    @patch("openmw.device_profile._BENCHMARK_TIMEOUT_MARGIN_S", 0.19)
    def test_detect_uncached_survives_hung_benchmark(
        self,
        _mock_ram: MagicMock,
        _mock_cores: MagicMock,
        _mock_nvidia: MagicMock,
        _mock_amd: MagicMock,
        _mock_apple: MagicMock,
        _mock_select_nvme: MagicMock,
        _mock_endurance: MagicMock,
    ) -> None:
        """PART 12b: benchmark_nvme_seq_read_gbps()'s internal deadline check only
        fires *between* reads - a single blocked read/write/tempfile call on a
        stalled boot drive can hang past its own 5s intended duration. This test
        proves the outer wrapper bounds it regardless."""
        import time

        def _hung_benchmark(**_kwargs: object) -> float:
            time.sleep(10.0)
            return 999.0  # never reached

        t0 = time.monotonic()
        profile = _detect_uncached(benchmark_fn=_hung_benchmark)
        elapsed = time.monotonic() - t0

        assert elapsed < 2.0, f"_detect_uncached did not survive a hung benchmark: {elapsed:.2f}s"
        assert profile.nvme_seq_read_gbps == 3.5  # _DEFAULT_NVME_SEQ_READ_GBPS, not the hung 999.0

    @patch("openmw.device_profile._estimate_endurance_tbw", return_value=0.0)
    @patch("openmw.device_profile._select_primary_nvme", return_value=(None, None))
    @patch("openmw.device_profile._probe_apple_silicon", return_value=(None, False))
    @patch("openmw.device_profile._probe_amd_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile._probe_nvidia_gpu", return_value=(None, 0.0))
    @patch("openmw.device_profile.psutil.cpu_count", return_value=8)
    @patch("openmw.device_profile.psutil.virtual_memory")
    def test_detect_uses_shorter_benchmark_when_nvme_unknown(
        self,
        mock_mem: MagicMock,
        *_mocks: object,
    ) -> None:
        mock_mem.return_value = _mock_ram()
        seen: dict[str, float] = {}

        def _capture_benchmark(**kwargs: object) -> float:
            duration = kwargs.get("duration_s")
            if isinstance(duration, (int, float)):
                seen["duration_s"] = float(duration)
            return 3.5

        from openmw import device_profile as dp

        profile = _detect_uncached(benchmark_fn=_capture_benchmark)

        assert profile.nvme_model is None
        assert seen["duration_s"] == dp._BENCHMARK_DURATION_DEGRADED_S

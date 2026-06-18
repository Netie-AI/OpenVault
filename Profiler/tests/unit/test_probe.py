"""Capability probe unit tests."""

from __future__ import annotations

from unittest.mock import patch

from nvme_profiler.probe import run_capability_probe
from nvme_profiler.schema import AccelerationTier, CapabilityManifest, GpuClass, GpuVendor


def test_capability_manifest_schema_roundtrip() -> None:
    manifest = CapabilityManifest(
        os="linux",
        python_version="3.12.0",
        kernel="6.8.0",
        gpu_vendor=GpuVendor.NVIDIA,
        gpu_class=GpuClass.CONSUMER,
        gpu_model="NVIDIA GeForce RTX 4060",
        enabled_tiers=[AccelerationTier.BASELINE, AccelerationTier.IO_URING_NVME],
        degraded_reasons=["gds: GDS not supported on consumer GeForce GPUs"],
    )
    data = manifest.model_dump(mode="json")
    restored = CapabilityManifest.model_validate(data)
    assert restored.gpu_class == GpuClass.CONSUMER
    assert AccelerationTier.BASELINE in restored.enabled_tiers


@patch("nvme_profiler.probe.list_devices", return_value=[])
@patch("nvme_profiler.probe._probe_gpu", return_value=(GpuVendor.NONE, GpuClass.NONE, None, None))
def test_run_capability_probe_baseline_only(
    _mock_gpu: object,
    _mock_devices: object,
) -> None:
    manifest = run_capability_probe()
    assert isinstance(manifest, CapabilityManifest)
    assert AccelerationTier.BASELINE in manifest.enabled_tiers
    assert manifest.os == __import__("sys").platform


@patch("nvme_profiler.probe.list_devices", return_value=[])
@patch(
    "nvme_profiler.probe._probe_gpu",
    return_value=(GpuVendor.NVIDIA, GpuClass.CONSUMER, "GeForce RTX 3080", "12.4"),
)
def test_consumer_gpu_gds_degraded(
    _mock_gpu: object,
    _mock_devices: object,
) -> None:
    manifest = run_capability_probe()
    assert AccelerationTier.GDS not in manifest.enabled_tiers
    assert any("gds:" in r for r in manifest.degraded_reasons)


@patch("nvme_profiler.probe._linux_io_uring_available", return_value=(True, ""))
@patch("nvme_profiler.probe.list_devices", return_value=[])
@patch(
    "nvme_profiler.probe._probe_gpu",
    return_value=(GpuVendor.NVIDIA, GpuClass.WORKSTATION, "NVIDIA A100", "12.4"),
)
@patch("nvme_profiler.probe._gds_available", return_value=(True, ""))
@patch("nvme_profiler.probe._spdk_available", return_value=(False, "no spare"))
def test_workstation_tiers_on_linux(
    _spdk: object,
    _gds: object,
    _gpu: object,
    _devices: object,
    _io_uring: object,
) -> None:
    with patch("nvme_profiler.probe.sys.platform", "linux"):
        manifest = run_capability_probe()
    assert AccelerationTier.IO_URING_NVME in manifest.enabled_tiers
    assert AccelerationTier.GDS in manifest.enabled_tiers

"""OpenMW measurement loop tests."""

from __future__ import annotations

from pathlib import Path

from openmw.prefetch_naive import NaivePrefetchConfig, lmcache_disk_config
from openmw.prefetch_heuristic import HeuristicPrefetchConfig, lmcache_heuristic_overlay
from openmw.run import compare_prefetch_runs, run_offload_measurement_loop
from openmw.windows_ioring_spike import probe_windows_ioring_spike


def test_lmcache_disk_config() -> None:
    cfg = lmcache_disk_config(NaivePrefetchConfig(enabled=True), "/tmp/kv")
    assert cfg["backend"] == "disk"
    prefetch = cfg["prefetch"]
    assert isinstance(prefetch, dict)
    assert prefetch["enabled"] is True


def test_heuristic_overlay() -> None:
    base = lmcache_disk_config(NaivePrefetchConfig(), "/tmp/kv")
    merged = lmcache_heuristic_overlay(base, HeuristicPrefetchConfig(enabled=True))
    prefetch = merged["prefetch"]
    assert isinstance(prefetch, dict)
    assert prefetch.get("heuristic_enabled") is True


def test_offload_measurement_loop(tmp_path: Path) -> None:
    result = run_offload_measurement_loop(
        tmp_path,
        prefetch=NaivePrefetchConfig(enabled=True),
    )
    assert result.bench_html.is_file()
    assert result.path_trace_html.is_file()
    assert result.manifest_path is not None


def test_compare_prefetch_runs(tmp_path: Path) -> None:
    cmp = compare_prefetch_runs(tmp_path)
    assert "prefetch_off_gpu_idle_pct" in cmp
    assert "prefetch_on_gpu_idle_pct" in cmp


def test_windows_ioring_spike() -> None:
    result = probe_windows_ioring_spike()
    assert result.label == "exploratory-not-committed"
    assert result.evidence

"""Chaos, OOM-edge, and random-user simulation tests — fully mocked, no hardware."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from openmw.device_profile import DeviceProfile
from openmw.model_manager import InsufficientDiskSpaceError, download
from openmw.model_router import (
    ModelRouter,
    classify_tier,
    estimate_vram_gb,
    layer_vram_gb,
    load_registry,
    quant_effective_bits,
    tier_rank,
)


def _profile(
    *,
    gpu_vram_gb: float = 8.0,
    system_ram_gb: float = 16.0,
    unified_memory: bool = False,
    cpu_inference_mode: bool = False,
    nvme_seq_read_gbps: float = 3.5,
) -> DeviceProfile:
    return DeviceProfile(
        gpu_name=None if cpu_inference_mode else "Chaos GPU",
        gpu_vram_gb=0.0 if cpu_inference_mode else gpu_vram_gb,
        gpu_bandwidth_gbps=360.0,
        system_ram_gb=system_ram_gb,
        cpu_cores=8,
        nvme_model="Mock NVMe",
        nvme_seq_read_gbps=nvme_seq_read_gbps,
        nvme_endurance_tbw=600.0,
        unified_memory=unified_memory,
        cpu_inference_mode=cpu_inference_mode,
    )


def _assert_routing_invariants(router: ModelRouter, profile: DeviceProfile, model_id: str) -> None:
    spec = router.registry[model_id]
    decision = router.route(profile, model_id)

    assert decision.model_id == model_id
    assert decision.quant_level in spec.quant_options
    assert decision.gpu_layers >= 0
    assert decision.cpu_ram_layers >= 0
    assert decision.nvme_layers >= 0
    assert decision.gpu_layers + decision.cpu_ram_layers + decision.nvme_layers == spec.layers

    if decision.gpu_layers >= spec.layers:
        assert decision.offload_strategy == "full_gpu"
        assert decision.nvme_layers == 0
    elif decision.nvme_layers > 0:
        assert decision.offload_strategy == "nvme_offload"
    else:
        assert decision.offload_strategy == "cpu_offload"

    if decision.kv_quant_recommended:
        assert decision.value_quant_bits == 4
        assert decision.key_quant_bits == 2
    else:
        assert decision.value_quant_bits == 16
        assert decision.key_quant_bits == 16

    assert decision.estimated_tok_s > 0.0
    assert decision.estimated_vram_gb > 0.0


class TestRandomUserMatrix:
    """Simulate thousands of random hardware + model combinations."""

    @pytest.mark.parametrize("seed", range(8))
    def test_random_profiles_never_crash(self, seed: int) -> None:
        router = ModelRouter()
        rng = np.random.default_rng(seed)
        model_ids = list(router.registry.keys())

        for _ in range(200):
            gpu_vram = float(rng.uniform(0.0, 128.0))
            system_ram = float(rng.uniform(1.0, 256.0))
            unified = bool(rng.integers(0, 2))
            cpu_only = bool(rng.integers(0, 2))
            nvme_gbps = float(rng.uniform(0.1, 14.0))

            if cpu_only:
                gpu_vram = 0.0
                unified = False

            profile = _profile(
                gpu_vram_gb=gpu_vram,
                system_ram_gb=system_ram,
                unified_memory=unified,
                cpu_inference_mode=cpu_only,
                nvme_seq_read_gbps=nvme_gbps,
            )
            model_id = model_ids[int(rng.integers(0, len(model_ids)))]
            _assert_routing_invariants(router, profile, model_id)


class TestOomEdgeCases:
    """Harsh memory starvation scenarios."""

    @pytest.mark.parametrize("model_id", sorted(load_registry().keys()))
    def test_starved_discrete_gpu_offloads(self, model_id: str) -> None:
        router = ModelRouter()
        profile = _profile(gpu_vram_gb=0.5, system_ram_gb=2.0)
        _assert_routing_invariants(router, profile, model_id)

    @pytest.mark.parametrize("model_id", sorted(load_registry().keys()))
    def test_starved_cpu_only_survives(self, model_id: str) -> None:
        router = ModelRouter()
        profile = _profile(system_ram_gb=1.0, cpu_inference_mode=True)
        _assert_routing_invariants(router, profile, model_id)

    @pytest.mark.parametrize("model_id", sorted(load_registry().keys()))
    def test_tiny_unified_memory_pool(self, model_id: str) -> None:
        router = ModelRouter()
        profile = _profile(
            gpu_vram_gb=4.0,
            system_ram_gb=4.0,
            unified_memory=True,
        )
        _assert_routing_invariants(router, profile, model_id)

    def test_disk_space_oom_blocks_download(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        router = ModelRouter()
        models_dir = tmp_path / "models"
        hf_filename = "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
        api = MagicMock()
        api.list_repo_files.return_value = [hf_filename]
        sibling = MagicMock()
        sibling.rfilename = hf_filename
        sibling.lfs = MagicMock(size=16 * 1024**3, sha256="c" * 64)
        sibling.size = 16 * 1024**3
        api.repo_info.return_value = MagicMock(siblings=[sibling])

        monkeypatch.setattr(
            "openmw.model_manager.shutil.disk_usage",
            lambda _path: MagicMock(free=512),
        )

        with pytest.raises(InsufficientDiskSpaceError):
            download(
                "qwen2.5-1.5b",
                quant_level="Q4_K_M",
                models_dir=models_dir,
                router=router,
                hf_api=api,
            )


class TestRegistryCorruption:
    def test_missing_models_key_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="models"):
            load_registry(bad)

    def test_non_object_entry_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"models": ["not-a-dict"]}), encoding="utf-8")
        with pytest.raises(ValueError, match="object"):
            load_registry(bad)

    def test_unknown_model_still_raises(self) -> None:
        router = ModelRouter()
        with pytest.raises(KeyError, match="unknown model_id"):
            router.route(_profile(), "totally-fake-model")


class TestFormulaStress:
    def test_layer_vram_rejects_zero_layers(self) -> None:
        with pytest.raises(ValueError, match="layers must be >= 1"):
            layer_vram_gb(8.0, 0, quant_effective_bits("Q4_K_M"))

    def test_quant_unknown_label_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown quant level"):
            quant_effective_bits("Q99_FAKE")

    @pytest.mark.parametrize("ctx_tokens", [128, 4096, 32768, 131072])
    def test_vram_monotonic_in_context(self, ctx_tokens: int) -> None:
        bits = quant_effective_bits("Q4_K_M")
        small = estimate_vram_gb(8.0, bits, 1024)
        large = estimate_vram_gb(8.0, bits, ctx_tokens)
        if ctx_tokens > 1024:
            assert large >= small

    @pytest.mark.parametrize(
        ("vram_gb", "expected"),
        [
            (-1.0, "NANO"),
            (0.0, "NANO"),
            (5.999, "NANO"),
            (6.0, "SMALL"),
            (1e6, "XLARGE"),
        ],
    )
    def test_tier_boundaries_extreme(self, vram_gb: float, expected: str) -> None:
        assert classify_tier(vram_gb) == expected

    def test_comfortable_tier_never_below_min_tier(self) -> None:
        for spec in load_registry().values():
            assert tier_rank(spec.comfortable_tier) >= tier_rank(spec.tier)

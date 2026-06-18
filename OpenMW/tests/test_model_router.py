"""Model router tests — mocked DeviceProfile only, no GPU/NVMe hardware."""

from __future__ import annotations

import pytest

from openmw.device_profile import DeviceProfile
from openmw.model_router import (
    ModelRouter,
    RoutingDecision,
    classify_tier,
    estimate_vram_gb,
    layer_vram_gb,
    load_registry,
    quant_effective_bits,
    tier_rank,
    tier_upper_bound_gb,
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
        gpu_name=None if cpu_inference_mode else "Test GPU",
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


@pytest.fixture
def router() -> ModelRouter:
    return ModelRouter()


class TestRegistry:
    def test_loads_twenty_models(self) -> None:
        registry = load_registry()
        assert len(registry) == 20
        tiers = {spec.tier for spec in registry.values()}
        assert tiers == {"NANO", "SMALL", "MID", "LARGE", "XLARGE"}

    def test_comfortable_tier_never_below_curated_tier(self) -> None:
        registry = load_registry()
        for spec in registry.values():
            assert tier_rank(spec.comfortable_tier) >= tier_rank(spec.tier), (
                f"{spec.id}: comfortable_tier {spec.comfortable_tier} < tier {spec.tier}"
            )


class TestComfortableTierFormula:
    @pytest.mark.parametrize(
        "model_id",
        sorted(load_registry().keys()),
    )
    def test_comfortable_tier_fits_formula(self, model_id: str) -> None:
        router = ModelRouter()
        spec = router.registry[model_id]
        q4 = quant_effective_bits("Q4_K_M")
        needed = estimate_vram_gb(spec.params_B, q4, router._ctx_tokens)
        ceiling = tier_upper_bound_gb(spec.comfortable_tier)
        assert needed <= ceiling, (
            f"{model_id}: {needed:.4f} GB > {spec.comfortable_tier} ceiling {ceiling} GB"
        )


class TestVramFormula:
    def test_eight_b_q4_matches_plan(self) -> None:
        bits = quant_effective_bits("Q4_K_M")
        vram = estimate_vram_gb(8.0, bits, 4096)
        assert 6.0 <= vram <= 8.0

    def test_layer_vram_sums_to_weights(self) -> None:
        per_layer = layer_vram_gb(8.0, 32, quant_effective_bits("Q4_K_M"))
        total = per_layer * 32
        weights_only = (8.0 * 4.5 / 8.0) * 1.4
        assert total == pytest.approx(weights_only, rel=1e-4)


class TestTierBoundaries:
    @pytest.mark.parametrize(
        ("vram_gb", "expected"),
        [
            (0.0, "NANO"),
            (5.99, "NANO"),
            (6.0, "SMALL"),
            (11.99, "SMALL"),
            (12.0, "MID"),
            (15.99, "MID"),
            (16.0, "LARGE"),
            (23.99, "LARGE"),
            (24.0, "XLARGE"),
            (128.0, "XLARGE"),
        ],
    )
    def test_classify_tier(self, vram_gb: float, expected: str) -> None:
        assert classify_tier(vram_gb) == expected

    @pytest.mark.parametrize(
        ("gpu_vram_gb", "expected_tier"),
        [
            (4.0, "NANO"),
            (8.0, "SMALL"),
            (14.0, "MID"),
            (20.0, "LARGE"),
            (48.0, "XLARGE"),
        ],
    )
    def test_tier_for_profile_boundaries(
        self,
        router: ModelRouter,
        gpu_vram_gb: float,
        expected_tier: str,
    ) -> None:
        profile = _profile(gpu_vram_gb=gpu_vram_gb, system_ram_gb=32.0)
        assert router.tier_for_profile(profile) == expected_tier


class TestRoutingPaths:
    def test_small_tier_routes_llama8b_on_gpu(self, router: ModelRouter) -> None:
        profile = _profile(gpu_vram_gb=10.0, system_ram_gb=32.0)
        decision = router.route(profile, "llama-3.3-8b")

        assert isinstance(decision, RoutingDecision)
        assert decision.model_id == "llama-3.3-8b"
        assert decision.gpu_layers == 32
        assert decision.nvme_layers == 0
        assert decision.offload_strategy == "full_gpu"
        assert decision.quant_level in {"Q4_K_M", "Q5_K_M", "Q8_0", "FP16"}

    def test_cpu_only_offloads_to_ram_then_nvme(self, router: ModelRouter) -> None:
        profile = _profile(system_ram_gb=8.0, cpu_inference_mode=True)
        decision = router.route(profile, "llama-3.3-8b")

        assert decision.gpu_layers == 0
        assert decision.cpu_ram_layers + decision.nvme_layers == 32
        assert decision.nvme_layers > 0
        assert decision.offload_strategy == "nvme_offload"
        assert decision.kv_quant_recommended is True
        assert decision.value_quant_bits == 4
        assert decision.key_quant_bits == 2

    def test_apple_unified_memory_full_gpu(self, router: ModelRouter) -> None:
        profile = _profile(
            gpu_vram_gb=64.0,
            system_ram_gb=64.0,
            unified_memory=True,
        )
        decision = router.route(profile, "llama-3.3-8b")

        assert decision.gpu_layers == 32
        assert decision.cpu_ram_layers == 0
        assert decision.nvme_layers == 0
        assert decision.offload_strategy == "full_gpu"

    def test_apple_unified_oversized_model_uses_nvme(self, router: ModelRouter) -> None:
        profile = _profile(
            gpu_vram_gb=32.0,
            system_ram_gb=32.0,
            unified_memory=True,
        )
        decision = router.route(profile, "llama-3.3-70b")

        assert decision.quant_level == "Q4_K_M"
        assert decision.gpu_layers < 80
        assert decision.nvme_layers > 0
        assert decision.offload_strategy == "nvme_offload"

    def test_discrete_gpu_nvme_offload_when_vram_insufficient(self, router: ModelRouter) -> None:
        profile = _profile(gpu_vram_gb=6.0, system_ram_gb=16.0)
        decision = router.route(profile, "qwen3-32b")

        assert decision.gpu_layers < 64
        assert decision.nvme_layers > 0
        assert decision.offload_strategy == "nvme_offload"
        assert decision.kv_quant_recommended is True

    def test_mid_tier_cpu_offload_without_nvme(self, router: ModelRouter) -> None:
        profile = _profile(gpu_vram_gb=14.0, system_ram_gb=64.0)
        decision = router.route(profile, "mistral-small-3.1-24b")

        assert decision.gpu_layers > 0
        assert decision.gpu_layers < 40
        if decision.nvme_layers == 0:
            assert decision.offload_strategy == "cpu_offload"
        assert decision.gpu_layers + decision.cpu_ram_layers + decision.nvme_layers == 40

    def test_unknown_model_raises(self, router: ModelRouter) -> None:
        profile = _profile()
        with pytest.raises(KeyError, match="unknown model_id"):
            router.route(profile, "does-not-exist")

    def test_estimated_tok_s_within_tier_band(self, router: ModelRouter) -> None:
        profile = _profile(gpu_vram_gb=24.0, system_ram_gb=64.0)
        decision = router.route(profile, "llama-3.3-8b")
        assert 8.0 <= decision.estimated_tok_s <= 70.0

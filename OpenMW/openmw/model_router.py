"""Model routing intelligence: VRAM estimation, tier classification, offload planning."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from openmw.device_profile import DeviceProfile

log = structlog.get_logger()

_DEFAULT_REGISTRY = Path(__file__).resolve().parent / "data" / "models.json"
_DEFAULT_CTX_TOKENS = 4096
_WEIGHT_OVERHEAD = 1.4
_KV_MB_PER_1K_AT_8B = 144.0
_FRAMEWORK_OVERHEAD_GB = 1.0
_RAM_USABLE_FRACTION = 0.75
_UNIFIED_USABLE_FRACTION = 0.90

QuantLevel = Literal["Q4_K_M", "Q5_K_M", "Q8_0", "FP16"]
OffloadStrategy = Literal["full_gpu", "cpu_offload", "nvme_offload"]
HardwareTier = Literal["NANO", "SMALL", "MID", "LARGE", "XLARGE"]

_QUANT_BITS: dict[str, float] = {
    "Q4_K_M": 4.5,
    "Q5_K_M": 5.5,
    "Q8_0": 8.0,
    "FP16": 16.0,
}
_QUANT_PREFERENCE: tuple[str, ...] = ("FP16", "Q8_0", "Q5_K_M", "Q4_K_M")

_TIER_VRAM_BOUNDS: tuple[tuple[float, HardwareTier], ...] = (
    (6.0, "NANO"),
    (12.0, "SMALL"),
    (16.0, "MID"),
    (24.0, "LARGE"),
    (math.inf, "XLARGE"),
)

_TIER_TOK_S_RANGE: dict[HardwareTier, tuple[float, float]] = {
    "NANO": (8.0, 20.0),
    "SMALL": (40.0, 60.0),
    "MID": (55.0, 70.0),
    "LARGE": (35.0, 50.0),
    "XLARGE": (15.0, 40.0),
}


_TIER_ORDER: tuple[HardwareTier, ...] = ("NANO", "SMALL", "MID", "LARGE", "XLARGE")


@dataclass(frozen=True)
class ModelSpec:
    """One entry from the model registry."""

    id: str
    name: str
    tier: HardwareTier
    comfortable_tier: HardwareTier
    params_B: float
    layers: int
    context_length: int
    quant_options: tuple[str, ...]
    download_url: str
    license: str


@dataclass(frozen=True)
class RoutingDecision:
    """Hardware-aware inference plan for a model on a device profile."""

    model_id: str
    quant_level: str
    gpu_layers: int
    cpu_ram_layers: int
    nvme_layers: int
    estimated_tok_s: float
    estimated_vram_gb: float
    offload_strategy: OffloadStrategy
    kv_quant_recommended: bool
    value_quant_bits: int
    key_quant_bits: int


def default_registry_path() -> Path:
    """Return the bundled model registry JSON path."""
    return _DEFAULT_REGISTRY


def classify_tier(vram_gb: float) -> HardwareTier:
    """Map effective GPU/unified VRAM (GB) to a hardware tier."""
    for upper_bound, tier in _TIER_VRAM_BOUNDS:
        if vram_gb < upper_bound:
            return tier
    return "XLARGE"


def tier_upper_bound_gb(tier: HardwareTier) -> float:
    """Return the exclusive VRAM upper bound (GB) for a hardware tier."""
    for upper_bound, bound_tier in _TIER_VRAM_BOUNDS:
        if bound_tier == tier:
            return upper_bound
    return math.inf


def tier_rank(tier: HardwareTier) -> int:
    """Return ordinal rank for min_tier vs comfortable_tier comparisons."""
    return _TIER_ORDER.index(tier)


def quant_effective_bits(quant_level: str) -> float:
    """Return effective bits-per-param for a GGUF quant label."""
    if quant_level not in _QUANT_BITS:
        raise ValueError(f"unknown quant level: {quant_level}")
    return _QUANT_BITS[quant_level]


def kv_mb_per_1k_tokens(params_B: float) -> float:
    """Scale KV working-set from the 8B FP16 reference (144 MB / 1k tokens)."""
    return _KV_MB_PER_1K_AT_8B * (params_B / 8.0)


def estimate_vram_gb(params_B: float, quant_bits: float, ctx_tokens: int) -> float:
    """VRAM for weights + KV cache using the PART 2 master-plan formula."""
    weights_gb = (params_B * quant_bits / 8.0) * _WEIGHT_OVERHEAD
    kv_gb = (ctx_tokens / 1024.0) * (kv_mb_per_1k_tokens(params_B) / 1024.0)
    return weights_gb + kv_gb


def layer_vram_gb(params_B: float, layers: int, quant_bits: float) -> float:
    """Per-layer weight footprint including the 1.4× overhead factor."""
    if layers < 1:
        raise ValueError(f"layers must be >= 1, got {layers}")
    return (params_B / layers) * (quant_bits / 8.0) * _WEIGHT_OVERHEAD


def load_registry(path: Path | None = None) -> dict[str, ModelSpec]:
    """Load the model registry keyed by model id."""
    registry_path = path or default_registry_path()
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise ValueError("registry must contain a 'models' list")

    specs: dict[str, ModelSpec] = {}
    for entry in raw_models:
        if not isinstance(entry, dict):
            raise ValueError("each registry entry must be an object")
        model_id = str(entry["id"])
        tier = str(entry["tier"])
        comfortable_raw = entry.get("comfortable_tier", tier)
        specs[model_id] = ModelSpec(
            id=model_id,
            name=str(entry["name"]),
            tier=tier,  # type: ignore[arg-type]
            comfortable_tier=str(comfortable_raw),  # type: ignore[arg-type]
            params_B=float(entry["params_B"]),
            layers=int(entry["layers"]),
            context_length=int(entry["context_length"]),
            quant_options=tuple(str(q) for q in entry["quant_options"]),
            download_url=str(entry["download_url"]),
            license=str(entry["license"]),
        )
    return specs


class ModelRouter:
    """Route a model onto hardware using VRAM formulas and tier heuristics."""

    def __init__(
        self,
        *,
        registry_path: Path | None = None,
        ctx_tokens: int = _DEFAULT_CTX_TOKENS,
    ) -> None:
        self._registry_path = registry_path
        self._ctx_tokens = ctx_tokens
        self._registry = load_registry(registry_path)

    @property
    def registry(self) -> dict[str, ModelSpec]:
        """Return the loaded model registry."""
        return self._registry

    def tier_for_profile(self, profile: DeviceProfile) -> HardwareTier:
        """Classify hardware tier from a device profile."""
        effective_vram = self._effective_vram_gb(profile)
        return classify_tier(effective_vram)

    def route(self, profile: DeviceProfile, model_id: str) -> RoutingDecision:
        """Produce an offload plan for *model_id* on *profile*."""
        if model_id not in self._registry:
            raise KeyError(f"unknown model_id: {model_id}")
        spec = self._registry[model_id]
        quant_level = self._select_quant(profile, spec)
        quant_bits = quant_effective_bits(quant_level)
        per_layer_gb = layer_vram_gb(spec.params_B, spec.layers, quant_bits)
        kv_gb = (self._ctx_tokens / 1024.0) * (kv_mb_per_1k_tokens(spec.params_B) / 1024.0)
        estimated_vram = estimate_vram_gb(spec.params_B, quant_bits, self._ctx_tokens)

        gpu_layers, cpu_ram_layers, nvme_layers = self._split_layers(
            profile=profile,
            total_layers=spec.layers,
            per_layer_gb=per_layer_gb,
            kv_gb=kv_gb,
        )
        offload_strategy = self._offload_strategy(
            gpu_layers, cpu_ram_layers, nvme_layers, spec.layers
        )
        kv_quant_recommended = self._kv_quant_recommended(
            profile=profile,
            estimated_vram=estimated_vram,
            offload_strategy=offload_strategy,
        )
        value_bits, key_bits = self._kv_quant_bits(kv_quant_recommended)
        tier = self.tier_for_profile(profile)
        tok_s = self._estimate_tok_s(
            tier=tier,
            total_layers=spec.layers,
            gpu_layers=gpu_layers,
            cpu_ram_layers=cpu_ram_layers,
            nvme_layers=nvme_layers,
            nvme_gbps=profile.nvme_seq_read_gbps,
            offload_strategy=offload_strategy,
        )

        decision = RoutingDecision(
            model_id=model_id,
            quant_level=quant_level,
            gpu_layers=gpu_layers,
            cpu_ram_layers=cpu_ram_layers,
            nvme_layers=nvme_layers,
            estimated_tok_s=round(tok_s, 1),
            estimated_vram_gb=round(estimated_vram, 2),
            offload_strategy=offload_strategy,
            kv_quant_recommended=kv_quant_recommended,
            value_quant_bits=value_bits,
            key_quant_bits=key_bits,
        )
        log.debug(
            "model_routed",
            model_id=model_id,
            tier=tier,
            quant=quant_level,
            offload=offload_strategy,
            gpu_layers=gpu_layers,
            cpu_layers=cpu_ram_layers,
            nvme_layers=nvme_layers,
        )
        return decision

    def _effective_vram_gb(self, profile: DeviceProfile) -> float:
        if profile.cpu_inference_mode:
            return 0.0
        if profile.unified_memory:
            return profile.system_ram_gb * _UNIFIED_USABLE_FRACTION
        return profile.gpu_vram_gb

    def _select_quant(self, profile: DeviceProfile, spec: ModelSpec) -> str:
        available = self._effective_vram_gb(profile)
        if profile.cpu_inference_mode:
            available = profile.system_ram_gb * _RAM_USABLE_FRACTION

        options = [q for q in _QUANT_PREFERENCE if q in spec.quant_options]
        if not options:
            options = list(spec.quant_options)

        for quant in options:
            bits = quant_effective_bits(quant)
            needed = estimate_vram_gb(spec.params_B, bits, self._ctx_tokens)
            if needed + _FRAMEWORK_OVERHEAD_GB <= available:
                return quant

        for quant in reversed(options):
            bits = quant_effective_bits(quant)
            per_layer = layer_vram_gb(spec.params_B, spec.layers, bits)
            kv_gb = (self._ctx_tokens / 1024.0) * (kv_mb_per_1k_tokens(spec.params_B) / 1024.0)
            gpu_budget = max(0.0, available - kv_gb - _FRAMEWORK_OVERHEAD_GB)
            if per_layer > 0 and math.floor(gpu_budget / per_layer) >= 1:
                return quant

        return options[-1]

    def _split_layers(
        self,
        *,
        profile: DeviceProfile,
        total_layers: int,
        per_layer_gb: float,
        kv_gb: float,
    ) -> tuple[int, int, int]:
        if per_layer_gb <= 0:
            raise ValueError("per_layer_gb must be positive")

        layer_budget = kv_gb + _FRAMEWORK_OVERHEAD_GB

        if profile.unified_memory:
            pool = profile.system_ram_gb * _UNIFIED_USABLE_FRACTION
            fit = min(total_layers, int(max(0.0, pool - layer_budget) // per_layer_gb))
            return fit, 0, total_layers - fit

        if profile.cpu_inference_mode:
            ram_budget = profile.system_ram_gb * _RAM_USABLE_FRACTION
            cpu_layers = min(total_layers, int(max(0.0, ram_budget - layer_budget) // per_layer_gb))
            return 0, cpu_layers, total_layers - cpu_layers

        gpu_budget = max(0.0, profile.gpu_vram_gb - layer_budget)
        gpu_layers = min(total_layers, int(gpu_budget // per_layer_gb))
        remaining = total_layers - gpu_layers

        ram_budget = max(0.0, profile.system_ram_gb * _RAM_USABLE_FRACTION - _FRAMEWORK_OVERHEAD_GB)
        cpu_ram_layers = min(remaining, int(ram_budget // per_layer_gb))
        nvme_layers = remaining - cpu_ram_layers
        return gpu_layers, cpu_ram_layers, nvme_layers

    @staticmethod
    def _offload_strategy(
        gpu_layers: int,
        cpu_ram_layers: int,
        nvme_layers: int,
        total_layers: int,
    ) -> OffloadStrategy:
        if gpu_layers >= total_layers:
            return "full_gpu"
        if nvme_layers > 0:
            return "nvme_offload"
        return "cpu_offload"

    @staticmethod
    def _kv_quant_recommended(
        *,
        profile: DeviceProfile,
        estimated_vram: float,
        offload_strategy: OffloadStrategy,
    ) -> bool:
        if offload_strategy != "full_gpu":
            return True
        budget = profile.gpu_vram_gb
        if profile.unified_memory:
            budget = profile.system_ram_gb * _UNIFIED_USABLE_FRACTION
        if profile.cpu_inference_mode:
            budget = profile.system_ram_gb * _RAM_USABLE_FRACTION
        if estimated_vram > budget * 0.85:
            return True
        return False

    @staticmethod
    def _kv_quant_bits(recommended: bool) -> tuple[int, int]:
        """Align with KvQuantConfig defaults (value bits=4, key k_bits=2)."""
        if recommended:
            return 4, 2
        return 16, 16

    @staticmethod
    def _estimate_tok_s(
        *,
        tier: HardwareTier,
        total_layers: int,
        gpu_layers: int,
        cpu_ram_layers: int,
        nvme_layers: int,
        nvme_gbps: float,
        offload_strategy: OffloadStrategy,
    ) -> float:
        lo, hi = _TIER_TOK_S_RANGE[tier]
        base = (lo + hi) / 2.0
        if total_layers <= 0:
            return base

        gpu_frac = gpu_layers / total_layers
        cpu_frac = cpu_ram_layers / total_layers
        nvme_frac = nvme_layers / total_layers
        nvme_scale = min(max(nvme_gbps / 3.5, 0.5), 2.0)

        throughput_factor = gpu_frac * 1.0 + cpu_frac * 0.25 + nvme_frac * 0.08 * nvme_scale
        if offload_strategy == "full_gpu":
            throughput_factor = max(throughput_factor, 0.85)
        return max(lo * 0.5, base * throughput_factor)

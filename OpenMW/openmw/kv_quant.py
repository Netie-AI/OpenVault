"""Value- and Key-cache quantization wrappers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from openmw.key_channel_quant import KeyCacheQuantized, KeyChannelQuantizer
from openmw.vendor.turboquant.mse_quantizer import QuantizedMSE, TurboQuantMSE


class KvQuantConfig(BaseModel):
    """Configuration for Value- and Key-cache quantization tiers."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    bits: int = Field(default=4, ge=1, le=8)
    backend: str = "scos-lab-vendored"
    key_quant_enabled: bool = False
    k_bits: int = Field(default=2, ge=1, le=8)
    group_size: int = Field(default=32, ge=1)
    residual_length: int = Field(default=32, ge=0)


@dataclass(frozen=True)
class ValueCacheQuantized:
    """Quantized Value-cache payload produced by quantize_value_cache()."""

    indices: np.ndarray
    norms: np.ndarray
    d: int
    bits: int
    seed: int | None


def quantize_value_cache(
    values: np.ndarray,
    config: KvQuantConfig,
    *,
    seed: int | None = 42,
) -> ValueCacheQuantized:
    """Quantize Value-cache tensor(s) for reconstruction-optimal storage.

    Value cache only — minimizes ‖x - x̃‖² via TurboQuantMSE (random rotation +
    Lloyd-Max scalar quantization). For Key cache, use quantize_key_cache().
    """
    if not config.enabled:
        raise ValueError("KvQuantConfig.enabled must be True to quantize Value cache")
    arr = np.asarray(values, dtype=np.float32)
    d = int(arr.shape[-1])
    quantizer = TurboQuantMSE(d, config.bits, seed)
    q = quantizer.quantize(arr)
    return ValueCacheQuantized(
        indices=q.indices,
        norms=q.norms,
        d=d,
        bits=config.bits,
        seed=seed,
    )


def dequantize_value_cache(payload: ValueCacheQuantized) -> np.ndarray:
    """Reconstruct Value-cache tensor(s) from quantize_value_cache() output."""
    quantizer = TurboQuantMSE(payload.d, payload.bits, payload.seed)
    q = QuantizedMSE(indices=payload.indices, norms=payload.norms)
    return quantizer.dequantize(q)


def quantize_key_cache(keys: np.ndarray, config: KvQuantConfig) -> KeyCacheQuantized:
    """Quantize Key cache with KIVI-style per-channel groups + residual window.

    Key cache only — per-channel affine quantization grouped along the token axis,
    with the most recent residual_length tokens kept at full precision. Deterministic;
    no random rotation step.
    """
    if not config.key_quant_enabled:
        raise ValueError("KvQuantConfig.key_quant_enabled must be True to quantize Key cache")
    arr = np.asarray(keys, dtype=np.float32)
    quantizer = KeyChannelQuantizer(
        dim=int(arr.shape[-1]),
        bits=config.k_bits,
        group_size=config.group_size,
        residual_length=config.residual_length,
    )
    return quantizer.quantize_key_cache(arr)


def dequantize_key_cache(payload: KeyCacheQuantized, config: KvQuantConfig) -> np.ndarray:
    """Reconstruct Key cache from quantize_key_cache() output."""
    quantizer = KeyChannelQuantizer(
        dim=payload.dim,
        bits=payload.bits,
        group_size=payload.group_size,
        residual_length=payload.residual_length,
    )
    return quantizer.dequantize_key_cache(payload)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors (illustrative metric only)."""
    a_flat = np.asarray(a, dtype=np.float64).ravel()
    b_flat = np.asarray(b, dtype=np.float64).ravel()
    denom = float(np.linalg.norm(a_flat) * np.linalg.norm(b_flat))
    if denom < 1e-12:
        return 1.0
    return float(np.dot(a_flat, b_flat) / denom)

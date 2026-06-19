"""Clean-room KIVI-style per-channel Key cache quantization (NumPy).

Algorithm attribution: Liu et al., KIVI (ICML 2024, arXiv:2402.02750). No code copied
from https://github.com/jy-yuan/KIVI — independent NumPy reproduction of the published
per-channel grouped + residual-window scheme.

Determinism: unlike TurboQuant's random orthogonal rotation, every scale and code is a
deterministic function of the input tensor. Same keys + config always yield identical
quantized payloads.

Boundary policy (pre-residual token axis):
  - Let P = num_tokens - min(num_tokens, residual_length) be the prefix length.
  - Prefix tokens are partitioned into consecutive groups of up to group_size along the
    token axis. A final partial group with length P % group_size (when non-zero) is
    quantized with its own per-channel min/max — it is not padded, dropped, or merged
    into the residual window.
  - When num_tokens <= residual_length, the entire sequence is stored in the residual
    window at full precision and no prefix groups are emitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class KeyGroupQuantized:
    """One prefix token group with per-channel affine codes."""

    codes: np.ndarray
    mins: np.ndarray
    scales: np.ndarray


@dataclass(frozen=True)
class KeyCacheQuantized:
    """Quantized Key-cache payload from KeyChannelQuantizer."""

    groups: tuple[KeyGroupQuantized, ...]
    residual: np.ndarray
    dim: int
    bits: int
    group_size: int
    residual_length: int


class KeyChannelQuantizer:
    """Per-channel grouped Key quantizer with a full-precision residual window.

    Intended for Key cache tensors shaped (num_tokens, dim). Recent tokens within
    residual_length are kept at full precision for streaming decode; earlier tokens
    are grouped along the token axis and quantized with independent per-channel
    affine min/max scales within each group.
    """

    def __init__(
        self,
        dim: int,
        bits: int,
        group_size: int = 32,
        residual_length: int = 32,
    ) -> None:
        if dim < 1:
            raise ValueError(f"dim must be >= 1, got {dim}")
        if bits < 1 or bits > 8:
            raise ValueError(f"bits must be in [1, 8], got {bits}")
        if group_size < 1:
            raise ValueError(f"group_size must be >= 1, got {group_size}")
        if residual_length < 0:
            raise ValueError(f"residual_length must be >= 0, got {residual_length}")
        self.dim = dim
        self.bits = bits
        self.group_size = group_size
        self.residual_length = residual_length
        self._levels = (1 << bits) - 1

    def quantize_key_cache(self, keys: np.ndarray) -> KeyCacheQuantized:
        """Quantize Key cache tensor of shape (num_tokens, dim)."""
        arr = np.asarray(keys, dtype=np.float32)
        if arr.ndim != 2:
            raise ValueError(f"keys must be 2-D (num_tokens, dim), got shape {arr.shape}")
        if arr.shape[1] != self.dim:
            raise ValueError(f"expected dim={self.dim}, got {arr.shape[1]}")

        num_tokens = arr.shape[0]
        residual_count = min(num_tokens, self.residual_length)
        prefix_count = num_tokens - residual_count

        if residual_count > 0:
            residual = arr[-residual_count:].copy()
            prefix = arr[:-residual_count]
        else:
            residual = np.empty((0, self.dim), dtype=np.float32)
            prefix = arr

        groups: list[KeyGroupQuantized] = []
        offset = 0
        while offset < prefix_count:
            end = min(offset + self.group_size, prefix_count)
            chunk = prefix[offset:end]
            groups.append(_quantize_group_per_channel(chunk, self.bits))
            offset = end

        return KeyCacheQuantized(
            groups=tuple(groups),
            residual=residual,
            dim=self.dim,
            bits=self.bits,
            group_size=self.group_size,
            residual_length=self.residual_length,
        )

    def dequantize_key_cache(self, payload: KeyCacheQuantized) -> np.ndarray:
        """Reconstruct Key cache tensor of shape (num_tokens, dim)."""
        if payload.dim != self.dim:
            raise ValueError(f"payload dim={payload.dim} != quantizer dim={self.dim}")
        prefix_parts: list[np.ndarray] = []
        for group in payload.groups:
            prefix_parts.append(_dequantize_group_per_channel(group))
        prefix = (
            np.concatenate(prefix_parts, axis=0)
            if prefix_parts
            else np.empty((0, self.dim), dtype=np.float32)
        )
        if payload.residual.size == 0:
            return prefix
        if prefix.size == 0:
            return payload.residual.astype(np.float32, copy=False)
        return np.concatenate([prefix, payload.residual.astype(np.float32, copy=False)], axis=0)


def _quantize_group_per_channel(chunk: np.ndarray, bits: int) -> KeyGroupQuantized:
    """Per-channel affine quantize one token group, shape (g, dim)."""
    levels = (1 << bits) - 1
    mins = np.min(chunk, axis=0)
    maxs = np.max(chunk, axis=0)
    spans = maxs - mins
    scales = np.where(spans > 0, spans / levels, 1.0).astype(np.float32)
    safe_scales = np.where(spans > 0, scales, 1.0)
    normed = (chunk - mins) / safe_scales
    codes = np.clip(np.rint(normed), 0, levels).astype(np.uint8)
    return KeyGroupQuantized(
        codes=codes,
        mins=mins.astype(np.float32),
        scales=scales,
    )


def _dequantize_group_per_channel(group: KeyGroupQuantized) -> NDArray[np.float32]:
    """Reconstruct one token group from per-channel affine codes."""
    out = group.codes.astype(np.float32) * group.scales + group.mins
    return cast(NDArray[np.float32], out.astype(np.float32))


def quantize_group_per_token(
    chunk: np.ndarray, bits: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-token baseline: one min/max pair shared across all channels per token row."""
    levels = (1 << bits) - 1
    token_mins = np.min(chunk, axis=1, keepdims=True)
    token_maxs = np.max(chunk, axis=1, keepdims=True)
    spans = token_maxs - token_mins
    scales = np.where(spans > 0, spans / levels, 1.0).astype(np.float32)
    safe_scales = np.where(spans > 0, scales, 1.0)
    normed = (chunk - token_mins) / safe_scales
    codes = np.clip(np.rint(normed), 0, levels).astype(np.uint8)
    return codes, token_mins.astype(np.float32), scales


def dequantize_group_per_token(
    codes: np.ndarray,
    token_mins: np.ndarray,
    scales: np.ndarray,
) -> NDArray[np.float32]:
    """Reconstruct per-token baseline quantization."""
    out = codes.astype(np.float32) * scales + token_mins
    return cast(NDArray[np.float32], out.astype(np.float32))


def reconstruction_mse(original: np.ndarray, reconstructed: np.ndarray) -> float:
    """Mean squared error between two arrays of the same shape."""
    diff = np.asarray(original, dtype=np.float64) - np.asarray(reconstructed, dtype=np.float64)
    return float(np.mean(diff * diff))

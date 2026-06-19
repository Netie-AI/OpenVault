"""KIVI-style per-channel Key cache quantization tests."""

from __future__ import annotations

import numpy as np
import pytest

from openmw.key_channel_quant import (
    KeyChannelQuantizer,
    dequantize_group_per_token,
    quantize_group_per_token,
    reconstruction_mse,
)
from openmw.kv_quant import KvQuantConfig, dequantize_key_cache, quantize_key_cache
from openmw.prefetch_naive import NaivePrefetchConfig, lmcache_disk_config


def _synthetic_outlier_keys(num_tokens: int = 96, dim: int = 64, seed: int = 0) -> np.ndarray:
    """Keys with a few high-variance channels — KIVI's empirical premise."""
    rng = np.random.default_rng(seed)
    keys = rng.normal(0.0, 0.05, size=(num_tokens, dim)).astype(np.float32)
    for channel in (3, 17, 41):
        keys[:, channel] = rng.normal(0.0, 5.0, size=num_tokens).astype(np.float32)
    return keys


class TestKeyChannelQuantizer:
    def test_roundtrip_shape_and_dtype(self) -> None:
        keys = _synthetic_outlier_keys()
        quantizer = KeyChannelQuantizer(dim=64, bits=4, group_size=32, residual_length=32)
        payload = quantizer.quantize_key_cache(keys)
        restored = quantizer.dequantize_key_cache(payload)
        assert restored.shape == keys.shape
        assert restored.dtype == np.float32

    def test_residual_window_bit_exact(self) -> None:
        keys = _synthetic_outlier_keys(num_tokens=80)
        residual_length = 32
        quantizer = KeyChannelQuantizer(
            dim=64, bits=2, group_size=32, residual_length=residual_length
        )
        payload = quantizer.quantize_key_cache(keys)
        restored = quantizer.dequantize_key_cache(payload)
        np.testing.assert_array_equal(restored[-residual_length:], keys[-residual_length:])

    def test_sequence_shorter_than_residual_window(self) -> None:
        keys = np.arange(20 * 8, dtype=np.float32).reshape(20, 8)
        quantizer = KeyChannelQuantizer(dim=8, bits=4, group_size=32, residual_length=32)
        payload = quantizer.quantize_key_cache(keys)
        assert len(payload.groups) == 0
        assert payload.residual.shape == (20, 8)
        restored = quantizer.dequantize_key_cache(payload)
        np.testing.assert_array_equal(restored, keys)

    def test_inexact_group_divisibility(self) -> None:
        keys = np.ones((50, 16), dtype=np.float32)
        quantizer = KeyChannelQuantizer(dim=16, bits=4, group_size=32, residual_length=10)
        payload = quantizer.quantize_key_cache(keys)
        # prefix = 40 tokens -> one full group (32) + partial group (8)
        assert len(payload.groups) == 2
        assert payload.groups[0].codes.shape[0] == 32
        assert payload.groups[1].codes.shape[0] == 8
        restored = quantizer.dequantize_key_cache(payload)
        assert restored.shape == keys.shape
        np.testing.assert_array_equal(restored[-10:], keys[-10:])

    def test_exact_group_divisibility(self) -> None:
        keys = np.ones((64, 8), dtype=np.float32)
        quantizer = KeyChannelQuantizer(dim=8, bits=4, group_size=32, residual_length=0)
        payload = quantizer.quantize_key_cache(keys)
        assert len(payload.groups) == 2
        assert payload.groups[0].codes.shape[0] == 32
        assert payload.groups[1].codes.shape[0] == 32

    def test_per_channel_beats_per_token_on_outlier_channels(self) -> None:
        keys = _synthetic_outlier_keys(num_tokens=96, dim=64, seed=0)
        bits = 4
        group_size = 32
        residual_length = 0
        quantizer = KeyChannelQuantizer(
            dim=64,
            bits=bits,
            group_size=group_size,
            residual_length=residual_length,
        )
        channel_payload = quantizer.quantize_key_cache(keys)
        channel_restored = quantizer.dequantize_key_cache(channel_payload)
        channel_mse = reconstruction_mse(keys, channel_restored)

        token_parts: list[np.ndarray] = []
        for start in range(0, keys.shape[0], group_size):
            chunk = keys[start : start + group_size]
            codes, tmin, scales = quantize_group_per_token(chunk, bits)
            token_parts.append(dequantize_group_per_token(codes, tmin, scales))
        token_restored = np.concatenate(token_parts, axis=0)
        token_mse = reconstruction_mse(keys, token_restored)

        ratio = token_mse / channel_mse
        assert channel_mse < token_mse, (
            f"per-channel MSE {channel_mse:.6f} should beat per-token {token_mse:.6f}"
        )
        # Threshold from measured run on this repo (ratio ≈ 8.9 at seed=0).
        assert ratio > 3.0, f"expected material per-channel win, ratio={ratio:.2f}"


class TestKeyCacheWrapper:
    def test_quantize_requires_key_enabled(self) -> None:
        keys = _synthetic_outlier_keys(num_tokens=32)
        with pytest.raises(ValueError, match="key_quant_enabled"):
            quantize_key_cache(keys, KvQuantConfig(key_quant_enabled=False))

    def test_wrapper_roundtrip(self) -> None:
        keys = _synthetic_outlier_keys(num_tokens=64)
        config = KvQuantConfig(key_quant_enabled=True, k_bits=4, group_size=32, residual_length=16)
        payload = quantize_key_cache(keys, config)
        restored = dequantize_key_cache(payload, config)
        assert restored.shape == keys.shape
        np.testing.assert_array_equal(restored[-16:], keys[-16:])

    def test_lmcache_disk_config_key_quant_optional(self) -> None:
        base = lmcache_disk_config(NaivePrefetchConfig(), "/tmp/kv")
        assert "key_quant" not in base

        enabled = lmcache_disk_config(
            NaivePrefetchConfig(),
            "/tmp/kv",
            kv_quant=KvQuantConfig(
                key_quant_enabled=True, k_bits=2, group_size=32, residual_length=32
            ),
        )
        key_quant = enabled["key_quant"]
        assert isinstance(key_quant, dict)
        assert key_quant["enabled"] is True
        assert key_quant["bits"] == 2
        assert key_quant["target"] == "key_cache"

    def test_value_side_default_dict_unchanged(self) -> None:
        base = lmcache_disk_config(NaivePrefetchConfig(), "/tmp/kv")
        assert set(base.keys()) == {"backend", "path", "prefetch"}

"""Value-cache quantization tests — vendored TurboQuantMSE + OpenMW wrapper."""

from __future__ import annotations

import numpy as np
import pytest

from openmw.kv_quant import (
    KvQuantConfig,
    cosine_similarity,
    dequantize_value_cache,
    quantize_value_cache,
)
from openmw.prefetch_naive import NaivePrefetchConfig, lmcache_disk_config
from openmw.vendor.turboquant import VENDORED_COMMIT
from openmw.vendor.turboquant.mse_quantizer import TurboQuantMSE
from openmw.vendor.turboquant.utils import random_unit_vectors


def test_vendored_commit_pin() -> None:
    assert VENDORED_COMMIT == "34a10b639247dce1aa5f20e31428568586e6f52a"


class TestTurboQuantMSEDistortion:
    """Regression: extracted mse_quantizer matches upstream test_core.py thresholds."""

    @pytest.mark.parametrize(
        ("b", "max_distortion"),
        [
            (1, 0.50),
            (2, 0.18),
            (3, 0.05),
            (4, 0.015),
        ],
    )
    def test_distortion_matches_paper(self, b: int, max_distortion: float) -> None:
        d = 256
        tq = TurboQuantMSE(d, b, seed=42)
        dist = tq.distortion(n_samples=5000, seed=0)
        assert dist < max_distortion, f"b={b}: distortion {dist:.5f} > {max_distortion}"

    def test_higher_b_lower_distortion(self) -> None:
        d = 256
        distortions: list[float] = []
        for b in (1, 2, 3, 4):
            tq = TurboQuantMSE(d, b, seed=42)
            distortions.append(tq.distortion(n_samples=5000, seed=0))
        for i in range(len(distortions) - 1):
            assert distortions[i] > distortions[i + 1]

    def test_two_bit_materially_worse_than_four_bit(self) -> None:
        d = 256
        tq2 = TurboQuantMSE(d, 2, seed=42)
        tq4 = TurboQuantMSE(d, 4, seed=42)
        dist2 = tq2.distortion(n_samples=5000, seed=0)
        dist4 = tq4.distortion(n_samples=5000, seed=0)
        assert dist2 > dist4 * 3.0, f"2-bit {dist2:.5f} should exceed 4-bit {dist4:.5f} materially"


def test_illustrative_cosine_similarity_at_head_dim_128() -> None:
    """Single-draw readability example — not the statistical headline."""
    d = 128
    x = random_unit_vectors(1, d, seed=7)[0]
    config = KvQuantConfig(enabled=True, bits=4)
    payload = quantize_value_cache(x, config, seed=42)
    x_hat = dequantize_value_cache(payload)
    sim = cosine_similarity(x, x_hat)
    assert sim > 0.95


class TestValueCacheWrapper:
    def test_quantize_requires_enabled(self) -> None:
        x = random_unit_vectors(1, 128, seed=0)[0]
        with pytest.raises(ValueError, match="enabled"):
            quantize_value_cache(x, KvQuantConfig(enabled=False))

    def test_roundtrip_shape_batch(self) -> None:
        n, d = 16, 256
        x = random_unit_vectors(n, d, seed=11)
        config = KvQuantConfig(enabled=True, bits=3)
        payload = quantize_value_cache(x, config, seed=42)
        x_hat = dequantize_value_cache(payload)
        assert x_hat.shape == (n, d)

    def test_roundtrip_preserves_norm_approximately(self) -> None:
        d = 128
        rng = np.random.default_rng(0)
        x = rng.standard_normal(d).astype(np.float32) * 5.0
        payload = quantize_value_cache(x, KvQuantConfig(enabled=True, bits=4), seed=42)
        x_hat = dequantize_value_cache(payload)
        rel_err = abs(float(np.linalg.norm(x_hat)) - float(np.linalg.norm(x))) / float(
            np.linalg.norm(x)
        )
        assert rel_err < 0.2


def test_lmcache_disk_config_kv_quant_optional() -> None:
    base = lmcache_disk_config(NaivePrefetchConfig(), "/tmp/kv")
    assert "kv_quant" not in base
    assert set(base.keys()) == {"backend", "path", "prefetch"}

    enabled = lmcache_disk_config(
        NaivePrefetchConfig(),
        "/tmp/kv",
        kv_quant=KvQuantConfig(enabled=True, bits=4),
    )
    kv_quant = enabled["kv_quant"]
    assert isinstance(kv_quant, dict)
    assert kv_quant["enabled"] is True
    assert kv_quant["bits"] == 4
    assert kv_quant["target"] == "value_cache"

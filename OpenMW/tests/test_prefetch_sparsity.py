"""Neuron sparsity prefetch tests — mocked calibration, no GPU/NVMe."""

from __future__ import annotations

import numpy as np
import pytest

from openmw.prefetch_naive import NaivePrefetchConfig, lmcache_disk_config
from openmw.prefetch_sparsity import (
    HotNeuronIndex,
    SparsityPrefetchConfig,
    SparsityPrefetcher,
    build_hot_neuron_index,
    run_calibration,
)


def _mock_activation_provider(
    *,
    layers: int = 2,
    neurons: int = 8,
    hot_neurons: tuple[int, ...] = (0, 1),
) -> tuple[np.ndarray, object]:
    """Return (stacked activations, provider callable) for calibration."""
    rng = np.random.default_rng(42)
    samples = np.zeros((4, layers, neurons), dtype=np.float32)
    for token in range(samples.shape[0]):
        for layer in range(layers):
            samples[token, layer, list(hot_neurons)] = 1.0
            cold = rng.integers(2, neurons, size=2)
            samples[token, layer, cold] = 1.0

    def provider(token_idx: int) -> np.ndarray:
        return samples[token_idx % samples.shape[0]]

    return samples, provider


class TestHotNeuronIndex:
    def test_build_hot_neuron_index_threshold(self) -> None:
        activations = np.zeros((4, 2, 4), dtype=np.float32)
        activations[:, 0, 0] = 1.0
        activations[:, 0, 1] = 1.0
        activations[0:3, 0, 2] = 1.0
        index = build_hot_neuron_index(activations, hot_threshold=0.80)
        assert index.hot_neurons(0) == frozenset({0, 1})

    def test_register_and_query(self) -> None:
        index = HotNeuronIndex()
        index.register_hot(0, {1, 3})
        assert index.hot_neurons(0) == frozenset({1, 3})
        assert index.layer_ids() == [0]


class TestCalibration:
    def test_run_calibration_mock_provider(self) -> None:
        _, provider = _mock_activation_provider(layers=2, neurons=8, hot_neurons=(0, 1))
        config = SparsityPrefetchConfig(
            enabled=True,
            calibration_tokens=4,
            layer_count=2,
            hot_threshold=0.80,
        )
        index = run_calibration(config, provider)
        assert 0 in index.hot_neurons(0)
        assert 1 in index.hot_neurons(0)

    def test_calibration_rejects_bad_shape(self) -> None:
        config = SparsityPrefetchConfig(enabled=True, calibration_tokens=2, layer_count=2)

        def bad_provider(_: int) -> np.ndarray:
            return np.zeros((3,), dtype=np.float32)

        with pytest.raises(ValueError):
            run_calibration(config, bad_provider)


class TestSparsityPrefetcher:
    def test_predict_hot_only(self) -> None:
        index = HotNeuronIndex()
        index.register_hot(0, {0, 2, 5})
        config = SparsityPrefetchConfig(enabled=True, layer_count=4, prefetch_batch_size=2)
        prefetcher = SparsityPrefetcher(config, index)
        predicted = prefetcher.predict_active_neurons(0)
        assert predicted == [0, 2]

    def test_predict_merges_token_activations(self) -> None:
        index = HotNeuronIndex()
        index.register_hot(1, {0})
        config = SparsityPrefetchConfig(enabled=True, layer_count=4, prefetch_batch_size=64)
        prefetcher = SparsityPrefetcher(config, index)
        token_acts = np.zeros(8, dtype=np.float32)
        token_acts[3] = 1.0
        token_acts[7] = 1.0
        predicted = prefetcher.predict_active_neurons(1, token_acts)
        assert set(predicted) == {0, 3, 7}

    def test_prefetch_plan(self) -> None:
        index = HotNeuronIndex()
        index.register_hot(0, {1})
        config = SparsityPrefetchConfig(enabled=True, layer_count=2)
        prefetcher = SparsityPrefetcher(config, index)
        plan = prefetcher.prefetch_plan(0)
        assert plan["layer"] == 0
        assert plan["neuron_ids"] == [1]
        assert plan["hot_only"] is True

    def test_lmcache_section(self) -> None:
        index = HotNeuronIndex()
        index.register_hot(0, {2, 0})
        config = SparsityPrefetchConfig(enabled=True, layer_count=2, calibration_tokens=512)
        prefetcher = SparsityPrefetcher(config, index)
        section = prefetcher.lmcache_section()
        assert section["enabled"] is True
        assert section["calibration_tokens"] == 512
        assert section["hot_neurons"] == {"0": [0, 2]}


class TestLmcacheDiskConfig:
    def test_sparsity_key_only_when_enabled(self) -> None:
        disabled = lmcache_disk_config(
            NaivePrefetchConfig(),
            "/tmp/kv",
            sparsity=SparsityPrefetchConfig(enabled=False),
        )
        assert "sparsity_prefetch" not in disabled

        enabled = lmcache_disk_config(
            NaivePrefetchConfig(),
            "/tmp/kv",
            sparsity=SparsityPrefetchConfig(enabled=True, calibration_tokens=256),
        )
        sparsity = enabled["sparsity_prefetch"]
        assert isinstance(sparsity, dict)
        assert sparsity["enabled"] is True
        assert sparsity["calibration_tokens"] == 256

"""Phase-3 neuron sparsity prefetch: PowerInfer-inspired hot-neuron calibration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

_DEFAULT_HOT_THRESHOLD = 0.80
_DEFAULT_CALIBRATION_TOKENS = 512
_DEFAULT_PREFETCH_BATCH_SIZE = 64


ActivationProvider = Callable[[int], np.ndarray]


@dataclass(frozen=True)
class SparsityPrefetchConfig:
    """Sparsity-aware weight prefetch using offline neuron calibration."""

    enabled: bool = False
    hot_threshold: float = _DEFAULT_HOT_THRESHOLD
    calibration_tokens: int = _DEFAULT_CALIBRATION_TOKENS
    layer_count: int = 32
    prefetch_batch_size: int = _DEFAULT_PREFETCH_BATCH_SIZE


@dataclass
class HotNeuronIndex:
    """Per-layer set of neurons active in >hot_threshold of calibration tokens."""

    hot_by_layer: dict[int, set[int]] = field(default_factory=dict)

    def hot_neurons(self, layer: int) -> frozenset[int]:
        """Return immutable hot-neuron ids for *layer*."""
        return frozenset(self.hot_by_layer.get(layer, set()))

    def register_hot(self, layer: int, neuron_ids: set[int]) -> None:
        """Replace hot-neuron set for *layer*."""
        self.hot_by_layer[layer] = set(neuron_ids)

    def layer_ids(self) -> list[int]:
        """Return sorted layer indices with calibration data."""
        return sorted(self.hot_by_layer)


def build_hot_neuron_index(
    activations: np.ndarray,
    *,
    hot_threshold: float = _DEFAULT_HOT_THRESHOLD,
) -> HotNeuronIndex:
    """Build hot-neuron index from calibration activation frequencies.

    *activations* shape: (calibration_tokens, layer_count, neuron_count) with
    values in {0, 1} or any positive threshold for "active".
    """
    if activations.ndim != 3:
        raise ValueError(f"activations must be 3-D, got shape {activations.shape}")
    if not 0.0 < hot_threshold <= 1.0:
        raise ValueError(f"hot_threshold must be in (0, 1], got {hot_threshold}")

    active = activations > 0
    token_count = activations.shape[0]
    freq = active.sum(axis=0) / float(token_count)

    index = HotNeuronIndex()
    for layer in range(freq.shape[0]):
        hot_mask = freq[layer] >= hot_threshold
        hot_ids = {int(i) for i in np.flatnonzero(hot_mask)}
        index.register_hot(layer, hot_ids)
    return index


def run_calibration(
    config: SparsityPrefetchConfig,
    activation_provider: ActivationProvider,
) -> HotNeuronIndex:
    """Run a calibration pass over *config.calibration_tokens* sample tokens."""
    if config.calibration_tokens < 1:
        raise ValueError(f"calibration_tokens must be >= 1, got {config.calibration_tokens}")
    if config.layer_count < 1:
        raise ValueError(f"layer_count must be >= 1, got {config.layer_count}")

    samples: list[np.ndarray] = []
    for token_idx in range(config.calibration_tokens):
        layer_acts = activation_provider(token_idx)
        if layer_acts.ndim != 2:
            raise ValueError(
                f"activation_provider must return 2-D (layers, neurons), got {layer_acts.shape}"
            )
        if layer_acts.shape[0] != config.layer_count:
            raise ValueError(
                f"expected {config.layer_count} layers, got {layer_acts.shape[0]}"
            )
        samples.append(layer_acts)

    stacked = np.stack(samples, axis=0)
    return build_hot_neuron_index(stacked, hot_threshold=config.hot_threshold)


class SparsityPrefetcher:
    """Prefetch weight regions for predicted active neurons only."""

    def __init__(
        self,
        config: SparsityPrefetchConfig,
        index: HotNeuronIndex,
    ) -> None:
        self._config = config
        self._index = index

    @property
    def index(self) -> HotNeuronIndex:
        """Calibrated hot-neuron index."""
        return self._index

    def predict_active_neurons(
        self,
        layer: int,
        token_activations: np.ndarray | None = None,
    ) -> list[int]:
        """Return neuron ids to prefetch for *layer* on the current token."""
        if layer < 0 or layer >= self._config.layer_count:
            raise IndexError(f"layer {layer} out of range [0, {self._config.layer_count})")

        hot = set(self._index.hot_neurons(layer))
        if token_activations is not None:
            if token_activations.ndim != 1:
                raise ValueError(
                    f"token_activations must be 1-D, got shape {token_activations.shape}"
                )
            cold_active = {int(i) for i in np.flatnonzero(token_activations > 0)}
            hot.update(cold_active)

        ordered = sorted(hot)
        batch = self._config.prefetch_batch_size
        if batch < 1:
            return ordered
        return ordered[:batch] if len(ordered) > batch else ordered

    def prefetch_plan(
        self,
        layer: int,
        token_activations: np.ndarray | None = None,
    ) -> dict[str, object]:
        """Return a prefetch plan dict for LMCache integration."""
        neurons = self.predict_active_neurons(layer, token_activations)
        return {
            "layer": layer,
            "neuron_ids": neurons,
            "prefetch_batch_size": self._config.prefetch_batch_size,
            "hot_only": token_activations is None,
        }

    def lmcache_section(self) -> dict[str, object]:
        """Return LMCache sparsity-prefetch config section."""
        hot_layers: dict[str, list[int]] = {
            str(layer): sorted(self._index.hot_neurons(layer))
            for layer in self._index.layer_ids()
        }
        return {
            "enabled": True,
            "hot_threshold": self._config.hot_threshold,
            "calibration_tokens": self._config.calibration_tokens,
            "layer_count": self._config.layer_count,
            "prefetch_batch_size": self._config.prefetch_batch_size,
            "hot_neurons": hot_layers,
        }

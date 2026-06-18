# Extracted from scos-lab/turboquant core.py (TurboQuantMSE + QuantizedMSE only)
# Source commit: 34a10b639247dce1aa5f20e31428568586e6f52a
# License: MIT — see OpenMW/THIRD_PARTY_NOTICES.md
# Paper: Zandieh et al., TurboQuant (ICLR 2026, arXiv:2504.19874)
# TurboQuantProd / qjl.py intentionally omitted — see THIRD_PARTY_NOTICES.md.

"""TurboQuantMSE — reconstruction-optimal quantizer for Value cache vectors."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from openmw.vendor.turboquant.rotation import generate_rotation, inverse_rotate, rotate
from openmw.vendor.turboquant.scalar_quantizer import (
    compute_centroids,
    dequantize_scalar,
    quantize_scalar,
)
from openmw.vendor.turboquant.utils import normalize, random_unit_vectors


@dataclass(frozen=True)
class QuantizedMSE:
    """Compressed representation from TurboQuantMSE."""

    indices: np.ndarray
    norms: np.ndarray


class TurboQuantMSE:
    """MSE-optimal quantizer via random rotation + Beta-distribution scalar quantization.

    Upstream design intent: Value cache / vector reconstruction (minimizes ‖x - x̃‖²).
    Do not use for Key cache — inner products require a different quantizer path.
    """

    def __init__(self, d: int, b: int, seed: int | None = None) -> None:
        if b < 1 or b > 8:
            raise ValueError(f"Bit-width b must be in [1, 8], got {b}")
        if d < 3:
            raise ValueError(f"Dimension d must be >= 3, got {d}")
        self.d = d
        self.b = b
        self.rotation = generate_rotation(d, seed)
        centroids_boundaries = compute_centroids(d, b)
        self.centroids = centroids_boundaries[0]
        self.boundaries = centroids_boundaries[1]

    def quantize(self, x: np.ndarray) -> QuantizedMSE:
        """Quantize vector(s) to compressed representation."""
        x_arr = np.asarray(x, dtype=np.float32)
        x_hat, norms = normalize(x_arr)
        y = rotate(x_hat, self.rotation)
        indices = quantize_scalar(y, self.boundaries)
        return QuantizedMSE(indices=indices, norms=norms)

    def dequantize(self, q: QuantizedMSE) -> np.ndarray:
        """Reconstruct vector(s) from compressed representation."""
        y_hat = dequantize_scalar(q.indices, self.centroids)
        x_hat = inverse_rotate(y_hat, self.rotation)
        if q.norms.ndim == 0:
            return x_hat * float(q.norms)
        return x_hat * q.norms[:, np.newaxis]

    def distortion(self, n_samples: int = 10000, seed: int | None = 42) -> float:
        """Empirical MSE distortion over random unit vectors."""
        x = random_unit_vectors(n_samples, self.d, seed)
        q = self.quantize(x)
        x_hat = self.dequantize(q)
        mse = np.mean(np.sum((x - x_hat) ** 2, axis=1))
        return float(mse)

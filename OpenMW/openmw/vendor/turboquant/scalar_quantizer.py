# Vendored from scos-lab/turboquant @ 34a10b639247dce1aa5f20e31428568586e6f52a
# License: MIT — see OpenMW/THIRD_PARTY_NOTICES.md
# Paper: Zandieh et al., TurboQuant (ICLR 2026, arXiv:2504.19874)
# Third-party reproduction via scos-lab; not an official Google Research release.

"""Optimal scalar quantizer for Beta-distributed coordinates."""

from __future__ import annotations

import functools

import numpy as np
from scipy import integrate, stats


def _beta_pdf(x: np.ndarray, d: int) -> np.ndarray:
    """PDF of a single coordinate of a uniformly random unit vector in ℝᵈ."""
    if d <= 2:
        raise ValueError(f"d must be >= 3, got {d}")
    alpha = (d - 1) / 2
    return stats.beta.pdf((x + 1) / 2, alpha, alpha) / 2


def _conditional_mean(a: float, b: float, d: int) -> float:
    """E[X | a ≤ X ≤ b] under the Beta PDF on [-1, 1]."""
    num, _ = integrate.quad(lambda x: x * _beta_pdf(np.array(x), d), a, b)
    den, _ = integrate.quad(lambda x: _beta_pdf(np.array(x), d), a, b)
    if den < 1e-15:
        return (a + b) / 2
    return num / den


@functools.lru_cache(maxsize=64)
def compute_centroids(d: int, b: int, max_iter: int = 200, tol: float = 1e-10) -> tuple:
    """Compute optimal scalar quantization centroids via Lloyd's algorithm."""
    k = 2**b
    alpha = (d - 1) / 2
    quantile_points = np.linspace(0.5 / k, 1 - 0.5 / k, k)
    centroids = 2 * stats.beta.ppf(quantile_points, alpha, alpha) - 1

    for _ in range(max_iter):
        boundaries = np.empty(k + 1)
        boundaries[0] = -1.0
        boundaries[-1] = 1.0
        boundaries[1:-1] = (centroids[:-1] + centroids[1:]) / 2

        new_centroids = np.empty(k)
        for i in range(k):
            new_centroids[i] = _conditional_mean(boundaries[i], boundaries[i + 1], d)

        shift = np.max(np.abs(new_centroids - centroids))
        centroids = new_centroids
        if shift < tol:
            break

    boundaries = np.empty(k + 1)
    boundaries[0] = -1.0
    boundaries[-1] = 1.0
    boundaries[1:-1] = (centroids[:-1] + centroids[1:]) / 2

    return (
        centroids.astype(np.float32),
        boundaries.astype(np.float32),
    )


def quantize_scalar(values: np.ndarray, boundaries: np.ndarray) -> np.ndarray:
    """Quantize values to nearest centroid bin index."""
    return np.searchsorted(boundaries[1:-1], values).astype(np.uint8)


def dequantize_scalar(indices: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """Map bin indices back to centroid values."""
    return centroids[indices]

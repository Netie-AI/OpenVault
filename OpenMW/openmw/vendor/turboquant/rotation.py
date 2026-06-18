# Vendored from scos-lab/turboquant @ 34a10b639247dce1aa5f20e31428568586e6f52a
# License: MIT — see OpenMW/THIRD_PARTY_NOTICES.md
# Paper: Zandieh et al., TurboQuant (ICLR 2026, arXiv:2504.19874)
# Third-party reproduction via scos-lab; not an official Google Research release.

"""Random rotation matrix generation and application.

A random orthogonal rotation transforms unit sphere vectors so that each
coordinate follows a known Beta distribution, enabling precomputed optimal
scalar quantization (data-oblivious).
"""

from __future__ import annotations

import numpy as np


def generate_rotation(d: int, seed: int | None = None) -> np.ndarray:
    """Generate a random orthogonal rotation matrix via QR decomposition.

    Args:
        d: Vector dimension.
        seed: Random seed for reproducibility.

    Returns:
        Orthogonal matrix Π ∈ ℝ^(d×d), dtype float32.
    """
    rng = np.random.default_rng(seed)
    g = rng.standard_normal((d, d)).astype(np.float32)
    q, r = np.linalg.qr(g)
    signs = np.sign(np.diag(r))
    signs[signs == 0] = 1.0
    q = q * signs[np.newaxis, :]
    return q.astype(np.float32)


def rotate(x: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    """Apply rotation: y = x @ Πᵀ (supports batched input)."""
    return x @ rotation.T


def inverse_rotate(y: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    """Apply inverse rotation: x̃ = y @ Π."""
    return y @ rotation

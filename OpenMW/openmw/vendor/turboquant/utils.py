# Vendored from scos-lab/turboquant @ 34a10b639247dce1aa5f20e31428568586e6f52a
# License: MIT — see OpenMW/THIRD_PARTY_NOTICES.md
# Paper: Zandieh et al., TurboQuant (ICLR 2026, arXiv:2504.19874)
# Third-party reproduction via scos-lab; not an official Google Research release.

"""Utility functions for TurboQuant."""

from __future__ import annotations

import numpy as np


def normalize(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Separate norm and direction."""
    if x.ndim == 1:
        norm = np.linalg.norm(x)
        if norm < 1e-10:
            return x, np.float32(norm)
        return (x / norm).astype(np.float32), np.float32(norm)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms_flat = norms.squeeze(-1).astype(np.float32)
    safe_norms = np.where(norms < 1e-10, 1.0, norms)
    x_hat = (x / safe_norms).astype(np.float32)
    return x_hat, norms_flat


def random_unit_vectors(n: int, d: int, seed: int | None = None) -> np.ndarray:
    """Generate n random unit vectors in ℝᵈ."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, d)).astype(np.float32)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return x / norms

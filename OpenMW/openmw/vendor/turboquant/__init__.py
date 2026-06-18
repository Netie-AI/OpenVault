"""Vendored TurboQuant MSE quantizer (rotation + Lloyd-Max scalar codebook)."""

VENDORED_COMMIT = "34a10b639247dce1aa5f20e31428568586e6f52a"

from openmw.vendor.turboquant.mse_quantizer import QuantizedMSE, TurboQuantMSE

__all__ = ["VENDORED_COMMIT", "QuantizedMSE", "TurboQuantMSE"]

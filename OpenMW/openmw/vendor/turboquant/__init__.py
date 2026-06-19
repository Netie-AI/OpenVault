"""Vendored TurboQuant MSE quantizer (rotation + Lloyd-Max scalar codebook)."""

from openmw.vendor.turboquant.mse_quantizer import QuantizedMSE, TurboQuantMSE

VENDORED_COMMIT = "34a10b639247dce1aa5f20e31428568586e6f52a"

__all__ = ["VENDORED_COMMIT", "QuantizedMSE", "TurboQuantMSE"]

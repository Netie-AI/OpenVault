"""Import numpy before pytest-cov starts tracing.

Coverage's tracer plus a first-time numpy import reloads _multiarray_umath
and leaves ndarray.sum broken. Loading numpy at plugin-import time (before
cov.start) keeps the C extension as a single process singleton.
"""

from __future__ import annotations

import numpy as np

__all__ = ["np"]

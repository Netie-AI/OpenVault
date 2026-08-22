"""Optional Redis + Lua bucket store seam.

STATUS.md: Redis cluster store is not done. `create_app` always falls back to
`InMemoryBucketStore` until a Lua backend lands. This module exists so the
import in `app.py` cannot take down the console.
"""

from __future__ import annotations

import os

from openmw.openvault.vault.ratelimit import BucketStore


def try_make_redis_store() -> BucketStore | None:
    """Return None so TokenBudgetLimiter stays on the in-memory store."""
    _ = os.environ.get("REDIS_URL", "").strip()
    return None

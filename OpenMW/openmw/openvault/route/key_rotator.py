"""Per-connection API key round-robin with health tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

KeyStatus = Literal["active", "warning", "invalid"]

FAILURE_THRESHOLD = 2
MAX_KEY_HEALTH_ENTRIES = 500
MAX_CONNECTION_EXTRA_KEYS = 500


@dataclass
class KeyHealth:
    status: KeyStatus = "active"
    failures: int = 0
    last_failure: str | None = None
    last_success: str | None = None
    total_requests: int = 0
    total_failures: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "failures": self.failures,
            "lastFailure": self.last_failure,
            "lastSuccess": self.last_success,
            "totalRequests": self.total_requests,
            "totalFailures": self.total_failures,
        }


_key_indexes: dict[str, int] = {}
_key_health: dict[str, KeyHealth] = {}
_connection_extra_keys: dict[str, bool] = {}


def _scoped_key(connection_id: str, key_id: str) -> str:
    return f"{connection_id}:{key_id}"


def _evict_oldest(store: dict[str, object]) -> None:
    if not store:
        return
    oldest = next(iter(store))
    del store[oldest]


def _get_or_create_health(connection_id: str, key_id: str) -> KeyHealth:
    scoped = _scoped_key(connection_id, key_id)
    if scoped not in _key_health:
        if len(_key_health) >= MAX_KEY_HEALTH_ENTRIES:
            _evict_oldest(_key_health)
        _key_health[scoped] = KeyHealth()
    return _key_health[scoped]


def track_connection_extra_keys(connection_id: str, extra_keys: list[str]) -> None:
    valid = [k for k in extra_keys if isinstance(k, str) and k.strip()]
    if (
        connection_id not in _connection_extra_keys
        and len(_connection_extra_keys) >= MAX_CONNECTION_EXTRA_KEYS
    ):
        _evict_oldest(_connection_extra_keys)
    _connection_extra_keys[connection_id] = len(valid) > 0


def connection_has_extra_keys(connection_id: str, extra_keys: list[str] | None = None) -> bool:
    if extra_keys:
        return any(isinstance(k, str) and k.strip() for k in extra_keys)
    return _connection_extra_keys.get(connection_id, False)


def get_valid_api_key(
    connection_id: str,
    primary_key: str,
    extra_keys: list[str] | None = None,
    health: dict[str, KeyHealth] | None = None,
) -> tuple[str, str] | None:
    """Round-robin across valid keys; skip keys marked invalid."""
    extras = extra_keys or []
    valid_extras = [k for k in extras if isinstance(k, str) and k.strip()]
    candidates: list[tuple[str, str]] = []

    if primary_key:
        h = (health or {}).get("primary") or _get_or_create_health(connection_id, "primary")
        if h.status != "invalid":
            candidates.append((primary_key, "primary"))

    for i, key in enumerate(valid_extras):
        key_id = f"extra_{i}"
        h = (health or {}).get(key_id) or _get_or_create_health(connection_id, key_id)
        if h.status != "invalid":
            candidates.append((key, key_id))

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    current = _key_indexes.get(connection_id, 0)
    idx = current % len(candidates)
    _key_indexes[connection_id] = current + 1
    return candidates[idx]


def record_key_failure(connection_id: str, key_id: str) -> KeyHealth:
    health = _get_or_create_health(connection_id, key_id)
    health.failures += 1
    health.total_requests += 1
    health.total_failures += 1
    health.last_failure = _iso_now()
    if health.failures >= FAILURE_THRESHOLD:
        health.status = "invalid"
    elif health.failures > 0:
        health.status = "warning"
    return health


def record_key_success(connection_id: str, key_id: str) -> KeyHealth:
    health = _get_or_create_health(connection_id, key_id)
    health.failures = 0
    health.total_requests += 1
    health.last_success = _iso_now()
    health.status = "active"
    return health


def reset_key_status(connection_id: str, key_id: str) -> KeyHealth:
    health = _get_or_create_health(connection_id, key_id)
    health.failures = 0
    health.status = "active"
    health.last_failure = None
    return health


def get_all_key_health() -> dict[str, KeyHealth]:
    return dict(_key_health)


def reset_all() -> None:
    _key_indexes.clear()
    _key_health.clear()
    _connection_extra_keys.clear()


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

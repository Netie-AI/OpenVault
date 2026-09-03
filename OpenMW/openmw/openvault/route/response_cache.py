"""Exact-match response cache — OmniRoute-class, not embeddings.

OmniRoute's "semantic cache" has no embeddings. It is a SHA-256 of the
request that only fires on explicit ``temperature: 0``. We do the same,
namespaced by tenant (issued API key / loopback identity) so two callers
cannot read each other's completions. Hits set ``HopTrace.cache_hit`` so
the usage ledger's existing column records a zero-billable row.

Never invent a completion: only store a 200 JSON body we already returned.
Streams are not cached. Missing temperature is not 0 (OpenAI default is 1).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

MAX_ENTRIES = 256
TTL_S = 300.0

_lock = threading.Lock()
_store: dict[str, tuple[float, dict[str, Any]]] = {}


def reset() -> None:
    with _lock:
        _store.clear()


def cacheable(body: dict[str, Any]) -> bool:
    """True only for non-stream chat with an explicit temperature of 0."""
    if body.get("stream"):
        return False
    temp = body.get("temperature")
    if temp is None:
        return False
    try:
        return float(temp) == 0.0
    except (TypeError, ValueError):
        return False


def cache_key(tenant: str, body: dict[str, Any]) -> str:
    """Tenant-scoped SHA-256 of the fields that change the completion."""
    payload = {
        "tenant": tenant or "",
        "model": body.get("model"),
        "messages": body.get("messages"),
        "tools": body.get("tools"),
        "tool_choice": body.get("tool_choice"),
        "response_format": body.get("response_format"),
        "max_tokens": body.get("max_tokens"),
        "temperature": body.get("temperature"),
    }
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def lookup(tenant: str, body: dict[str, Any]) -> dict[str, Any] | None:
    if not cacheable(body):
        return None
    key = cache_key(tenant, body)
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if now >= expires_at:
            del _store[key]
            return None
        return json.loads(json.dumps(payload))


def store(tenant: str, body: dict[str, Any], payload: dict[str, Any]) -> None:
    if not cacheable(body):
        return
    if not isinstance(payload, dict) or payload.get("error") is not None:
        return
    key = cache_key(tenant, body)
    copy = json.loads(json.dumps(payload, default=str))
    expires_at = time.monotonic() + TTL_S
    with _lock:
        if len(_store) >= MAX_ENTRIES and key not in _store:
            oldest = next(iter(_store))
            del _store[oldest]
        _store[key] = (expires_at, copy)

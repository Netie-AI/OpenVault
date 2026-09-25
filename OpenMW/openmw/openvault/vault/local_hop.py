"""LOCAL-1 FreeRoute hop: one Qwen-class OpenAI-compat server on loopback.

Needs no cloud key and is not stored in the vault. Operator config is env only:

* ``OPENVAULT_LOCAL_BASE_URL`` -- llama.cpp (``http://127.0.0.1:8080/v1``) or
  Ollama (``http://127.0.0.1:11434/v1``). Host must be loopback.
* ``OPENVAULT_LOCAL_MODEL`` -- default ``qwen2.5:0.5b``.

This module does not start a server, bind a port, or download a model.

Wire contract (OV#70 / Cortex#272, names from Cortex#274 ``a9f3fa03``):
``served_provider``, ``served_model``, ``served_local`` on the chat JSON;
``local_only`` on the request; 503 ``openvault_local_only_unavailable``;
status hop ``served_local`` / ``local_reason`` (always a string).
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from openmw.openvault.vault.providers import (
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_LOCAL_MODEL,
    LOCAL_BASE_URL_ENV,
    LOCAL_HOP_KEY_ID,
    LOCAL_MODEL_ENV,
    LOCAL_QWEN_ID,
    get_provider,
)

log = structlog.get_logger()

REASON_UNREACHABLE = "local_unreachable"
REASON_MODEL_NOT_LOADED = "local_model_not_loaded"
REASON_NOT_LOOPBACK = "local_base_url_not_loopback"
LOCAL_ONLY_ERROR_TYPE = "openvault_local_only_unavailable"
LOCAL_ONLY_MESSAGE = "local-only request cannot be served; no local hop succeeded"

SERVED_PROVIDER_HEADER = "X-OpenVault-Served-Provider"
SERVED_MODEL_HEADER = "X-OpenVault-Served-Model"
SERVED_LOCAL_HEADER = "X-OpenVault-Served-Local"

LOCAL_PLACEHOLDER_SECRET = "local"

_PROBE_TIMEOUT_S = 0.8


@dataclass(frozen=True)
class LocalProbe:
    """Result of probing the configured loopback server. No secrets."""

    reason: str | None
    model: str
    base_url: str
    latency_ms: float | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.reason is None


def configured_model() -> str:
    return (os.environ.get(LOCAL_MODEL_ENV) or "").strip() or DEFAULT_LOCAL_MODEL


def configured_base_url() -> str:
    """Operator override. Empty means the hop is listed but not walked."""
    return (os.environ.get(LOCAL_BASE_URL_ENV) or "").strip()


def local_base_url_for_status() -> str:
    return configured_base_url() or DEFAULT_LOCAL_BASE_URL


def is_loopback_base_url(url: str) -> bool:
    """True only for 127.0.0.0/8, ::1, or localhost that resolves only to loopback.

    Other hostnames are refused without DNS (SSRF). ``localhost`` is the one
    name we resolve, and every address must be loopback or we refuse.
    """
    raw = (url or "").strip()
    if not raw:
        return False
    parsed = urllib.parse.urlsplit(raw if "://" in raw else f"http://{raw}")
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip()
    if not host:
        return False
    if host.lower() == "localhost":
        return _localhost_resolves_loopback()
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = addr.ipv4_mapped if isinstance(addr, ipaddress.IPv6Address) else None
    return (mapped or addr).is_loopback


def loopback_refusal_reason(url: str) -> str | None:
    """Named reason if this URL must not be contacted, else None."""
    if not url.strip():
        return REASON_UNREACHABLE
    if not is_loopback_base_url(url):
        return REASON_NOT_LOOPBACK
    return None


def _localhost_resolves_loopback() -> bool:
    try:
        infos = socket.getaddrinfo("localhost", None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        mapped = addr.ipv4_mapped if isinstance(addr, ipaddress.IPv6Address) else None
        if not (mapped or addr).is_loopback:
            return False
    return True


def pop_local_only(body: dict[str, Any]) -> bool:
    """Remove ``local_only`` so it never reaches an upstream. True only for JSON true."""
    if "local_only" not in body:
        return False
    value = body.pop("local_only")
    return value is True


def local_only_refusal(reason: str) -> tuple[int, dict[str, Any]]:
    return 503, {
        "error": {
            "message": LOCAL_ONLY_MESSAGE,
            "type": LOCAL_ONLY_ERROR_TYPE,
            "reason": reason,
        }
    }


def served_response_headers(
    *,
    provider: str,
    model: str,
    served_local: bool,
) -> dict[str, str]:
    if not provider:
        return {}
    return {
        SERVED_PROVIDER_HEADER: provider,
        SERVED_MODEL_HEADER: model,
        SERVED_LOCAL_HEADER: "true" if served_local else "false",
    }


def attach_served_fields(
    payload: dict[str, Any],
    *,
    provider: str,
    model: str,
    served_local: bool,
) -> dict[str, Any]:
    stamped = dict(payload)
    stamped["served_provider"] = provider
    stamped["served_model"] = model
    stamped["served_local"] = served_local
    return stamped


def inject_served_into_sse_chunk(
    chunk: bytes,
    *,
    provider: str,
    model: str,
    served_local: bool,
) -> bytes:
    """Add served_* to complete SSE ``data: {...}`` lines. Pass incomplete tails through."""
    if not chunk or b"data:" not in chunk:
        return chunk
    ends_nl = chunk.endswith(b"\n")
    parts = chunk.split(b"\n")
    keep_tail = None if ends_nl else parts[-1]
    iterable = parts if ends_nl else parts[:-1]
    rebuilt = [
        _inject_sse_line(line, provider=provider, model=model, served_local=served_local)
        for line in iterable
    ]
    if keep_tail is not None:
        rebuilt.append(keep_tail)
    return b"\n".join(rebuilt)


def _inject_sse_line(
    line: bytes,
    *,
    provider: str,
    model: str,
    served_local: bool,
) -> bytes:
    if not line.startswith(b"data: "):
        return line
    payload = line[6:].strip()
    if payload == b"[DONE]" or not payload.startswith(b"{"):
        return line
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return line
    if not isinstance(obj, dict):
        return line
    obj["served_provider"] = provider
    obj["served_model"] = model
    obj["served_local"] = served_local
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8")


def models_listed(payload: Any) -> set[str]:
    """Parse OpenAI ``/v1/models`` or Ollama ``/api/tags`` into id strings."""
    found: set[str] = set()
    if not isinstance(payload, dict):
        return found
    rows = payload.get("data")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                for key in ("id", "name"):
                    value = str(row.get(key) or "").strip()
                    if value:
                        found.add(value)
            elif isinstance(row, str) and row.strip():
                found.add(row.strip())
    tags = payload.get("models")
    if isinstance(tags, list):
        for row in tags:
            if isinstance(row, dict):
                value = str(row.get("name") or row.get("model") or "").strip()
                if value:
                    found.add(value)
            elif isinstance(row, str) and row.strip():
                found.add(row.strip())
    return found


def chat_error_is_model_missing(status: int, body_text: str, model: str) -> bool:
    if status not in (404, 400):
        return False
    text = (body_text or "").lower()
    needle = model.lower()
    if "not found" in text or "does not exist" in text or "unknown model" in text:
        return True
    return bool(needle) and needle in text and "model" in text


def probe_local(
    *,
    client: httpx.Client | None = None,
    timeout_s: float = _PROBE_TIMEOUT_S,
) -> LocalProbe:
    """GET ``{base}/models``. Never contacts a non-loopback host."""
    import time

    model = configured_model()
    base = configured_base_url()
    if not base:
        return LocalProbe(
            REASON_UNREACHABLE, model, local_base_url_for_status(), detail="unconfigured"
        )
    loop_reason = loopback_refusal_reason(base)
    if loop_reason is not None:
        return LocalProbe(loop_reason, model, base, detail="refused without request")

    url = f"{base.rstrip('/')}/models"
    owns = client is None
    http = client or httpx.Client(timeout=timeout_s)
    started = time.perf_counter()
    try:
        resp = http.get(url, headers={"Authorization": f"Bearer {LOCAL_PLACEHOLDER_SECRET}"})
        latency = (time.perf_counter() - started) * 1000.0
        if resp.status_code >= 500:
            return LocalProbe(REASON_UNREACHABLE, model, base, latency, f"HTTP {resp.status_code}")
        try:
            payload: Any = resp.json()
        except ValueError:
            payload = None
        listed = models_listed(payload)
        if resp.status_code == 200 and listed and model not in listed:
            return LocalProbe(REASON_MODEL_NOT_LOADED, model, base, latency, "model not in /models")
        if resp.status_code == 200:
            return LocalProbe(None, model, base, latency)
        if resp.status_code == 404:
            return LocalProbe(REASON_UNREACHABLE, model, base, latency, "HTTP 404")
        return LocalProbe(REASON_UNREACHABLE, model, base, latency, f"HTTP {resp.status_code}")
    except httpx.TimeoutException:
        return LocalProbe(REASON_UNREACHABLE, model, base, detail="timeout")
    except (httpx.HTTPError, OSError) as exc:
        return LocalProbe(REASON_UNREACHABLE, model, base, detail=str(exc))
    finally:
        if owns:
            http.close()


def local_status_hop(*, probe: LocalProbe | None = None) -> dict[str, Any]:
    """Synthetic hop for ``GET /api/freeroute/status``. Not a vault key."""
    result = probe if probe is not None else probe_local()
    spec = get_provider(LOCAL_QWEN_ID)
    label = spec.name if spec is not None else "Local Qwen (loopback)"
    precheck = "ok" if result.ok else "error"
    if result.reason == REASON_UNREACHABLE and "timeout" in (result.detail or ""):
        precheck = "timeout"
    return {
        "key_id": LOCAL_HOP_KEY_ID,
        "label": label,
        "provider": LOCAL_QWEN_ID,
        "role": "free",
        "priority": 0,
        "precheck_status": precheck,
        "circuit": "closed",
        "failures": 0,
        "last_error": result.reason,
        "last_latency_ms": result.latency_ms,
        "park_until": None,
        "park_reason": None,
        "served_local": True,
        "local_reason": result.reason or "",
        "base_url": result.base_url,
        "model": result.model,
    }


def walkable_local_base() -> tuple[str, str] | tuple[None, str]:
    """Return ``(base_url, model)`` when the hop may be contacted, else ``(None, reason)``."""
    model = configured_model()
    base = configured_base_url()
    if not base:
        return None, REASON_UNREACHABLE
    loop_reason = loopback_refusal_reason(base)
    if loop_reason is not None:
        return None, loop_reason
    return base.rstrip("/"), model


__all__ = [
    "LOCAL_ONLY_ERROR_TYPE",
    "LOCAL_ONLY_MESSAGE",
    "LOCAL_PLACEHOLDER_SECRET",
    "REASON_MODEL_NOT_LOADED",
    "REASON_NOT_LOOPBACK",
    "REASON_UNREACHABLE",
    "SERVED_LOCAL_HEADER",
    "SERVED_MODEL_HEADER",
    "SERVED_PROVIDER_HEADER",
    "LocalProbe",
    "attach_served_fields",
    "chat_error_is_model_missing",
    "configured_base_url",
    "configured_model",
    "inject_served_into_sse_chunk",
    "is_loopback_base_url",
    "local_only_refusal",
    "local_status_hop",
    "loopback_refusal_reason",
    "models_listed",
    "pop_local_only",
    "probe_local",
    "served_response_headers",
    "walkable_local_base",
]

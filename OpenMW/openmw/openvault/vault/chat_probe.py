"""Per-key chat probe, on its own schedule from the 60s models list probe.

One POST ``/chat/completions`` per enabled OpenAI-compatible key. The body is
the fixed prompt "Reply with OK" and is not stored. ``max_tokens`` is 16, or
512 when that key's first catalog chat model is a reasoning model.

The probe writes no ``usage_events`` rows. A 402, or a plan-level 429, marks
the key unusable for ``usable_provider_count``. A transient 429 is a park.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

from openmw.openvault.route.attempt import DEFAULT_RATE_LIMIT_PARK_MS
from openmw.openvault.route.fallback_signals import (
    ACCOUNT_DEACTIVATED_SIGNALS,
    CREDITS_EXHAUSTED_SIGNALS,
    is_account_deactivated,
    is_credits_exhausted,
    parse_upstream_retry_hint_ms,
)
from openmw.openvault.vault.crypto import VaultCryptoError, VaultSealedError
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.parks import clip_error_text, provider_error_text
from openmw.openvault.vault.precheck import _default_base_url, classify_http_error
from openmw.openvault.vault.providers import (
    MIN_REASONING_BUDGET,
    get_provider,
    is_reasoning_model,
)
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()

DEFAULT_CHAT_PROBE_INTERVAL_S = 300.0
DEFAULT_CHAT_PROBE_TIMEOUT_S = 20.0
CHAT_PROBE_PROMPT = "Reply with OK"
CHAT_PROBE_MAX_TOKENS = 16

_CODE = re.compile(r"[a-z0-9_]{1,64}\Z")


def _plan_codes() -> frozenset[str]:
    found: set[str] = set()
    for sig in (*CREDITS_EXHAUSTED_SIGNALS, *ACCOUNT_DEACTIVATED_SIGNALS):
        token = sig.strip().lower()
        if _CODE.fullmatch(token):
            found.add(token)
    found.add("payment_required")
    return frozenset(found)


_PLAN_CODES = _plan_codes()

_DDL = """
CREATE TABLE IF NOT EXISTS chat_probe (
  key_id TEXT PRIMARY KEY,
  unusable INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT '',
  error_text TEXT NOT NULL DEFAULT '',
  checked_at REAL NOT NULL
)
"""


@dataclass(frozen=True)
class ChatProbeTarget:
    """Catalog model and completion budget for one key's probe."""

    model: str
    max_tokens: int


@dataclass(frozen=True)
class ChatProbeResult:
    """Probe outcome. ``error`` is scrubbed provider text, never a body."""

    key_id: str
    status: str
    http_status: int | None
    unusable: bool
    parked: bool
    model: str
    error: str


def chat_probe_target(provider: str) -> ChatProbeTarget | None:
    """First catalog chat model for this provider, or None when chat cannot be probed.

    OpenAI-compatible catalog rows only. Local loopback hops and providers
    without a chat id are skipped. A reasoning model uses the 512-token floor.
    """
    spec = get_provider(provider)
    if spec is None or spec.local_hop or not spec.openai_compatible:
        return None
    if not spec.chat_models:
        return None
    model = spec.chat_models[0]
    if is_reasoning_model(provider, model):
        return ChatProbeTarget(model, MIN_REASONING_BUDGET)
    return ChatProbeTarget(model, CHAT_PROBE_MAX_TOKENS)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)


def chat_unusable_ids(db_path: Path) -> set[str]:
    """Key ids whose last chat probe marked them unusable. Read only."""
    if not db_path.is_file():
        return set()
    with contextlib.closing(_connect(db_path)) as conn, conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_probe'"
        ).fetchone()
        if found is None:
            return set()
        rows = conn.execute("SELECT key_id FROM chat_probe WHERE unusable=1").fetchall()
    return {str(row["key_id"]) for row in rows}


def _save_status(
    db_path: Path,
    *,
    key_id: str,
    status: str,
    error_text: str,
    unusable: bool | None,
) -> bool:
    """Write one row. ``unusable is None`` keeps the flag already stored."""
    text = clip_error_text(error_text)
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT unusable FROM chat_probe WHERE key_id=?",
            (key_id,),
        ).fetchone()
        if unusable is None:
            flag = bool(int(row["unusable"])) if row is not None else False
        else:
            flag = bool(unusable)
        conn.execute(
            """
            INSERT INTO chat_probe (key_id, unusable, status, error_text, checked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key_id) DO UPDATE SET
              unusable=excluded.unusable,
              status=excluded.status,
              error_text=excluded.error_text,
              checked_at=excluded.checked_at
            """,
            (key_id, int(flag), status, text, time.time()),
        )
    return flag


def _request_limit_is_zero(headers: Mapping[str, str]) -> bool:
    """True when a request-limit header is 0. Remaining-0 is not a plan limit."""
    for name, value in headers.items():
        lowered = name.lower()
        if "remaining" in lowered or "reset" in lowered:
            continue
        if "ratelimit" not in lowered and "rate-limit" not in lowered:
            continue
        if "limit" not in lowered:
            continue
        if "req" not in lowered and "request" not in lowered:
            continue
        try:
            if float(str(value).strip()) == 0.0:
                return True
        except ValueError:
            continue
    return False


def _dig_code(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    sources: list[object] = []
    err = payload.get("error")
    if isinstance(err, dict):
        sources.extend((err.get("code"), err.get("type")))
    sources.extend((payload.get("code"), payload.get("type")))
    for item in sources:
        if not isinstance(item, str):
            continue
        token = item.strip().lower()
        if _CODE.fullmatch(token) and token in _PLAN_CODES:
            return token
    return ""


def _plan_code(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("{") and not text.startswith("["):
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return _dig_code(payload)


def _plan_level_429(headers: Mapping[str, str], raw: str) -> bool:
    """Request limit 0, or an account/plan code, or a known billing message."""
    if _request_limit_is_zero(headers):
        return True
    if _plan_code(raw):
        return True
    message = provider_error_text(raw)
    return is_credits_exhausted(message) or is_account_deactivated(message)


def _park_ms(headers: Mapping[str, str]) -> int:
    hint = parse_upstream_retry_hint_ms(headers, "")
    if hint is not None and hint > 0:
        return hint
    return DEFAULT_RATE_LIMIT_PARK_MS


def _header_map(headers: object) -> dict[str, str]:
    items = getattr(headers, "items", None)
    if not callable(items):
        return {}
    found: dict[str, str] = {}
    for key, value in items():
        found[str(key)] = str(value)
    return found


def _apply_http(
    vault: KeyVault,
    fallback: FallbackManager,
    *,
    key_id: str,
    code: int,
    raw: str,
    headers: Mapping[str, str],
) -> ChatProbeResult:
    classified = classify_http_error(code)
    stored = ""
    if code >= 400:
        stored = clip_error_text(provider_error_text(raw, status=code))
    unusable: bool | None
    park = False
    if 200 <= code < 300:
        unusable = False
    elif code == 402 or (code == 429 and _plan_level_429(headers, raw)):
        unusable = True
    elif code == 429:
        unusable = None
        park = True
    else:
        unusable = None
    flag = _save_status(
        vault.db_path,
        key_id=key_id,
        status=classified,
        error_text=stored,
        unusable=unusable,
    )
    if park:
        fallback.record_park(
            key_id,
            _park_ms(headers),
            "rate_limited",
            error_text=stored,
        )
    return ChatProbeResult(
        key_id=key_id,
        status=classified,
        http_status=code,
        unusable=flag,
        parked=park and fallback.key_is_parked(key_id),
        model="",
        error=stored,
    )


async def probe_key_chat(
    vault: KeyVault,
    fallback: FallbackManager,
    key_id: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = DEFAULT_CHAT_PROBE_TIMEOUT_S,
) -> ChatProbeResult:
    """Chat-probe one key. Does not record usage. Does not log the body or the key."""
    record = vault.get(key_id)
    if record is None or not record.enabled:
        return ChatProbeResult(key_id, "skipped", None, False, False, "", "")
    target = chat_probe_target(record.provider)
    if target is None:
        return ChatProbeResult(key_id, "skipped", None, False, False, "", "")
    base = _default_base_url(record.provider, record.base_url)
    if not base:
        return ChatProbeResult(
            key_id, "error", None, False, False, target.model, "missing base_url"
        )
    try:
        secret = vault.get_secret(key_id)
    except (VaultSealedError, VaultCryptoError):
        log.warning("openvault_chat_probe_secret", key_ref=key_id[:8], error="secret unavailable")
        return ChatProbeResult(
            key_id, "error", None, False, False, target.model, "secret unavailable"
        )
    if not secret:
        return ChatProbeResult(key_id, "error", None, False, False, target.model, "secret missing")

    owns = client is None
    http = client or httpx.AsyncClient(
        timeout=timeout_s,
        trust_env=False,
        follow_redirects=False,
    )
    url = f"{base}/chat/completions"
    payload = {
        "model": target.model,
        "messages": [{"role": "user", "content": CHAT_PROBE_PROMPT}],
        "max_tokens": target.max_tokens,
    }
    headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    try:
        try:
            resp = await http.post(url, headers=headers, json=payload)
        except httpx.TimeoutException:
            flag = _save_status(
                vault.db_path,
                key_id=key_id,
                status="timeout",
                error_text=clip_error_text("timeout"),
                unusable=None,
            )
            return ChatProbeResult(key_id, "timeout", None, flag, False, target.model, "timeout")
        except httpx.HTTPError:
            flag = _save_status(
                vault.db_path,
                key_id=key_id,
                status="error",
                error_text=clip_error_text("unreachable"),
                unusable=None,
            )
            return ChatProbeResult(key_id, "error", None, flag, False, target.model, "unreachable")
        code = int(resp.status_code)
        raw = resp.text if code >= 400 else ""
        header_map = _header_map(resp.headers)
        result = _apply_http(
            vault,
            fallback,
            key_id=key_id,
            code=code,
            raw=raw,
            headers=header_map,
        )
        log.info(
            "openvault_chat_probe",
            key_ref=key_id[:8],
            provider=record.provider,
            model=target.model,
            http_status=code,
            status=result.status,
            unusable=result.unusable,
            parked=result.parked,
        )
        return ChatProbeResult(
            key_id=result.key_id,
            status=result.status,
            http_status=result.http_status,
            unusable=result.unusable,
            parked=result.parked,
            model=target.model,
            error=result.error,
        )
    finally:
        if owns:
            await http.aclose()


async def probe_enabled_chats(
    vault: KeyVault,
    fallback: FallbackManager,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_s: float = DEFAULT_CHAT_PROBE_TIMEOUT_S,
) -> list[ChatProbeResult]:
    """Probe every enabled key that has a catalog chat model. No usage rows."""
    owns = client is None
    http = client or httpx.AsyncClient(
        timeout=timeout_s,
        trust_env=False,
        follow_redirects=False,
    )
    results: list[ChatProbeResult] = []
    try:
        for record in vault.list_keys():
            if not record.enabled:
                continue
            if chat_probe_target(record.provider) is None:
                continue
            try:
                results.append(
                    await probe_key_chat(
                        vault,
                        fallback,
                        record.id,
                        client=http,
                        timeout_s=timeout_s,
                    )
                )
            except Exception as exc:
                log.warning(
                    "openvault_chat_probe_key_error",
                    key_ref=record.id[:8],
                    error=clip_error_text(type(exc).__name__),
                )
        return results
    finally:
        if owns:
            await http.aclose()


class ChatProbeLoop:
    """Background chat probe. Not the models-list loop, and not its interval."""

    def __init__(
        self,
        vault: KeyVault,
        fallback: FallbackManager,
        *,
        interval_s: float = DEFAULT_CHAT_PROBE_INTERVAL_S,
    ) -> None:
        self._vault = vault
        self._fallback = fallback
        self._interval_s = interval_s
        self._stop = False

    @property
    def interval_s(self) -> float:
        return self._interval_s

    def stop(self) -> None:
        self._stop = True

    async def run_forever(self) -> None:
        import asyncio

        while not self._stop:
            try:
                await probe_enabled_chats(self._vault, self._fallback)
            except Exception as exc:
                log.warning(
                    "openvault_chat_probe_loop_error",
                    error=clip_error_text(type(exc).__name__),
                )
            await asyncio.sleep(self._interval_s)

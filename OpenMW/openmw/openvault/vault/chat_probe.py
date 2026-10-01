"""Per-key chat probe, on its own schedule from the 60s models list probe.

One POST ``/chat/completions`` per enabled OpenAI-compatible key that is due.
The body is the fixed prompt "Reply with OK" and is not stored. ``max_tokens``
is 16 on a non-reasoning catalog chat model. A provider whose chat models are
all reasoning models uses the 512-token floor on its first chat model.

Default period is 86400s (``OPENVAULT_CHAT_PROBE_INTERVAL_S``, floored at
3600s). Startup runs one pass after a random 30-120s jitter, and skips a key
whose last chat probe is younger than that floor. SambaNova and SEA-LION are
probed at most once every 6h. A key with a 2xx ``hop_attempts`` row inside
the current gap is skipped. The probe writes no ``usage_events`` rows.

The client timeout defaults to 120s (``OPENVAULT_CHAT_PROBE_TIMEOUT_S``,
floored at 30s). A timeout or connect error records status only. A 402, a
plan-level 429, or 401/403 marks the key unusable for
``usable_provider_count``. A transient 429 is a park. Only a 2xx clears
unusable.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import random
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

DEFAULT_CHAT_PROBE_INTERVAL_S = 86400.0
CHAT_PROBE_INTERVAL_MIN_S = 3600.0
CHAT_PROBE_INTERVAL_ENV = "OPENVAULT_CHAT_PROBE_INTERVAL_S"
# Free-tier request caps (SambaNova 20 RPD). Once per 6h is 4 probes/day.
CHAT_PROBE_LOW_CAP_INTERVAL_S = 6.0 * 60.0 * 60.0
CHAT_PROBE_LOW_CAP_PROVIDERS: frozenset[str] = frozenset({"sambanova", "sea_lion"})
DEFAULT_CHAT_PROBE_TIMEOUT_S = 120.0
CHAT_PROBE_TIMEOUT_MIN_S = 30.0
CHAT_PROBE_TIMEOUT_ENV = "OPENVAULT_CHAT_PROBE_TIMEOUT_S"
CHAT_PROBE_BOOT_JITTER_MIN_S = 30.0
CHAT_PROBE_BOOT_JITTER_MAX_S = 120.0
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


def _env_float(
    environ: Mapping[str, str] | None,
    name: str,
    *,
    default: float,
    floor: float,
) -> float:
    """Env seconds. Blank, junk, or non-finite values keep ``default``; else the floor."""
    source: Mapping[str, str] = os.environ if environ is None else environ
    raw = str(source.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    if value < floor:
        return floor
    return value


def chat_probe_interval_s(environ: Mapping[str, str] | None = None) -> float:
    """Probe period in seconds. Blank, junk, or non-finite env keeps 86400s.

    Numeric values below ``CHAT_PROBE_INTERVAL_MIN_S`` clamp up to that floor.
    """
    return _env_float(
        environ,
        CHAT_PROBE_INTERVAL_ENV,
        default=DEFAULT_CHAT_PROBE_INTERVAL_S,
        floor=CHAT_PROBE_INTERVAL_MIN_S,
    )


def chat_probe_timeout_s(environ: Mapping[str, str] | None = None) -> float:
    """Client timeout in seconds. Blank, junk, or non-finite env keeps 120s.

    Numeric values below ``CHAT_PROBE_TIMEOUT_MIN_S`` clamp up to that floor.
    """
    return _env_float(
        environ,
        CHAT_PROBE_TIMEOUT_ENV,
        default=DEFAULT_CHAT_PROBE_TIMEOUT_S,
        floor=CHAT_PROBE_TIMEOUT_MIN_S,
    )


def boot_jitter_s(rng: random.Random | None = None) -> float:
    """Startup delay in seconds, uniformly chosen from 30 to 120 inclusive."""
    if rng is None:
        return random.uniform(CHAT_PROBE_BOOT_JITTER_MIN_S, CHAT_PROBE_BOOT_JITTER_MAX_S)
    return rng.uniform(CHAT_PROBE_BOOT_JITTER_MIN_S, CHAT_PROBE_BOOT_JITTER_MAX_S)


def probe_gap_s(provider: str, base_interval: float) -> float:
    """Seconds this provider must wait between probes. Low-cap rows wait 6h."""
    if provider in CHAT_PROBE_LOW_CAP_PROVIDERS:
        return max(base_interval, CHAT_PROBE_LOW_CAP_INTERVAL_S)
    return base_interval


def chat_probe_target(provider: str) -> ChatProbeTarget | None:
    """Catalog chat model for this provider, or None when chat cannot be probed.

    OpenAI-compatible catalog rows only. Local loopback hops and providers
    without a chat id are skipped. Prefer the first non-reasoning chat model
    at 16 tokens. When every chat model is reasoning, use the first one at the
    512-token floor, the smallest budget those models accept.
    """
    spec = get_provider(provider)
    if spec is None or spec.local_hop or not spec.openai_compatible:
        return None
    if not spec.chat_models:
        return None
    for model in spec.chat_models:
        if not is_reasoning_model(provider, model):
            return ChatProbeTarget(model, CHAT_PROBE_MAX_TOKENS)
    return ChatProbeTarget(spec.chat_models[0], MIN_REASONING_BUDGET)


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
    checked_at: float | None = None,
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
            (key_id, int(flag), status, text, time.time() if checked_at is None else checked_at),
        )
    return flag


def _last_checked_at(db_path: Path, key_id: str) -> float | None:
    """Last probe stamp, or None. Does not create ``chat_probe``."""
    if not db_path.is_file():
        return None
    with contextlib.closing(_connect(db_path)) as conn, conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_probe'"
        ).fetchone()
        if found is None:
            return None
        row = conn.execute(
            "SELECT checked_at FROM chat_probe WHERE key_id=?",
            (key_id,),
        ).fetchone()
    if row is None:
        return None
    return float(row["checked_at"])


def _hop_success_within(db_path: Path, key_id: str, now: float, gap: float) -> bool:
    """True when ``hop_attempts`` has a 2xx for this key younger than ``gap``.

    Reads the #97 ledger only. A missing table is not created.
    """
    if not db_path.is_file():
        return False
    with contextlib.closing(_connect(db_path)) as conn, conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hop_attempts'"
        ).fetchone()
        if found is None:
            return False
        rows = conn.execute(
            "SELECT status, ts FROM hop_attempts WHERE key_id=?",
            (key_id,),
        ).fetchall()
    for row in rows:
        status = str(row["status"] or "")
        if not status.isdigit():
            continue
        code = int(status)
        if 200 <= code < 300 and (now - float(row["ts"])) < gap:
            return True
    return False


def _probe_suppressed(
    db_path: Path,
    key_id: str,
    provider: str,
    now: float,
    interval_s: float | None,
    *,
    boot: bool = False,
) -> bool:
    """Skip a key that already succeeded, or that was probed inside its gap.

    The cadence check runs only when the caller passes ``interval_s`` (the
    scheduled loop). A direct probe still skips a recent hop 2xx. A boot pass
    uses the 3600s floor (low-cap providers still take ``max`` with 6h) so a
    crash-loop restart inside that window does not probe again.
    """
    base = chat_probe_interval_s() if interval_s is None else float(interval_s)
    gap = probe_gap_s(provider, base)
    if _hop_success_within(db_path, key_id, now, gap):
        return True
    if boot:
        last = _last_checked_at(db_path, key_id)
        floor_gap = probe_gap_s(provider, CHAT_PROBE_INTERVAL_MIN_S)
        return last is not None and (now - last) < floor_gap
    if interval_s is None:
        return False
    last = _last_checked_at(db_path, key_id)
    return last is not None and (now - last) < gap


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
    checked_at: float | None = None,
) -> ChatProbeResult:
    classified = classify_http_error(code)
    stored = ""
    if code >= 400:
        stored = clip_error_text(provider_error_text(raw, status=code))
    unusable: bool | None
    park = False
    plan_429 = code == 429 and _plan_level_429(headers, raw)
    if 200 <= code < 300:
        unusable = False
    elif code in (401, 402, 403) or plan_429:
        unusable = True
    elif code == 429:
        unusable = None
        park = True
    else:
        # 404 and 5xx record status only. They do not park or change unusable.
        unusable = None
    flag = _save_status(
        vault.db_path,
        key_id=key_id,
        status=classified,
        error_text=stored,
        unusable=unusable,
        checked_at=checked_at,
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
    timeout_s: float | None = None,
    now: float | None = None,
    interval_s: float | None = None,
    boot: bool = False,
) -> ChatProbeResult:
    """Chat-probe one key. Does not record usage. Does not log the body or the key."""
    record = vault.get(key_id)
    if record is None or not record.enabled:
        return ChatProbeResult(key_id, "skipped", None, False, False, "", "")
    target = chat_probe_target(record.provider)
    if target is None:
        return ChatProbeResult(key_id, "skipped", None, False, False, "", "")
    stamp = time.time() if now is None else float(now)
    if _probe_suppressed(
        vault.db_path,
        key_id,
        record.provider,
        stamp,
        interval_s,
        boot=boot,
    ):
        return ChatProbeResult(key_id, "skipped", None, False, False, target.model, "")
    limit_s = chat_probe_timeout_s() if timeout_s is None else float(timeout_s)
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
        timeout=limit_s,
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
                checked_at=stamp,
            )
            return ChatProbeResult(key_id, "timeout", None, flag, False, target.model, "timeout")
        except httpx.HTTPError:
            flag = _save_status(
                vault.db_path,
                key_id=key_id,
                status="error",
                error_text=clip_error_text("unreachable"),
                unusable=None,
                checked_at=stamp,
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
            checked_at=stamp,
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
    timeout_s: float | None = None,
    now: float | None = None,
    interval_s: float | None = None,
    boot: bool = False,
) -> list[ChatProbeResult]:
    """Probe every enabled key that is due and has a catalog chat model. No usage rows."""
    limit_s = chat_probe_timeout_s() if timeout_s is None else float(timeout_s)
    owns = client is None
    http = client or httpx.AsyncClient(
        timeout=limit_s,
        trust_env=False,
        follow_redirects=False,
    )
    results: list[ChatProbeResult] = []
    period = chat_probe_interval_s() if interval_s is None else float(interval_s)
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
                        timeout_s=limit_s,
                        now=now,
                        interval_s=period,
                        boot=boot,
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
        timeout_s: float | None = None,
        jitter_s: float | None = None,
    ) -> None:
        self._vault = vault
        self._fallback = fallback
        self._interval_s = interval_s
        self._timeout_s = chat_probe_timeout_s() if timeout_s is None else float(timeout_s)
        self._jitter_s = jitter_s
        self._stop = False

    @property
    def interval_s(self) -> float:
        return self._interval_s

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    def stop(self) -> None:
        self._stop = True

    async def _pass(self, *, boot: bool) -> None:
        await probe_enabled_chats(
            self._vault,
            self._fallback,
            timeout_s=self._timeout_s,
            interval_s=self._interval_s,
            boot=boot,
        )

    async def run_forever(self) -> None:
        import asyncio

        delay = boot_jitter_s() if self._jitter_s is None else self._jitter_s
        await asyncio.sleep(delay)
        if self._stop:
            return
        try:
            await self._pass(boot=True)
        except Exception as exc:
            log.warning(
                "openvault_chat_probe_loop_error",
                error=clip_error_text(type(exc).__name__),
            )
        while not self._stop:
            try:
                await self._pass(boot=False)
            except Exception as exc:
                log.warning(
                    "openvault_chat_probe_loop_error",
                    error=clip_error_text(type(exc).__name__),
                )
            await asyncio.sleep(self._interval_s)

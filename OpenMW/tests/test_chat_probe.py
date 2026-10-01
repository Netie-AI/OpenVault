"""Chat-level health probe (#92). Mocked upstream. No network. No usage rows."""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from openmw.openvault.app import create_app
from openmw.openvault.vault.chat_probe import (
    CHAT_PROBE_BOOT_JITTER_MAX_S,
    CHAT_PROBE_BOOT_JITTER_MIN_S,
    CHAT_PROBE_INTERVAL_MIN_S,
    CHAT_PROBE_LOW_CAP_INTERVAL_S,
    CHAT_PROBE_LOW_CAP_PROVIDERS,
    CHAT_PROBE_MAX_TOKENS,
    CHAT_PROBE_PROMPT,
    CHAT_PROBE_TIMEOUT_MIN_S,
    DEFAULT_CHAT_PROBE_INTERVAL_S,
    DEFAULT_CHAT_PROBE_TIMEOUT_S,
    ChatProbeLoop,
    boot_jitter_s,
    chat_probe_interval_s,
    chat_probe_target,
    chat_probe_timeout_s,
    chat_unusable_ids,
    probe_enabled_chats,
    probe_key_chat,
)
from openmw.openvault.vault.crypto import Seal, VaultSealedError
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.hop_attempts import record_hop_attempt
from openmw.openvault.vault.precheck import (
    DEFAULT_PRECHECK_INTERVAL_S,
    classify_http_error,
    precheck_one,
)
from openmw.openvault.vault.providers import (
    MIN_REASONING_BUDGET,
    get_provider,
    is_reasoning_model,
    spendable_for_freeroute,
)
from openmw.openvault.vault.store import KeyVault

_SECRET = "sk-test-ov92-SECRET-marker"
_BODY = "RESPBODY-ov92-not-stored"
_MISTRAL = "https://api.mistral.ai/v1"
_GROQ = "https://api.groq.com/openai/v1"


class _Resp:
    def __init__(
        self,
        code: int,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = code
        self._body = body
        self.headers = headers or {}
        self.text_reads = 0

    @property
    def text(self) -> str:
        self.text_reads += 1
        return self._body


class _Client:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp
        self.posts: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _Resp:
        self.posts.append({"url": url, "headers": dict(headers or {}), "json": json or {}})
        return self.resp

    async def aclose(self) -> None:
        return None


class _RaiseClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.posts = 0

    async def post(self, *_args: object, **_kwargs: object) -> _Resp:
        self.posts += 1
        raise self.exc

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))


def _key(vault: KeyVault, provider: str, base_url: str, secret: str = _SECRET) -> str:
    record = vault.create(
        label=provider,
        provider=provider,  # type: ignore[arg-type]
        secret=secret,
        role="free",
        priority=1,
        base_url=base_url,
    )
    return record.id


def _usage_count(db: Path) -> int:
    with sqlite3.connect(str(db)) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_events'"
        ).fetchone()
        if found is None:
            return 0
        row = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()
    return int(row[0] if row else 0)


def _dump(db: Path) -> str:
    with sqlite3.connect(str(db)) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        parts: list[str] = []
        for (name,) in tables:
            rows = conn.execute(f"SELECT * FROM {name}").fetchall()
            parts.append(f"{name}={rows!r}")
    return "\n".join(parts)


def _assert_clean(db: Path, logs: list[object]) -> None:
    blob = _dump(db) + "\n" + json.dumps(logs, default=str)
    assert _SECRET not in blob
    assert _BODY not in blob
    assert CHAT_PROBE_PROMPT not in blob
    assert _usage_count(db) == 0


def _run(vault: KeyVault, key_id: str, client: _Client | _RaiseClient) -> Any:
    mgr = FallbackManager(vault)
    with capture_logs() as logs:
        result = asyncio.run(
            probe_key_chat(vault, mgr, key_id, client=client)  # type: ignore[arg-type]
        )
    return mgr, result, logs


def test_probe_target_prefers_a_non_reasoning_chat_model() -> None:
    mistral = get_provider("mistral")
    cerebras = get_provider("cerebras")
    groq = get_provider("groq")
    samba = get_provider("sambanova")
    assert mistral is not None and cerebras is not None
    assert groq is not None and samba is not None
    cheap = chat_probe_target("mistral")
    assert cheap is not None
    assert cheap.model == mistral.chat_models[0]
    assert not is_reasoning_model("mistral", cheap.model)
    assert cheap.max_tokens == CHAT_PROBE_MAX_TOKENS == 16
    # Cerebras lists a reasoning model first. The probe takes the chat model
    # that does not force the 512-token floor.
    cerebras_chat = chat_probe_target("cerebras")
    assert cerebras_chat is not None
    assert cerebras_chat.model == "qwen-3.8-27b"
    assert not is_reasoning_model("cerebras", cerebras_chat.model)
    assert cerebras_chat.max_tokens == CHAT_PROBE_MAX_TOKENS == 16
    # Groq's catalog is all reasoning, so the smallest accepted budget is 512.
    groq_chat = chat_probe_target("groq")
    assert groq_chat is not None
    assert groq_chat.model == groq.chat_models[0]
    assert is_reasoning_model("groq", groq_chat.model)
    assert groq_chat.max_tokens == MIN_REASONING_BUDGET == 512
    samba_chat = chat_probe_target("sambanova")
    assert samba_chat is not None
    assert samba_chat.model == "MiniMax-M2.7"
    assert samba_chat.max_tokens == 16
    assert chat_probe_target("anthropic") is None
    assert chat_probe_target("huggingface") is None
    assert chat_probe_target("local_qwen") is None
    assert DEFAULT_CHAT_PROBE_INTERVAL_S == 86400.0
    assert DEFAULT_CHAT_PROBE_TIMEOUT_S == 120.0
    assert DEFAULT_CHAT_PROBE_INTERVAL_S != DEFAULT_PRECHECK_INTERVAL_S
    assert frozenset({"sambanova", "sea_lion"}) == CHAT_PROBE_LOW_CAP_PROVIDERS
    assert CHAT_PROBE_LOW_CAP_INTERVAL_S == 6.0 * 60.0 * 60.0


def test_402_marks_unusable_without_parking_or_usage(vault: KeyVault) -> None:
    mistral_id = _key(vault, "mistral", _MISTRAL)
    groq_id = _key(vault, "groq", _GROQ, secret="gsk_test_ov92_other")
    body = json.dumps(
        {
            "error": {
                "message": f"{_SECRET} payment required " + ("x" * 400),
                "type": "payment_required",
                "dump": _BODY,
            },
            "request": CHAT_PROBE_PROMPT,
        }
    )
    client = _Client(_Resp(402, body))
    mgr = FallbackManager(vault)
    before = [row.id for row in mgr.ordered_candidates()]
    spendable = len(spendable_for_freeroute())
    assert mgr.usable_provider_count() == 2
    with capture_logs() as logs:
        result = asyncio.run(probe_key_chat(vault, mgr, mistral_id, client=client))
    assert result.status == "error"
    assert result.status == classify_http_error(402)
    assert result.unusable is True
    assert result.parked is False
    assert result.http_status == 402
    assert mgr.key_is_parked(mistral_id) is False
    assert mgr.usable_provider_count() == 1
    assert mistral_id in chat_unusable_ids(vault.db_path)
    assert groq_id not in chat_unusable_ids(vault.db_path)
    assert [row.id for row in mgr.ordered_candidates()] == before
    assert len(spendable_for_freeroute()) == spendable
    circ = mgr._circuit(mistral_id)
    assert circ.state == "closed"
    assert circ.failures == 0
    assert vault.get(mistral_id) is not None
    assert vault.get(mistral_id).precheck_status == "unknown"  # type: ignore[union-attr]
    posted = client.posts[0]
    assert str(posted["url"]).endswith("/chat/completions")
    assert posted["json"]["messages"][0]["content"] == CHAT_PROBE_PROMPT
    assert posted["json"]["max_tokens"] == 16
    assert posted["json"]["model"] == chat_probe_target("mistral").model  # type: ignore[union-attr]
    assert _SECRET not in posted["url"]
    stored = _chat_error(vault.db_path)
    assert stored is not None
    assert len(stored) <= 200
    assert _SECRET not in stored
    _assert_clean(vault.db_path, logs)


def test_429_limit_zero_marks_unusable(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    _key(vault, "groq", _GROQ, secret="gsk_test_ov92_limit")
    body = json.dumps(
        {
            "error": {
                "message": "slow down",
                "code": "rate_limit_exceeded",
                "dump": _BODY,
            }
        }
    )
    client = _Client(
        _Resp(
            429,
            body,
            {
                "X-RateLimit-Limit-Req-Minute": "0",
                "X-RateLimit-Remaining-Req-Minute": "0",
            },
        )
    )
    mgr, result, logs = _run(vault, key_id, client)
    assert result.status == "rate_limit"
    assert result.status == classify_http_error(429)
    assert result.unusable is True
    assert result.parked is False
    assert mgr.key_is_parked(key_id) is False
    assert mgr.usable_provider_count() == 1
    assert key_id in chat_unusable_ids(vault.db_path)
    _assert_clean(vault.db_path, logs)


def test_429_plan_code_marks_unusable(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    _key(vault, "groq", _GROQ, secret="gsk_test_ov92_plan")
    body = json.dumps(
        {
            "error": {
                "message": "plan blocked",
                "type": "insufficient_quota",
                "dump": _BODY,
            },
            "echo": _SECRET,
        }
    )
    client = _Client(_Resp(429, body, {"x-ratelimit-remaining-req-minute": "0"}))
    mgr, result, logs = _run(vault, key_id, client)
    assert result.status == "rate_limit"
    assert result.unusable is True
    assert result.parked is False
    assert mgr.key_is_parked(key_id) is False
    assert mgr._circuit(key_id).state == "closed"
    assert mgr.usable_provider_count() == 1
    _assert_clean(vault.db_path, logs)


def test_billing_message_429_is_unusable(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    body = json.dumps({"error": {"message": "account has been deactivated", "dump": _BODY}})
    client = _Client(_Resp(429, body))
    mgr, result, _logs = _run(vault, key_id, client)
    assert result.status == "rate_limit"
    assert result.unusable is True
    assert mgr.key_is_parked(key_id) is False


def test_transient_429_parks_and_is_not_unusable(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    _key(vault, "groq", _GROQ, secret="gsk_test_ov92_park")
    body = json.dumps(
        {
            "error": {
                "message": "too many requests",
                "code": "rate_limit_exceeded",
                "dump": _BODY,
            },
            "secret": _SECRET,
        }
    )
    headers = {
        "x-ratelimit-limit-req-minute": "20",
        "x-ratelimit-remaining-req-minute": "0",
    }
    client = _Client(_Resp(429, body, headers))
    mgr, result, logs = _run(vault, key_id, client)
    assert result.status == "rate_limit"
    assert result.status == classify_http_error(429)
    assert result.unusable is False
    assert result.parked is True
    assert mgr.key_is_parked(key_id) is True
    assert mgr.key_park_reason(key_id) == "rate_limited"
    assert key_id not in chat_unusable_ids(vault.db_path)
    assert mgr.usable_provider_count() == 1
    assert mgr._circuit(key_id).failures == 0
    assert mgr._circuit(key_id).state == "closed"
    _assert_clean(vault.db_path, logs)


def test_auth_fail_and_rate_limit_come_from_classify_http_error(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    seen: list[tuple[object, ...]] = []
    real = classify_http_error

    def spy(status_code: int, body: str = "") -> Any:
        seen.append((status_code, body))
        return real(status_code, body)

    auth_body = json.dumps({"error": {"message": "unauthorized", "dump": _BODY}, "key": _SECRET})
    limit_body = json.dumps({"error": {"message": "too many requests", "dump": _BODY}})
    auth_client = _Client(_Resp(401, auth_body))
    limit_client = _Client(_Resp(429, limit_body))
    mgr = FallbackManager(vault)
    with (
        patch("openmw.openvault.vault.chat_probe.classify_http_error", spy),
        capture_logs() as logs,
    ):
        auth = asyncio.run(probe_key_chat(vault, mgr, key_id, client=auth_client))
        limited = asyncio.run(probe_key_chat(vault, mgr, key_id, client=limit_client))
    assert seen == [(401, ""), (429, "")]
    assert auth.status == "auth_fail"
    assert auth.status == real(401)
    assert auth.unusable is True
    assert auth.parked is False
    assert limited.status == "rate_limit"
    assert limited.status == real(429)
    assert limited.parked is True
    # A transient 429 parks and does not clear the 401 unusable flag.
    assert limited.unusable is True
    assert mgr.key_is_parked(key_id) is True
    _assert_clean(vault.db_path, logs)


def test_success_clears_unusable_and_writes_no_usage(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    mgr = FallbackManager(vault)
    failed = _Client(
        _Resp(402, json.dumps({"error": {"message": "payment required", "dump": _BODY}}))
    )
    ok_body = json.dumps({"choices": [{"message": {"content": _BODY}}], "key": _SECRET})
    ok = _Client(_Resp(200, ok_body))
    with capture_logs() as logs:
        first = asyncio.run(probe_key_chat(vault, mgr, key_id, client=failed))
        assert first.unusable is True
        assert failed.resp.text_reads == 1
        second = asyncio.run(probe_key_chat(vault, mgr, key_id, client=ok))
    assert second.status == "ok"
    assert second.unusable is False
    assert second.parked is False
    assert ok.resp.text_reads == 0
    assert key_id not in chat_unusable_ids(vault.db_path)
    assert mgr.usable_provider_count() == 1
    assert _usage_count(vault.db_path) == 0
    _assert_clean(vault.db_path, logs)


def test_models_probe_does_not_post_chat(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)

    class _GetOnly:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.gets = 0
            self.posts = 0

        async def get(self, _url: str, headers: dict[str, str] | None = None) -> _Resp:
            self.gets += 1
            return _Resp(200, "")

        async def post(self, *_args: object, **_kwargs: object) -> _Resp:
            self.posts += 1
            raise AssertionError("models probe posted a chat")

        async def aclose(self) -> None:
            return None

    holder: list[_GetOnly] = []

    def _factory(*args: object, **kwargs: object) -> _GetOnly:
        client = _GetOnly(*args, **kwargs)
        holder.append(client)
        return client

    with patch("openmw.openvault.vault.precheck.httpx.AsyncClient", _factory):
        asyncio.run(precheck_one(vault, key_id))
    assert holder[0].gets == 1
    assert holder[0].posts == 0


def test_disabled_and_non_chat_keys_are_not_probed(vault: KeyVault) -> None:
    off = _key(vault, "mistral", _MISTRAL)
    vault.update(off, enabled=False)
    anthropic = _key(vault, "anthropic", "https://api.anthropic.com", secret="sk-ant-ov92-secret")
    live = _key(vault, "cerebras", "https://api.cerebras.ai/v1", secret="csk-test-ov92-live")
    client = _Client(_Resp(200, ""))
    mgr = FallbackManager(vault)
    results = asyncio.run(probe_enabled_chats(vault, mgr, client=client))  # type: ignore[arg-type]
    assert [item.key_id for item in results] == [live]
    assert len(client.posts) == 1
    assert client.posts[0]["json"]["max_tokens"] == 16
    assert client.posts[0]["json"]["model"] == chat_probe_target("cerebras").model  # type: ignore[union-attr]
    assert anthropic not in [item.key_id for item in results]
    assert off not in [item.key_id for item in results]


def test_timeout_and_transport_are_not_unusable(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    mgr = FallbackManager(vault)
    timed = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            key_id,
            client=_RaiseClient(httpx.TimeoutException("timed out")),  # type: ignore[arg-type]
        )
    )
    assert timed.status == "timeout"
    assert timed.unusable is False
    assert timed.parked is False
    assert mgr.key_is_parked(key_id) is False
    assert key_id not in chat_unusable_ids(vault.db_path)
    down = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            key_id,
            client=_RaiseClient(httpx.ConnectError("down")),  # type: ignore[arg-type]
        )
    )
    assert down.status == "error"
    assert down.unusable is False
    assert down.parked is False
    assert mgr.key_is_parked(key_id) is False
    assert key_id not in chat_unusable_ids(vault.db_path)
    assert _usage_count(vault.db_path) == 0
    flagged = _key(vault, "groq", _GROQ, secret="gsk_test_ov104_timeout")
    blocked = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            flagged,
            client=_Client(_Resp(402, json.dumps({"error": {"message": "payment required"}}))),  # type: ignore[arg-type]
        )
    )
    assert blocked.unusable is True
    held = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            flagged,
            client=_RaiseClient(httpx.TimeoutException("still slow")),  # type: ignore[arg-type]
        )
    )
    assert held.status == "timeout"
    assert held.unusable is True
    assert held.parked is False
    assert mgr.key_is_parked(flagged) is False
    assert flagged in chat_unusable_ids(vault.db_path)


def test_sealed_vault_does_not_probe(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    client = _Client(_Resp(200, _BODY))
    mgr = FallbackManager(vault)
    with (
        patch.object(vault, "get_secret", side_effect=VaultSealedError("sealed")),
        capture_logs() as logs,
    ):
        result = asyncio.run(probe_key_chat(vault, mgr, key_id, client=client))
    assert result.status == "error"
    assert result.error == "secret unavailable"
    assert client.posts == []
    assert _SECRET not in json.dumps(logs, default=str)
    assert _BODY not in json.dumps(logs, default=str)


def test_chat_loop_stops_after_one_pass(vault: KeyVault) -> None:
    mgr = FallbackManager(vault)
    loop = ChatProbeLoop(vault, mgr, interval_s=0.01, jitter_s=0.0)
    seen: list[str] = []

    async def _fake(*_args: object, **_kwargs: object) -> list[object]:
        seen.append("chat")
        loop.stop()
        return []

    with patch("openmw.openvault.vault.chat_probe.probe_enabled_chats", _fake):
        asyncio.run(loop.run_forever())
    assert seen == ["chat"]
    assert loop.interval_s == 0.01


def test_lifespan_starts_chat_probe_apart_from_models(
    vault: KeyVault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[str, float]] = []

    class _Models:
        def __init__(self, _vault: object, *, interval_s: float = 60.0) -> None:
            started.append(("models", interval_s))

        def stop(self) -> None:
            return None

        async def run_forever(self) -> None:
            await asyncio.Event().wait()

    class _Chat:
        def __init__(
            self,
            _vault: object,
            _fallback: object,
            *,
            interval_s: float = DEFAULT_CHAT_PROBE_INTERVAL_S,
            timeout_s: float = DEFAULT_CHAT_PROBE_TIMEOUT_S,
        ) -> None:
            self._interval_s = interval_s
            self._timeout_s = timeout_s
            started.append(("chat", interval_s, timeout_s))

        @property
        def interval_s(self) -> float:
            return self._interval_s

        @property
        def timeout_s(self) -> float:
            return self._timeout_s

        def stop(self) -> None:
            return None

        async def run_forever(self) -> None:
            await asyncio.Event().wait()

    monkeypatch.delenv("OPENVAULT_CHAT_PROBE_INTERVAL_S", raising=False)
    monkeypatch.delenv("OPENVAULT_CHAT_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.setattr("openmw.openvault.app.PrecheckLoop", _Models)
    monkeypatch.setattr("openmw.openvault.app.ChatProbeLoop", _Chat)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=True,
        cortex_url="http://127.0.0.1:9",
    )
    with TestClient(app, client=("127.0.0.1", 5555)):
        assert started == [
            ("models", 60.0),
            ("chat", DEFAULT_CHAT_PROBE_INTERVAL_S, DEFAULT_CHAT_PROBE_TIMEOUT_S),
        ]


def test_missing_db_has_no_unusable_ids(tmp_path: Path) -> None:
    assert chat_unusable_ids(tmp_path / "missing.db") == set()


def _posts_across_day(
    vault: KeyVault,
    *,
    now0: float,
    interval_s: float,
) -> int:
    """How many chat POSTs one enabled key emits across a simulated day."""
    client = _Client(_Resp(200, ""))
    mgr = FallbackManager(vault)
    sent = 0
    t = now0
    day_end = now0 + 86400.0
    while t < day_end:
        before = len(client.posts)
        asyncio.run(
            probe_enabled_chats(
                vault,
                mgr,
                client=client,  # type: ignore[arg-type]
                now=t,
                interval_s=interval_s,
            )
        )
        sent += len(client.posts) - before
        t += interval_s
    return sent


def test_default_cadence_is_one_probe_per_day_plus_boot(vault: KeyVault, tmp_path: Path) -> None:
    _key(vault, "mistral", _MISTRAL)
    now0 = 1_700_000_000.0
    scheduled = _posts_across_day(vault, now0=now0, interval_s=DEFAULT_CHAT_PROBE_INTERVAL_S)
    assert DEFAULT_CHAT_PROBE_INTERVAL_S == 86400.0
    assert scheduled == 1
    fresh = KeyVault(db_path=tmp_path / "boot.db", seal=Seal(Fernet.generate_key()))
    _key(fresh, "mistral", _MISTRAL)
    client = _Client(_Resp(200, ""))
    mgr = FallbackManager(fresh)
    asyncio.run(
        probe_enabled_chats(
            fresh,
            mgr,
            client=client,  # type: ignore[arg-type]
            now=now0,
            interval_s=DEFAULT_CHAT_PROBE_INTERVAL_S,
            boot=True,
        )
    )
    assert len(client.posts) == 1
    t = now0
    day_end = now0 + 86400.0
    while t < day_end:
        asyncio.run(
            probe_enabled_chats(
                fresh,
                mgr,
                client=client,  # type: ignore[arg-type]
                now=t,
                interval_s=DEFAULT_CHAT_PROBE_INTERVAL_S,
            )
        )
        t += DEFAULT_CHAT_PROBE_INTERVAL_S
    assert len(client.posts) == 1
    asyncio.run(
        probe_enabled_chats(
            fresh,
            mgr,
            client=client,  # type: ignore[arg-type]
            now=now0 + 86400.0,
            interval_s=DEFAULT_CHAT_PROBE_INTERVAL_S,
        )
    )
    assert len(client.posts) == 2


def test_sambanova_is_probed_at_most_four_times_a_day(vault: KeyVault) -> None:
    _key(vault, "sambanova", "https://api.sambanova.ai/v1", secret="sk-samba-ov92-day")
    sent = _posts_across_day(vault, now0=1_700_000_000.0, interval_s=3600.0)
    assert sent == 4
    assert sent <= 4


def test_sea_lion_uses_the_same_low_cap_gap(vault: KeyVault) -> None:
    _key(vault, "sea_lion", "https://api.sea-lion.ai/v1", secret="sk-sealion-ov92-day")
    sent = _posts_across_day(vault, now0=1_700_000_000.0, interval_s=3600.0)
    assert sent == 4
    assert sent <= 4


def test_recent_hop_2xx_is_not_probed(vault: KeyVault) -> None:
    recent = _key(vault, "mistral", _MISTRAL)
    other = _key(vault, "groq", _GROQ, secret="gsk_test_ov92_hop")
    stale = _key(vault, "cerebras", "https://api.cerebras.ai/v1", secret="csk-test-ov92-stale")
    now = 1_700_000_000.0
    record_hop_attempt(
        vault.db_path,
        request_id="req-ov92-ok",
        key_id=recent,
        model="mistral-small-latest",
        status="200",
        latency_ms=12,
        reason="",
        ts=now - 30.0,
        now=now,
    )
    record_hop_attempt(
        vault.db_path,
        request_id="req-ov92-err",
        key_id=other,
        model="openai/gpt-oss-120b",
        status="500",
        latency_ms=12,
        reason="",
        ts=now - 30.0,
        now=now,
    )
    record_hop_attempt(
        vault.db_path,
        request_id="req-ov92-old",
        key_id=stale,
        model="qwen-3.8-27b",
        status="200",
        latency_ms=12,
        reason="",
        ts=now - 3600.0,
        now=now,
    )
    client = _Client(_Resp(200, ""))
    mgr = FallbackManager(vault)
    skipped = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            recent,
            client=client,  # type: ignore[arg-type]
            now=now,
            interval_s=3600.0,
        )
    )
    probed = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            other,
            client=client,  # type: ignore[arg-type]
            now=now,
            interval_s=3600.0,
        )
    )
    due = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            stale,
            client=client,  # type: ignore[arg-type]
            now=now,
            interval_s=3600.0,
        )
    )
    assert skipped.status == "skipped"
    assert probed.status == "ok"
    assert due.status == "ok"
    assert len(client.posts) == 2
    posted_models = [item["json"]["model"] for item in client.posts]
    assert chat_probe_target("groq").model in posted_models  # type: ignore[union-attr]
    assert chat_probe_target("cerebras").model in posted_models  # type: ignore[union-attr]


def test_interval_env_is_honoured_and_floored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENVAULT_CHAT_PROBE_INTERVAL_S", raising=False)
    assert chat_probe_interval_s() == DEFAULT_CHAT_PROBE_INTERVAL_S == 86400.0
    assert chat_probe_interval_s({}) == 86400.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": ""}) == 86400.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": "nope"}) == 86400.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": "nan"}) == 86400.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": "inf"}) == 86400.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": "-inf"}) == 86400.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": "7200"}) == 7200.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": "86400"}) == 86400.0
    assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": "3600"}) == 3600.0
    for raw in ("1800", "600", "60", "0", "3599", "-5"):
        assert chat_probe_interval_s({"OPENVAULT_CHAT_PROBE_INTERVAL_S": raw}) == (
            CHAT_PROBE_INTERVAL_MIN_S
        )
    monkeypatch.setenv("OPENVAULT_CHAT_PROBE_INTERVAL_S", "90000")
    assert chat_probe_interval_s() == 90000.0
    monkeypatch.setenv("OPENVAULT_CHAT_PROBE_INTERVAL_S", "30")
    assert chat_probe_interval_s() == 3600.0


def test_timeout_env_is_honoured_and_floored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENVAULT_CHAT_PROBE_TIMEOUT_S", raising=False)
    assert chat_probe_timeout_s() == DEFAULT_CHAT_PROBE_TIMEOUT_S == 120.0
    assert chat_probe_timeout_s({}) == 120.0
    assert chat_probe_timeout_s({"OPENVAULT_CHAT_PROBE_TIMEOUT_S": ""}) == 120.0
    assert chat_probe_timeout_s({"OPENVAULT_CHAT_PROBE_TIMEOUT_S": "nope"}) == 120.0
    assert chat_probe_timeout_s({"OPENVAULT_CHAT_PROBE_TIMEOUT_S": "nan"}) == 120.0
    assert chat_probe_timeout_s({"OPENVAULT_CHAT_PROBE_TIMEOUT_S": "inf"}) == 120.0
    assert chat_probe_timeout_s({"OPENVAULT_CHAT_PROBE_TIMEOUT_S": "90"}) == 90.0
    assert chat_probe_timeout_s({"OPENVAULT_CHAT_PROBE_TIMEOUT_S": "30"}) == 30.0
    for raw in ("29", "10", "0", "-1"):
        assert chat_probe_timeout_s({"OPENVAULT_CHAT_PROBE_TIMEOUT_S": raw}) == (
            CHAT_PROBE_TIMEOUT_MIN_S
        )
    monkeypatch.setenv("OPENVAULT_CHAT_PROBE_TIMEOUT_S", "45")
    assert chat_probe_timeout_s() == 45.0
    monkeypatch.setenv("OPENVAULT_CHAT_PROBE_TIMEOUT_S", "1")
    assert chat_probe_timeout_s() == 30.0


def _chat_error(db: Path) -> str | None:
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute("SELECT error_text FROM chat_probe").fetchone()
    if row is None:
        return None
    return str(row[0])


def test_boot_skips_a_restart_within_one_hour(vault: KeyVault) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    now = 1_700_000_000.0
    client = _Client(_Resp(200, ""))
    mgr = FallbackManager(vault)
    first = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            key_id,
            client=client,  # type: ignore[arg-type]
            now=now,
        )
    )
    assert first.status == "ok"
    assert len(client.posts) == 1
    client.posts.clear()
    skipped = asyncio.run(
        probe_enabled_chats(
            vault,
            mgr,
            client=client,  # type: ignore[arg-type]
            now=now + 1800.0,
            interval_s=DEFAULT_CHAT_PROBE_INTERVAL_S,
            boot=True,
        )
    )
    assert [item.status for item in skipped] == ["skipped"]
    assert client.posts == []
    again = asyncio.run(
        probe_enabled_chats(
            vault,
            mgr,
            client=client,  # type: ignore[arg-type]
            now=now + 7200.0,
            interval_s=DEFAULT_CHAT_PROBE_INTERVAL_S,
            boot=True,
        )
    )
    assert [item.status for item in again] == ["ok"]
    assert len(client.posts) == 1


def test_boot_low_cap_keeps_the_six_hour_gap(vault: KeyVault) -> None:
    key_id = _key(vault, "sambanova", "https://api.sambanova.ai/v1", secret="sk-samba-ov104-boot")
    now = 1_700_000_000.0
    client = _Client(_Resp(200, ""))
    mgr = FallbackManager(vault)
    asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            key_id,
            client=client,  # type: ignore[arg-type]
            now=now,
        )
    )
    client.posts.clear()
    skipped = asyncio.run(
        probe_enabled_chats(
            vault,
            mgr,
            client=client,  # type: ignore[arg-type]
            now=now + 7200.0,
            interval_s=DEFAULT_CHAT_PROBE_INTERVAL_S,
            boot=True,
        )
    )
    assert [item.status for item in skipped] == ["skipped"]
    assert client.posts == []
    assert CHAT_PROBE_LOW_CAP_INTERVAL_S == 6.0 * 60.0 * 60.0


def test_boot_jitter_stays_inside_30_to_120() -> None:
    rng = random.Random(104)
    for _ in range(40):
        delay = boot_jitter_s(rng)
        assert CHAT_PROBE_BOOT_JITTER_MIN_S <= delay <= CHAT_PROBE_BOOT_JITTER_MAX_S


def test_loop_boots_after_jitter_before_the_daily_pass(vault: KeyVault) -> None:
    mgr = FallbackManager(vault)
    loop = ChatProbeLoop(vault, mgr, interval_s=86400.0, timeout_s=120.0, jitter_s=45.0)
    calls: list[dict[str, object]] = []

    async def _fake(*_args: object, **kwargs: object) -> list[object]:
        calls.append(dict(kwargs))
        if len(calls) == 2:
            loop.stop()
        return []

    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    with (
        patch("openmw.openvault.vault.chat_probe.probe_enabled_chats", _fake),
        patch("asyncio.sleep", _sleep),
    ):
        asyncio.run(loop.run_forever())
    assert [bool(item["boot"]) for item in calls] == [True, False]
    assert calls[0]["timeout_s"] == 120.0
    assert calls[0]["interval_s"] == 86400.0
    assert slept == [45.0, 86400.0]


def test_slow_2xx_inside_the_timeout_is_ok(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    monkeypatch.delenv("OPENVAULT_CHAT_PROBE_TIMEOUT_S", raising=False)
    seen: dict[str, float] = {}

    class _SlowClient:
        def __init__(self, *, timeout: float, trust_env: bool, follow_redirects: bool) -> None:
            seen["timeout"] = float(timeout)
            assert trust_env is False
            assert follow_redirects is False

        async def post(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            json: dict[str, Any] | None = None,
        ) -> _Resp:
            await asyncio.sleep(0.02)
            return _Resp(200, "")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("openmw.openvault.vault.chat_probe.httpx.AsyncClient", _SlowClient)
    mgr = FallbackManager(vault)
    result = asyncio.run(probe_key_chat(vault, mgr, key_id))
    assert seen["timeout"] == DEFAULT_CHAT_PROBE_TIMEOUT_S == 120.0
    assert result.status == "ok"
    assert result.http_status == 200
    assert result.unusable is False
    assert result.parked is False
    assert mgr.key_is_parked(key_id) is False


@pytest.mark.parametrize("code", [401, 403])
def test_401_and_403_mark_unusable(vault: KeyVault, code: int) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    _key(vault, "groq", _GROQ, secret="gsk_test_ov104_auth")
    body = json.dumps({"error": {"message": "denied", "dump": _BODY}, "key": _SECRET})
    client = _Client(_Resp(code, body))
    mgr, result, logs = _run(vault, key_id, client)
    assert result.status == "auth_fail"
    assert result.status == classify_http_error(code)
    assert result.http_status == code
    assert result.unusable is True
    assert result.parked is False
    assert mgr.key_is_parked(key_id) is False
    assert key_id in chat_unusable_ids(vault.db_path)
    assert mgr.usable_provider_count() == 1
    _assert_clean(vault.db_path, logs)


@pytest.mark.parametrize("code", [404, 500, 503])
def test_404_and_5xx_record_status_only(vault: KeyVault, code: int) -> None:
    key_id = _key(vault, "mistral", _MISTRAL)
    _key(vault, "groq", _GROQ, secret="gsk_test_ov104_status")
    body = json.dumps({"error": {"message": "missing", "dump": _BODY}, "key": _SECRET})
    client = _Client(_Resp(code, body))
    mgr, result, logs = _run(vault, key_id, client)
    assert result.http_status == code
    assert result.status == classify_http_error(code)
    assert result.unusable is False
    assert result.parked is False
    assert mgr.key_is_parked(key_id) is False
    assert key_id not in chat_unusable_ids(vault.db_path)
    assert mgr.usable_provider_count() == 2
    blocked = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            key_id,
            client=_Client(  # type: ignore[arg-type]
                _Resp(402, json.dumps({"error": {"message": "payment required", "dump": _BODY}}))
            ),
        )
    )
    assert blocked.unusable is True
    kept = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            key_id,
            client=_Client(_Resp(code, body)),  # type: ignore[arg-type]
        )
    )
    assert kept.http_status == code
    assert kept.status == classify_http_error(code)
    assert kept.unusable is True
    assert kept.parked is False
    assert mgr.key_is_parked(key_id) is False
    cleared = asyncio.run(
        probe_key_chat(
            vault,
            mgr,
            key_id,
            client=_Client(_Resp(200, "")),  # type: ignore[arg-type]
        )
    )
    assert cleared.status == "ok"
    assert cleared.unusable is False
    assert key_id not in chat_unusable_ids(vault.db_path)
    _assert_clean(vault.db_path, logs)

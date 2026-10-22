"""Single-turn key spread and a per-key circuit breaker (#106).

Mocks and fake keys only. No network. Served provider and served model stay
put; only the vault key changes. Multi-turn and prompt_cache_key stay on
rendezvous hashing.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import get_circuit_breaker, reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import (
    FallbackManager,
    note_key_used,
    rendezvous_score,
    reset_key_spread,
)
from openmw.openvault.vault.local_hop import SERVED_MODEL_HEADER, SERVED_PROVIDER_HEADER
from openmw.openvault.vault.providers import models_for
from openmw.openvault.vault.proxy import STRICT_PIN_HEADER, affinity_key_for, chat_completions
from openmw.openvault.vault.store import KeyRecord, KeyVault
from openmw.openvault.vault.usage_store import HopTrace, UsageEvent, UsageStore

_PIN = "openai/gpt-oss-120b"
_GROQ = "https://api.groq.com/openai/v1"
_OPENAI = "https://api.openai.com/v1"
_TOGETHER = "https://api.together.xyz/v1"
assert models_for("groq")[0] == _PIN
assert _PIN in models_for("together")


@pytest.fixture(autouse=True)
def _reset_spread_state() -> None:
    reset_all_circuit_breakers()
    reset_key_spread()
    yield
    reset_all_circuit_breakers()
    reset_key_spread()


@pytest.fixture()
def vault(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OPENVAULT_HOME", str(home))
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=home / "keys.db", seal=seal)


def _hop(
    vault: KeyVault,
    *,
    label: str,
    provider: str,
    secret: str,
    base_url: str,
    priority: int = 0,
    role: str = "primary",
) -> KeyRecord:
    record = vault.create(
        label=label,
        provider=provider,
        secret=secret,
        role=role,
        priority=priority,
        base_url=base_url,
    )
    vault.set_precheck(record.id, status="ok", latency_ms=10.0, error=None)
    return record


def _groq(vault: KeyVault, index: int, *, priority: int = 0) -> KeyRecord:
    return _hop(
        vault,
        label=f"groq-{index}",
        provider="groq",
        secret=f"gsk-test-ov106-{index:04d}",
        base_url=_GROQ,
        priority=priority,
    )


def _five_groq(vault: KeyVault) -> list[KeyRecord]:
    return [_groq(vault, index) for index in range(5)]


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.headers = {}
    resp.json = MagicMock(
        return_value={
            "id": "chatcmpl-ov106",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )
    return resp


class _Recorder:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.models: list[str] = []
        mock = MagicMock()
        mock.post = AsyncMock(side_effect=self._post)
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        self.mock = mock

    async def _post(self, url: str, **kwargs: Any) -> MagicMock:
        self.urls.append(url)
        body = kwargs.get("json") or {}
        model = body.get("model")
        self.models.append(model if isinstance(model, str) else "")
        return _ok_response()


def _served_key_ids(db_path: Any) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT vault_key_id FROM usage_events "
            "WHERE provider='groq' AND status>=200 AND status<300 "
            "ORDER BY created_at ASC"
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows if row[0]]


def _chat_client(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _assert_served(response: Any) -> None:
    assert response.status_code == 200
    body = response.json()
    assert response.headers.get(SERVED_PROVIDER_HEADER) == "groq"
    assert response.headers.get(SERVED_MODEL_HEADER) == _PIN
    assert body["served_provider"] == "groq"
    assert body["served_model"] == _PIN
    assert body["served_local"] is False


def test_ten_single_turn_calls_use_more_than_one_groq_key(vault: KeyVault) -> None:
    keys = _five_groq(vault)
    rec = _Recorder()
    body = {
        "model": _PIN,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "single turn"}],
    }
    with (
        TestClient(
            create_app(
                vault=vault,
                mock_health=True,
                enable_precheck_loop=False,
                cortex_url="http://127.0.0.1:9",
            ),
            client=("127.0.0.1", 5555),
        ) as client,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock),
    ):
        _identity, headers = issue_key(client, tier="free")
        responses = [
            client.post("/v1/chat/completions", json=body, headers=headers) for _ in range(10)
        ]

    for response in responses:
        _assert_served(response)
    assert rec.models == [_PIN] * 10
    assert rec.urls
    assert all("groq.com" in url for url in rec.urls)
    served = _served_key_ids(vault.db_path)
    assert len(served) == 10
    assert len(set(served)) > 1
    assert set(served) <= {key.id for key in keys}


def test_one_tripped_key_leaves_the_other_four_serving(vault: KeyVault) -> None:
    keys = _five_groq(vault)
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-test-ov106-together",
        base_url=_TOGETHER,
        priority=0,
    )
    bad = keys[0]
    breaker = get_circuit_breaker(bad.id)
    for _ in range(breaker.profile.failure_threshold):
        breaker.record_failure(status=500)
    assert breaker.state == "OPEN"
    assert get_circuit_breaker("groq").can_execute()

    rec = _Recorder()
    body = {
        "model": _PIN,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "still single"}],
    }
    with (
        _chat_client(vault) as client,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock),
    ):
        _identity, headers = issue_key(client, tier="free")
        headers = {**headers, STRICT_PIN_HEADER: "true"}
        responses = [
            client.post("/v1/chat/completions", json=body, headers=headers) for _ in range(8)
        ]

    for response in responses:
        _assert_served(response)
    assert rec.models == [_PIN] * 8
    assert all("groq.com" in url for url in rec.urls)
    assert all("together" not in url for url in rec.urls)
    served = _served_key_ids(vault.db_path)
    healthy = {key.id for key in keys if key.id != bad.id}
    assert bad.id not in served
    assert set(served) == healthy


def test_strict_pin_stays_on_groq_while_keys_change(vault: KeyVault) -> None:
    keys = _five_groq(vault)
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-test-ov106-eeee",
        base_url=_TOGETHER,
        priority=0,
    )
    rec = _Recorder()
    body = {
        "model": _PIN,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "pinned"}],
    }
    with (
        _chat_client(vault) as client,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock),
    ):
        _identity, headers = issue_key(client, tier="free")
        headers = {**headers, STRICT_PIN_HEADER: "true"}
        responses = [
            client.post("/v1/chat/completions", json=body, headers=headers) for _ in range(6)
        ]

    for response in responses:
        _assert_served(response)
    assert all("groq.com" in url for url in rec.urls)
    assert all("together" not in url for url in rec.urls)
    served = set(_served_key_ids(vault.db_path))
    assert len(served) > 1
    assert served <= {key.id for key in keys}


def test_provider_order_inside_a_band_does_not_change(vault: KeyVault) -> None:
    first = _groq(vault, 1, priority=0)
    openai = _hop(
        vault,
        label="openai",
        provider="openai",
        secret="sk-test-ov106-openai",
        base_url=_OPENAI,
        priority=0,
    )
    second = _groq(vault, 2, priority=0)
    later = _groq(vault, 3, priority=10)
    mgr = FallbackManager(vault)
    before = mgr.ordered_candidates()
    assert [row.provider for row in before] == ["groq", "openai", "groq", "groq"]
    assert before[-1].id == later.id
    note_key_used(before[0].id)
    after = mgr.ordered_candidates()
    assert [row.provider for row in after] == ["groq", "openai", "groq", "groq"]
    assert after[1].id == openai.id
    assert after[-1].id == later.id
    assert after[0].id != before[0].id
    assert {after[0].id, after[2].id} == {first.id, second.id}


def test_quota_remaining_weights_the_least_recently_used_key(vault: KeyVault) -> None:
    heavy = _groq(vault, 1)
    light = _groq(vault, 2)
    UsageStore(db_path=vault.db_path).record(
        UsageEvent(
            identity="local",
            tier="free",
            provider="groq",
            vault_key_id=heavy.id,
            total_tokens=150_000,
            status=200,
        )
    )
    mgr = FallbackManager(vault)
    assert mgr.ordered_candidates()[0].id == light.id
    for _ in range(4):
        note_key_used(light.id)
    assert mgr.ordered_candidates()[0].id == heavy.id


def test_multiturn_and_prompt_cache_key_keep_rendezvous(vault: KeyVault) -> None:
    records = _five_groq(vault)
    ids = [row.id for row in records]
    mgr = FallbackManager(vault)
    convo: list[dict[str, str]] = [
        {"role": "system", "content": "stable preamble"},
        {"role": "user", "content": "turn one"},
    ]
    affinity = affinity_key_for({"model": _PIN, "messages": convo})
    assert affinity
    expected = mgr.ordered_candidates(affinity_key=affinity)[0].id
    ranked = sorted(ids, key=lambda key_id: -rendezvous_score(affinity, key_id))
    assert expected == ranked[0]

    rec = _Recorder()

    def _once(messages: list[dict[str, str]], *, cache_key: str = "") -> HopTrace:
        body: dict[str, Any] = {"model": _PIN, "messages": messages}
        if cache_key:
            body["prompt_cache_key"] = cache_key
        trace = HopTrace()
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=rec.mock):
            status, payload = asyncio.run(chat_completions(vault, mgr, body, trace=trace))
        assert status == 200
        assert isinstance(payload, dict)
        assert payload["served_provider"] == "groq"
        assert payload["served_model"] == _PIN
        assert payload["served_local"] is False
        return trace

    seen: set[str] = set()
    for turn in range(6):
        if turn:
            convo = [
                *convo,
                {"role": "assistant", "content": f"answer {turn}"},
                {"role": "user", "content": f"question {turn}"},
            ]
        trace = _once(convo)
        assert trace.vault_key_id == expected
        seen.add(trace.vault_key_id)
    assert seen == {expected}

    cache_body = {
        "model": _PIN,
        "messages": [{"role": "user", "content": "hi"}],
        "prompt_cache_key": "ov106",
    }
    cache_affinity = affinity_key_for(cache_body)
    expected_cache = mgr.ordered_candidates(affinity_key=cache_affinity)[0].id
    cache_ranked = sorted(ids, key=lambda key_id: -rendezvous_score(cache_affinity, key_id))
    assert expected_cache == cache_ranked[0]
    for _ in range(5):
        trace = _once([{"role": "user", "content": "hi"}], cache_key="ov106")
        assert trace.vault_key_id == expected_cache
        assert trace.model_served == _PIN

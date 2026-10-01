"""T2 (#76): dead-model skip, per-model 429, no in-provider swap of a pinned model.

No network. Upstream httpx is mocked. Bodies are classified and then dropped:
they must not show up in logs, the trace, or the payload we return.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from structlog.testing import capture_logs

from openmw.openvault.route.attempt import classify_attempt
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.providers import models_for
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.store import KeyVault
from openmw.openvault.vault.usage_store import HopTrace

_REQ = "REQBODY-7f3a"
_RESP = "RESPBODY-9c2e"
_GROQ = models_for("groq")
_MODEL_A = _GROQ[0]
_MODEL_B = _GROQ[1]


@pytest.fixture(autouse=True)
def _reset_breakers() -> None:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


@pytest.fixture()
def vault(tmp_path: Any) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def _hop(vault: KeyVault, label: str, *, role: str, secret: str) -> Any:
    record = vault.create(
        label=label,
        provider="groq",
        secret=secret,
        role=role,
        priority=1,
        base_url="https://api.groq.com/openai/v1",
    )
    vault.set_precheck(record.id, status="ok", latency_ms=10.0, error=None)
    return record


def _response(status: int, body: str, payload: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    resp.headers = {}
    if payload is None:
        resp.json = MagicMock(side_effect=ValueError("not json"))
    else:
        resp.json = MagicMock(return_value=payload)
    return resp


def _client(*responses: MagicMock) -> MagicMock:
    mock = MagicMock()
    mock.post = AsyncMock(side_effect=list(responses))
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def _posted_models(mock: MagicMock) -> list[str]:
    found: list[str] = []
    for call in mock.post.await_args_list:
        body = call.kwargs.get("json") or {}
        found.append(str(body.get("model")))
    return found


def _chat(model: str) -> dict[str, Any]:
    return {"model": model, "messages": [{"role": "user", "content": _REQ}]}


def test_404_model_not_found_is_served_by_hop_2(vault: KeyVault) -> None:
    primary = _hop(vault, "groq-a", role="primary", secret="gsk-test-ov76-aaaa")
    backup = _hop(vault, "groq-b", role="backup", secret="gsk-test-ov76-bbbb")
    mgr = FallbackManager(vault)
    dead = _response(404, f'{{"error":{{"code":"model_not_found","message":"{_RESP}"}}}}')
    ok = _response(200, '{"id":"chatcmpl-hop2"}', {"id": "chatcmpl-hop2", "choices": []})
    mock = _client(dead, ok)
    trace = HopTrace()

    with (
        capture_logs() as logs,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock),
    ):
        status, payload = asyncio.run(chat_completions(vault, mgr, _chat(_MODEL_A), trace=trace))

    assert status == 200
    assert isinstance(payload, dict)
    assert payload.get("id") == "chatcmpl-hop2"
    assert trace.vault_key_id == backup.id
    assert trace.vault_key_id != primary.id
    assert trace.model_served == _MODEL_A
    assert mock.post.await_count == 2
    assert _posted_models(mock) == [_MODEL_A, _MODEL_A]
    blob = json.dumps({"payload": payload, "logs": logs, "trace": trace.__dict__})
    assert _RESP not in blob
    assert _REQ not in blob
    assert mgr._circuit(primary.id).park_until is None
    assert mgr._circuit(primary.id).failures == 0


def test_malformed_400_returns_400_after_one_upstream_call(vault: KeyVault) -> None:
    _hop(vault, "groq-a", role="primary", secret="gsk-test-ov76-cccc")
    _hop(vault, "groq-b", role="backup", secret="gsk-test-ov76-dddd")
    mgr = FallbackManager(vault)
    bad = _response(400, f'{{"error":"invalid request {_RESP}"}}')
    ok = _response(200, '{"id":"should-not-run"}', {"id": "should-not-run", "choices": []})
    mock = _client(bad, ok)

    with (
        capture_logs() as logs,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock),
    ):
        status, payload = asyncio.run(chat_completions(vault, mgr, _chat(_MODEL_A)))

    assert status == 400
    assert isinstance(payload, dict)
    assert payload["error"]["type"] == "openvault_non_retryable"
    assert mock.post.await_count == 1
    blob = json.dumps({"payload": payload, "logs": logs})
    assert _RESP not in blob
    assert _REQ not in blob


def test_429_on_model_a_serves_model_b_without_parking_the_key(vault: KeyVault) -> None:
    key = _hop(vault, "groq-a", role="primary", secret="gsk-test-ov76-eeee")
    mgr = FallbackManager(vault)
    limited = _response(429, "too many requests")
    ok = _response(200, '{"id":"chatcmpl-b"}', {"id": "chatcmpl-b", "choices": []})
    mock = _client(limited, ok)

    with (
        patch.object(mgr, "record_park", wraps=mgr.record_park) as park_spy,
        patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock),
    ):
        status, payload = asyncio.run(chat_completions(vault, mgr, _chat("auto")))

    assert status == 200
    assert isinstance(payload, dict)
    assert payload.get("served_model") == _MODEL_B
    assert payload.get("id") == "chatcmpl-b"
    assert _posted_models(mock) == [_MODEL_A, _MODEL_B]
    assert mgr.model_is_parked(key.id, _MODEL_A)
    assert mgr.model_is_parked(key.id, _MODEL_B) is False
    assert park_spy.call_count == 0
    assert mgr._circuit(key.id).park_until is None
    assert mgr._circuit(key.id).failures == 0
    assert mgr._circuit(key.id).state == "closed"


def test_pinned_model_is_not_swapped_within_a_provider(vault: KeyVault) -> None:
    primary = _hop(vault, "groq-a", role="primary", secret="gsk-test-ov76-ffff")
    backup = _hop(vault, "groq-b", role="backup", secret="gsk-test-ov76-gggg")
    mgr = FallbackManager(vault)
    limited = _response(429, "too many requests")
    ok = _response(200, '{"id":"chatcmpl-pin"}', {"id": "chatcmpl-pin", "choices": []})
    mock = _client(limited, ok)
    trace = HopTrace()

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, payload = asyncio.run(chat_completions(vault, mgr, _chat(_MODEL_A), trace=trace))

    assert status == 200
    assert isinstance(payload, dict)
    assert payload.get("served_model") == _MODEL_A
    assert payload.get("served_model") != _MODEL_B
    assert trace.vault_key_id == backup.id
    assert mock.post.await_count == 2
    assert _posted_models(mock) == [_MODEL_A, _MODEL_A]
    assert _MODEL_B not in _posted_models(mock)
    assert mgr._circuit(primary.id).park_until is not None
    assert mgr.model_is_parked(primary.id, _MODEL_A)
    assert mgr._circuit(primary.id).failures == 0


def test_402_still_parks_the_key(vault: KeyVault) -> None:
    key = _hop(vault, "groq-a", role="primary", secret="gsk-test-ov76-hhhh")
    mgr = FallbackManager(vault)
    broke = _response(402, "payment required")
    mock = _client(broke)

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, _payload = asyncio.run(chat_completions(vault, mgr, _chat("auto")))

    assert status == 502
    assert mock.post.await_count == 1
    circ = mgr._circuit(key.id)
    assert circ.park_until is not None
    assert circ.park_until > 0
    assert circ.park_reason == "credits_exhausted"
    assert circ.failures == 0
    assert circ.state == "closed"
    assert mgr.model_is_parked(key.id, _MODEL_A) is False


def test_every_model_429_parks_the_key(vault: KeyVault) -> None:
    key = _hop(vault, "groq-a", role="primary", secret="gsk-test-ov76-iiii")
    mgr = FallbackManager(vault)
    responses = tuple(_response(429, "too many requests") for _ in _GROQ)
    mock = _client(*responses)

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
        status, _payload = asyncio.run(chat_completions(vault, mgr, _chat("auto")))

    assert status == 502
    assert mock.post.await_count == len(_GROQ)
    assert _posted_models(mock) == list(_GROQ)
    assert mgr._circuit(key.id).park_until is not None
    for model in _GROQ:
        assert mgr.model_is_parked(key.id, model)


def test_404_is_model_unavailable_and_plain_400_is_dead() -> None:
    missing = classify_attempt(404, f'{{"code":"model_not_found","message":"{_RESP}"}}')
    assert missing.attempt_class == "model_unavailable"
    assert missing.job == "continue_chain"
    assert missing.candidate == "eject_for_job"
    assert missing.counts_as_hard_fail is False

    named = classify_attempt(400, "model_not_found")
    assert named.attempt_class == "model_unavailable"
    assert named.job == "continue_chain"

    retired = classify_attempt(422, "The model has been decommissioned")
    assert retired.attempt_class == "model_unavailable"
    assert retired.job == "continue_chain"

    caller = classify_attempt(400, "model field is required")
    assert caller.attempt_class == "non_retryable"
    assert caller.job == "dead"

    auth = classify_attempt(401, "invalid api key")
    assert auth.attempt_class == "auth_fail"
    assert auth.candidate == "quarantine_key"
    assert auth.job == "continue_chain"

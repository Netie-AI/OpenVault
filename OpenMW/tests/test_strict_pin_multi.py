"""Strict pins for gemini-3.5-flash and google/gemma-4-31b-it:free (#109).

No network. Upstream httpx is mocked. A decoy provider is patched so its
catalog lists the pinned id. Without the pin bind, that decoy is the hop.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import get_circuit_breaker, reset_all_circuit_breakers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.local_hop import SERVED_MODEL_HEADER, SERVED_PROVIDER_HEADER
from openmw.openvault.vault.providers import models_for
from openmw.openvault.vault.proxy import (
    PIN_UNAVAILABLE,
    STRICT_PIN_HEADER,
    chat_completions,
)
from openmw.openvault.vault.store import KeyRecord, KeyVault

_GEMINI = "gemini-3.5-flash"
_GEMMA = "google/gemma-4-31b-it:free"
_GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai"
_BAD_GOOGLE = "https://bad-google.example/v1"
_OPENROUTER = "https://openrouter.ai/api/v1"
_TOGETHER = "https://api.together.xyz/v1"
_SSE_CHUNK = b'data: {"id":"chatcmpl-pin","choices":[{"delta":{"content":"ok"}}]}\n\n'


@pytest.fixture(autouse=True)
def _reset_breakers() -> Iterator[None]:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


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
    priority: int,
) -> KeyRecord:
    record = vault.create(
        label=label,
        provider=provider,
        secret=secret,
        role="primary",
        priority=priority,
        base_url=base_url,
    )
    vault.set_precheck(record.id, status="ok", latency_ms=10.0, error=None)
    return record


def _decoy_listing(decoy: str, pin: str):
    real = models_for

    def _listing(provider: str, *, multimodal: bool = False) -> tuple[str, ...]:
        found = real(provider, multimodal=multimodal)
        if provider == decoy and pin not in found:
            return (pin, *found)
        return found

    return _listing


class _Upstream:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.bodies: list[dict[str, Any]] = []

    def client(self, *_args: Any, **_kwargs: Any) -> _Client:
        return _Client(self)


class _Client:
    def __init__(self, upstream: _Upstream) -> None:
        self._upstream = upstream

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _JsonResp:
        body = kwargs.get("json") or {}
        self._upstream.urls.append(url)
        self._upstream.bodies.append(body if isinstance(body, dict) else {})
        return _JsonResp()

    def build_request(
        self,
        _method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> object:
        del headers
        self._upstream.urls.append(url)
        self._upstream.bodies.append(json or {})
        return object()

    async def send(self, _request: object, *, stream: bool = False) -> _SseResp:
        assert stream is True
        return _SseResp()

    async def aclose(self) -> None:
        return None


class _JsonResp:
    status_code = 200
    text = ""
    headers: ClassVar[dict[str, str]] = {}

    def json(self) -> dict[str, Any]:
        return {
            "id": "chatcmpl-pin",
            "choices": [{"message": {"content": "ok"}}],
        }


class _SseResp:
    status_code = 200
    text = ""
    headers: ClassVar[dict[str, str]] = {"content-type": "text/event-stream"}

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield _SSE_CHUNK
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        return None

    async def aread(self) -> bytes:
        return b""


def _hosts(urls: list[str]) -> list[str]:
    found: list[str] = []
    for url in urls:
        if "bad-google.example" in url:
            found.append("bad-google")
        elif "googleapis.com" in url:
            found.append("google")
        elif "openrouter.ai" in url:
            found.append("openrouter")
        elif "together.xyz" in url:
            found.append("together")
        else:
            found.append(url)
    return found


def _gateway(vault: KeyVault) -> TestClient:
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=("127.0.0.1", 5555))


def _pin_body(model: str, *, stream: bool = False, strict: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if stream:
        body["stream"] = True
    if strict is not None:
        body["strict"] = strict
    return body


def _assert_pin_headers(response: Any, provider: str, model: str) -> None:
    assert response.status_code == 200
    assert response.headers.get(SERVED_PROVIDER_HEADER) == provider
    assert response.headers.get("x-openvault-served-provider") == provider
    assert response.headers.get(SERVED_MODEL_HEADER) == model
    assert response.headers.get("x-openvault-served-model") == model


def _assert_nonstream(response: Any, provider: str, model: str) -> None:
    _assert_pin_headers(response, provider, model)
    payload = response.json()
    assert payload["served_provider"] == provider
    assert payload["served_model"] == model
    assert payload["served_local"] is False


def _assert_sse(response: Any, raw: bytes, provider: str, model: str) -> None:
    _assert_pin_headers(response, provider, model)
    frames = [
        json.loads(line[6:])
        for line in raw.splitlines()
        if line.startswith(b"data: ") and line != b"data: [DONE]"
    ]
    assert frames
    assert frames[0]["served_provider"] == provider
    assert frames[0]["served_model"] == model
    assert frames[0]["served_local"] is False


def _assert_unavailable(
    payload: dict[str, Any],
    model: str,
    reason: str,
    *,
    provider: str | None,
) -> None:
    assert payload == {
        "error": {
            "message": "pinned model has no healthy hop",
            "type": PIN_UNAVAILABLE,
            "model": model,
            "reason": reason,
            "provider": provider,
            "park_reason": None,
            "retry_after_s": None,
        },
        "served_provider": None,
        "served_model": None,
        "served_local": False,
    }


def _patched(decoy: str, pin: str, upstream: _Upstream):
    return (
        patch(
            "openmw.openvault.vault.proxy.models_for",
            side_effect=_decoy_listing(decoy, pin),
        ),
        patch(
            "openmw.openvault.vault.proxy.httpx.AsyncClient",
            side_effect=upstream.client,
        ),
    )


def test_strict_gemini_nonstream_serves_google_not_a_decoy(vault: KeyVault) -> None:
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-ov109-gemini-decoy",
        base_url=_TOGETHER,
        priority=0,
    )
    _hop(
        vault,
        label="google",
        provider="google",
        secret="AIza-ov109-gemini-good",
        base_url=_GOOGLE,
        priority=10,
    )
    upstream = _Upstream()
    models_patch, http_patch = _patched("together", _GEMINI, upstream)
    with _gateway(vault) as client, models_patch, http_patch:
        _identity, headers = issue_key(client, tier="free")
        response = client.post(
            "/v1/chat/completions",
            json=_pin_body(_GEMINI),
            headers={**headers, STRICT_PIN_HEADER: "true"},
        )

    _assert_nonstream(response, "google", _GEMINI)
    assert _hosts(upstream.urls) == ["google"]
    assert upstream.bodies[0]["model"] == _GEMINI
    assert "strict" not in upstream.bodies[0]


def test_strict_gemini_sse_serves_google_headers_and_body(vault: KeyVault) -> None:
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-ov109-gemini-sse-decoy",
        base_url=_TOGETHER,
        priority=0,
    )
    _hop(
        vault,
        label="google",
        provider="google",
        secret="AIza-ov109-gemini-sse",
        base_url=_GOOGLE,
        priority=10,
    )
    upstream = _Upstream()
    models_patch, http_patch = _patched("together", _GEMINI, upstream)
    with _gateway(vault) as client, models_patch, http_patch:
        _identity, headers = issue_key(client, tier="free")
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json=_pin_body(_GEMINI, stream=True, strict=True),
            headers=headers,
        ) as response:
            raw = b"".join(response.iter_bytes())
            _assert_sse(response, raw, "google", _GEMINI)

    assert _hosts(upstream.urls) == ["google"]
    assert upstream.bodies[0]["model"] == _GEMINI
    assert "strict" not in upstream.bodies[0]


def test_strict_gemini_other_provider_alone_is_no_hop(vault: KeyVault) -> None:
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-ov109-gemini-only",
        base_url=_TOGETHER,
        priority=0,
    )
    upstream = _Upstream()
    models_patch, http_patch = _patched("together", _GEMINI, upstream)
    body = _pin_body(_GEMINI, strict=True)
    with models_patch, http_patch:
        status, payload = asyncio.run(chat_completions(vault, FallbackManager(vault), body))

    assert status == 503
    assert isinstance(payload, dict)
    _assert_unavailable(payload, _GEMINI, "no_hop", provider="google")
    assert upstream.urls == []


def test_strict_gemma_nonstream_serves_openrouter_not_a_decoy(vault: KeyVault) -> None:
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-ov109-gemma-decoy",
        base_url=_TOGETHER,
        priority=0,
    )
    _hop(
        vault,
        label="openrouter",
        provider="openrouter",
        secret="sk-or-ov109-gemma-good",
        base_url=_OPENROUTER,
        priority=10,
    )
    upstream = _Upstream()
    models_patch, http_patch = _patched("together", _GEMMA, upstream)
    with _gateway(vault) as client, models_patch, http_patch:
        _identity, headers = issue_key(client, tier="free")
        response = client.post(
            "/v1/chat/completions",
            json=_pin_body(_GEMMA),
            headers={**headers, STRICT_PIN_HEADER: "true"},
        )

    _assert_nonstream(response, "openrouter", _GEMMA)
    assert _hosts(upstream.urls) == ["openrouter"]
    assert upstream.bodies[0]["model"] == _GEMMA
    assert "strict" not in upstream.bodies[0]


def test_strict_gemma_sse_serves_openrouter_headers_and_body(vault: KeyVault) -> None:
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-ov109-gemma-sse-decoy",
        base_url=_TOGETHER,
        priority=0,
    )
    _hop(
        vault,
        label="openrouter",
        provider="openrouter",
        secret="sk-or-ov109-gemma-sse",
        base_url=_OPENROUTER,
        priority=10,
    )
    upstream = _Upstream()
    models_patch, http_patch = _patched("together", _GEMMA, upstream)
    with _gateway(vault) as client, models_patch, http_patch:
        _identity, headers = issue_key(client, tier="free")
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json=_pin_body(_GEMMA, stream=True, strict=True),
            headers=headers,
        ) as response:
            raw = b"".join(response.iter_bytes())
            _assert_sse(response, raw, "openrouter", _GEMMA)

    assert _hosts(upstream.urls) == ["openrouter"]
    assert upstream.bodies[0]["model"] == _GEMMA
    assert "strict" not in upstream.bodies[0]


def test_strict_gemma_other_provider_alone_is_no_hop(vault: KeyVault) -> None:
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-ov109-gemma-only",
        base_url=_TOGETHER,
        priority=0,
    )
    upstream = _Upstream()
    models_patch, http_patch = _patched("together", _GEMMA, upstream)
    body = _pin_body(_GEMMA, strict=True)
    with models_patch, http_patch:
        status, payload = asyncio.run(chat_completions(vault, FallbackManager(vault), body))

    assert status == 503
    assert isinstance(payload, dict)
    _assert_unavailable(payload, _GEMMA, "no_hop", provider="openrouter")
    assert upstream.urls == []


def test_strict_unknown_model_is_not_in_catalog(vault: KeyVault) -> None:
    _hop(
        vault,
        label="google",
        provider="google",
        secret="AIza-ov109-catalog",
        base_url=_GOOGLE,
        priority=0,
    )
    _hop(
        vault,
        label="openrouter",
        provider="openrouter",
        secret="sk-or-ov109-catalog",
        base_url=_OPENROUTER,
        priority=1,
    )
    upstream = _Upstream()
    http_patch = patch(
        "openmw.openvault.vault.proxy.httpx.AsyncClient",
        side_effect=upstream.client,
    )
    body = _pin_body("not-a-catalog-id", strict=True)
    with http_patch:
        status, payload = asyncio.run(chat_completions(vault, FallbackManager(vault), body))

    assert status == 503
    assert isinstance(payload, dict)
    _assert_unavailable(payload, "not-a-catalog-id", "not_in_catalog", provider=None)
    assert upstream.urls == []


def test_strict_gemini_tripped_key_leaves_sibling_serving(vault: KeyVault) -> None:
    _hop(
        vault,
        label="together",
        provider="together",
        secret="tg-ov109-sibling-decoy",
        base_url=_TOGETHER,
        priority=0,
    )
    bad = _hop(
        vault,
        label="google-bad",
        provider="google",
        secret="AIza-ov109-sibling-bad",
        base_url=_BAD_GOOGLE,
        priority=1,
    )
    _hop(
        vault,
        label="google-good",
        provider="google",
        secret="AIza-ov109-sibling-good",
        base_url=_GOOGLE,
        priority=2,
    )
    breaker = get_circuit_breaker(bad.id)
    for _ in range(breaker.profile.failure_threshold):
        breaker.record_failure(status=500)
    assert breaker.state == "OPEN"
    assert get_circuit_breaker("google").can_execute()

    upstream = _Upstream()
    models_patch, http_patch = _patched("together", _GEMINI, upstream)
    with _gateway(vault) as client, models_patch, http_patch:
        _identity, headers = issue_key(client, tier="free")
        response = client.post(
            "/v1/chat/completions",
            json=_pin_body(_GEMINI),
            headers={**headers, STRICT_PIN_HEADER: "true"},
        )

    _assert_nonstream(response, "google", _GEMINI)
    assert _hosts(upstream.urls) == ["google"]
    assert "bad-google.example" not in "".join(upstream.urls)
    assert "together.xyz" not in "".join(upstream.urls)
    assert upstream.bodies[0]["model"] == _GEMINI

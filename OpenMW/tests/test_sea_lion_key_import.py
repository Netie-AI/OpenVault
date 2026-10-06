"""SEA-LION key import stores catalog id sea_lion.

Fixtures only. No network. An unknown provider stays a 422.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.route.breaker import reset_all_circuit_breakers
from openmw.openvault.vault.chat_probe import CHAT_PROBE_LOW_CAP_PROVIDERS, chat_probe_target
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.fallback import FallbackManager
from openmw.openvault.vault.key_add import add_tested_key
from openmw.openvault.vault.providers import get_provider, spendable_for_freeroute
from openmw.openvault.vault.proxy import chat_completions
from openmw.openvault.vault.store import KeyVault

_FAKE_SEA = "sk-sealion-fixture-not-real-0001"
_FAKE_ALT = "sk-sealion-alias-fixture-0002"
_FAKE_BAD = "sk-not-a-real-provider-key-01"
_NOT_CHAT = (
    "aisingapore/SEA-Guard",
    "aisingapore/SEA-LION-ModernBERT-Embedding-600M",
)
_CHAT = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture(autouse=True)
def _reset_breakers() -> Any:
    reset_all_circuit_breakers()
    yield
    reset_all_circuit_breakers()


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    return root


@pytest.fixture()
def vault(home: Path) -> KeyVault:
    return KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))


def _client(vault: KeyVault) -> TestClient:
    return TestClient(
        create_app(vault=vault, mock_health=True, enable_precheck_loop=False),
        client=("127.0.0.1", 5555),
    )


def _sea_spec() -> Any:
    spec = get_provider("sea_lion")
    assert spec is not None
    return spec


@pytest.mark.parametrize("env_key", ["SEA_LION_API_KEY", "SEALION_API_KEY"])
def test_ingest_sea_lion_env_maps_to_sea_lion(vault: KeyVault, env_key: str) -> None:
    secret = _FAKE_SEA if env_key == "SEA_LION_API_KEY" else _FAKE_ALT
    client = _client(vault)
    written = client.post(
        "/api/vault/ingest-env",
        json={"dry_run": False, "env_text": f"{env_key}={secret}\n"},
    )
    assert written.status_code == 200, written.text
    body = written.json()
    assert body["imported"] == 1
    assert body["results"][0]["ok"] is True
    assert body["results"][0]["provider"] == "sea_lion"
    assert secret not in written.text
    keys = vault.list_keys()
    assert len(keys) == 1
    assert keys[0].provider == "sea_lion"
    assert keys[0].base_url == _sea_spec().base_url
    assert vault.get_secret(keys[0].id) == secret


def test_post_sea_lion_is_200_and_unknown_provider_is_422(vault: KeyVault) -> None:
    client = _client(vault)
    for provider in ("not_a_provider", "sealion", "sea-lion"):
        refused = client.post(
            "/api/keys",
            json={
                "label": "refused",
                "provider": provider,
                "secret": _FAKE_BAD,
                "role": "free",
            },
        )
        assert refused.status_code == 422, provider
        assert _FAKE_BAD not in refused.text
    assert vault.list_keys() == []

    saved = client.post(
        "/api/keys",
        json={
            "label": "SEA-LION",
            "provider": "sea_lion",
            "secret": _FAKE_SEA,
            "role": "free",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["provider"] == "sea_lion"
    assert _FAKE_SEA not in saved.text
    keys = vault.list_keys()
    assert len(keys) == 1
    assert keys[0].provider == "sea_lion"
    assert keys[0].base_url == _sea_spec().base_url


def test_add_tested_key_accepts_sea_lion(vault: KeyVault) -> None:
    spec = _sea_spec()
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["model"] = json.loads(request.content.decode())["model"]
        return httpx.Response(200, json={"id": "chatcmpl-fixture"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = add_tested_key(vault, "sea_lion", _FAKE_SEA, client=client)
    assert result.ok is True
    assert result.error == ""
    assert "api.sea-lion.ai" in seen["url"]
    assert seen["model"] == spec.chat_models[0]
    assert seen["model"] not in _NOT_CHAT
    keys = vault.list_keys()
    assert len(keys) == 1
    assert keys[0].provider == "sea_lion"
    assert keys[0].base_url == spec.base_url


def test_freeroute_lists_and_selects_sea_lion(
    vault: KeyVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENVAULT_LOCAL_BASE_URL", raising=False)
    spec = _sea_spec()
    assert spec.openai_compatible is True
    for banned in _NOT_CHAT:
        assert banned not in spec.chat_models
    assert "sea_lion" in CHAT_PROBE_LOW_CAP_PROVIDERS
    target = chat_probe_target("sea_lion")
    assert target is not None
    assert target.model in spec.chat_models
    assert target.model not in _NOT_CHAT

    spendable = {row["id"]: row for row in spendable_for_freeroute()}
    row = spendable["sea_lion"]
    assert row["base_url"] == spec.base_url
    assert row["openai_compatible"] is True
    assert row["chat_models"] == list(spec.chat_models)

    rec = vault.create(
        label="SEA-LION",
        provider="sea_lion",
        secret=_FAKE_SEA,
        role="free",
        base_url=spec.base_url,
    )
    vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(
            {
                "url": str(request.url),
                "model": json.loads(request.content.decode())["model"],
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "model": "upstream-name",
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", factory):
        status, payload = asyncio.run(chat_completions(vault, FallbackManager(vault), dict(_CHAT)))
    assert status == 200
    assert isinstance(payload, dict)
    assert payload["served_provider"] == "sea_lion"
    assert payload["served_local"] is False
    assert posted
    assert "api.sea-lion.ai" in posted[0]["url"]
    assert posted[0]["model"] in spec.chat_models
    assert posted[0]["model"] not in _NOT_CHAT
    assert _FAKE_SEA not in json.dumps(payload)

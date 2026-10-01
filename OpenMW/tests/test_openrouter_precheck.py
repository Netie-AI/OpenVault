"""Honest OpenRouter precheck. The HTTP client is a fake. No network."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.key_quota import quota_snapshot
from openmw.openvault.vault.precheck import OPENROUTER_KEY_URL, precheck_one
from openmw.openvault.vault.store import KeyVault

_FAKE_OR = "sk-or-v1-test-ov93-not-live"
_FAKE_GROQ = "gsk_test_ov93_not_live"
_MARKER = "RESPBODY-ov93-precheck"
_GROQ = "https://api.groq.com/openai/v1"
_OPENROUTER = "https://openrouter.ai/api/v1"


class _Resp:
    def __init__(self, code: int, payload: object | None = None) -> None:
        self.status_code = code
        self._payload = payload
        self.json_reads = 0
        self.text_reads = 0

    def json(self) -> object:
        self.json_reads += 1
        return {} if self._payload is None else self._payload

    @property
    def text(self) -> str:
        self.text_reads += 1
        return f"{_MARKER} {_FAKE_OR}"


class _Client:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp
        self.urls: list[str] = []
        self.headers: dict[str, str] = {}

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
        self.urls.append(url)
        self.headers = dict(headers or {})
        return self.resp

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KeyVault:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    return KeyVault(db_path=tmp_path / "keys.db", seal=Seal(Fernet.generate_key()))


def _run(vault: KeyVault, *, provider: str, secret: str, base_url: str, resp: _Resp) -> Any:
    record = vault.create(
        label=provider,
        provider=provider,
        secret=secret,
        role="free",
        priority=1,
        base_url=base_url,
    )
    client = _Client(resp)
    built: list[str] = []

    def _factory(*_args: object, **_kwargs: object) -> _Client:
        built.append("built")
        return client

    with patch("openmw.openvault.vault.precheck.httpx.AsyncClient", _factory):
        result = asyncio.run(precheck_one(vault, record.id))
    assert built == ["built"]
    assert secret not in " ".join(client.urls)
    assert client.headers.get("Authorization", "").startswith("Bearer ")
    assert secret not in client.urls[0]
    return record, client, result


def _probe_blob(db: Path) -> str:
    with sqlite3.connect(str(db)) as conn:
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='openrouter_probe'"
        ).fetchone()
        if found is None:
            return ""
        rows = conn.execute("SELECT * FROM openrouter_probe").fetchall()
    return repr(rows)


def test_openrouter_precheck_2xx_records_two_fields_without_network(vault: KeyVault) -> None:
    payload = {
        "data": {
            "label": _FAKE_OR,
            "limit": 10,
            "usage": 1,
            "limit_remaining": 12.5,
            "is_free_tier": True,
            "body_marker": _MARKER,
        }
    }
    record, client, result = _run(
        vault,
        provider="openrouter",
        secret=_FAKE_OR,
        base_url=_OPENROUTER,
        resp=_Resp(200, payload),
    )
    assert client.urls == [OPENROUTER_KEY_URL]
    assert client.resp.json_reads == 1
    assert client.resp.text_reads == 0
    assert result.status == "ok"
    assert result.limit_remaining == 12.5
    assert result.is_free_tier is True
    assert result.error is None
    stored = vault.get(record.id)
    assert stored is not None
    assert stored.precheck_status == "ok"
    assert stored.last_error is None
    blob = _probe_blob(vault.db_path)
    assert "12.5" in blob
    assert _FAKE_OR not in blob
    assert _MARKER not in blob
    assert "label" not in blob
    assert "body_marker" not in blob
    view = repr(quota_snapshot(vault))
    assert _FAKE_OR not in view
    assert _MARKER not in view
    assert "12.5" in view


def test_openrouter_precheck_non_2xx_is_failed_without_network(vault: KeyVault) -> None:
    record, client, result = _run(
        vault,
        provider="openrouter",
        secret=_FAKE_OR,
        base_url=_OPENROUTER,
        resp=_Resp(401, {"error": {"message": _MARKER, "key": _FAKE_OR}}),
    )
    assert client.urls == [OPENROUTER_KEY_URL]
    assert client.resp.json_reads == 0
    assert client.resp.text_reads == 0
    assert result.status == "failed"
    assert result.error == "HTTP 401"
    stored = vault.get(record.id)
    assert stored is not None
    assert stored.precheck_status == "failed"
    assert stored.last_error == "HTTP 401"
    blob = _probe_blob(vault.db_path)
    assert _FAKE_OR not in blob
    assert _MARKER not in blob
    assert blob == "[]"
    view = repr(quota_snapshot(vault))
    assert _FAKE_OR not in view
    assert _MARKER not in view
    assert "failed" in view


def test_groq_precheck_still_uses_models(vault: KeyVault) -> None:
    record, client, result = _run(
        vault,
        provider="groq",
        secret=_FAKE_GROQ,
        base_url=_GROQ,
        resp=_Resp(401, {"error": _MARKER}),
    )
    assert len(client.urls) == 1
    assert client.urls[0].endswith("/models")
    assert OPENROUTER_KEY_URL not in client.urls[0]
    assert client.resp.text_reads == 1
    assert result.status == "auth_fail"
    assert result.status != "failed"
    assert result.error == "HTTP 401"
    stored = vault.get(record.id)
    assert stored is not None
    assert stored.precheck_status == "auth_fail"
    assert stored.last_error == "HTTP 401"
    assert _MARKER not in (stored.last_error or "")
    assert _FAKE_GROQ not in (stored.last_error or "")

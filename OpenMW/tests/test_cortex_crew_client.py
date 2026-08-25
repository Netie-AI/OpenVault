"""Cortex skills/crew index: talk to Cortex, never keep the bodies (DR-0012)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.mesh.cortex_client import CortexClient, strip_skill_bodies
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


def test_strip_skill_bodies_drops_prompt_text() -> None:
    raw = {
        "skills": [
            {
                "id": "outreach.human-email",
                "tag": "outreach",
                "skill_body": "write like a human",
                "prompt": "secret",
            }
        ],
        "transcript": "child chat",
    }
    cleaned = strip_skill_bodies(raw)
    assert cleaned["skills"][0]["id"] == "outreach.human-email"
    assert cleaned["skills"][0]["tag"] == "outreach"
    assert "skill_body" not in cleaned["skills"][0]
    assert "prompt" not in cleaned["skills"][0]
    assert "transcript" not in cleaned


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self) -> Any:
        if isinstance(self._payload, str):
            raise json.JSONDecodeError("not json", self._payload, 0)
        return self._payload


class _FakeSkillsClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeSkillsClient:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def get(self, url: str) -> _Resp:
        if url.endswith("/api/skills"):
            return _Resp(
                200,
                {
                    "skills": [
                        {
                            "id": "outreach.human-email",
                            "tag": "outreach",
                            "skill_body": "MUST-NOT-LEAK",
                            "instructions": "also secret",
                        }
                    ]
                },
            )
        if url.endswith("/api/crew"):
            return _Resp(
                200,
                {
                    "runs": [
                        {
                            "id": "run-parent-1",
                            "status": "open",
                            "transcript": "child ramble",
                        }
                    ]
                },
            )
        return _Resp(404, {})


def test_skills_index_keeps_ids_and_drops_bodies() -> None:
    async def _run() -> dict[str, Any]:
        client = CortexClient("http://127.0.0.1:8010")
        with patch("openmw.openvault.mesh.cortex_client.httpx.AsyncClient", _FakeSkillsClient):
            return await client.skills_index()

    body = asyncio.run(_run())
    assert body["online"] is True
    assert body["owner"] == "cortex"
    assert body["location"].endswith("/api/skills")
    assert body["skills"] == [{"id": "outreach.human-email", "tag": "outreach"}]
    dumped = json.dumps(body)
    assert "MUST-NOT-LEAK" not in dumped
    assert "also secret" not in dumped


def test_crew_index_keeps_run_ids_and_drops_transcripts() -> None:
    async def _run() -> dict[str, Any]:
        client = CortexClient("http://127.0.0.1:8010")
        with patch("openmw.openvault.mesh.cortex_client.httpx.AsyncClient", _FakeSkillsClient):
            return await client.crew_index()

    body = asyncio.run(_run())
    assert body["online"] is True
    assert body["runs"] == [{"id": "run-parent-1", "status": "open"}]
    assert "child ramble" not in json.dumps(body)


def test_http_skills_index_when_cortex_offline(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(home))
    vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    http = TestClient(app, client=("127.0.0.1", 5555))

    skills = http.get("/api/cortex/skills").json()
    assert skills["online"] is False
    assert skills["owner"] == "cortex"
    assert skills["skills"] == []
    assert "skill_body" not in skills

    crew = http.get("/api/cortex/crew").json()
    assert crew["online"] is False
    assert crew["runs"] == []

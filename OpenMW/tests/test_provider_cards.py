"""Provider cards: POST-only add behind the admin credential, catalog cards only."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import inject_admin_credential
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from openmw.openvault.app import create_app
from openmw.openvault.vault.admin_token import ADMIN_HEADER, ensure_admin_token
from openmw.openvault.vault.key_add import ProbeResult
from openmw.openvault.vault.provider_cards import icon_path, iter_cards
from openmw.openvault.vault.providers import get_provider

_SECRET = "sk-unit-test-key-9f3c2a7b-do-not-log"
_ADD = "/api/keys/cards"


@pytest.fixture(autouse=True)
def _do_not_inject_admin() -> Any:
    token = inject_admin_credential.set(False)
    yield
    inject_admin_credential.reset(token)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    monkeypatch.delenv("OPENVAULT_ADMIN_TOKEN_PATH", raising=False)
    monkeypatch.delenv("OPENVAULT_REQUIRE_API_KEY", raising=False)
    return tmp_path


def _client() -> TestClient:
    return TestClient(
        create_app(
            mock_health=True,
            enable_precheck_loop=False,
            cortex_url="http://127.0.0.1:9",
        ),
        client=("127.0.0.1", 5555),
    )


def _admin() -> dict[str, str]:
    return {ADMIN_HEADER: ensure_admin_token()}


def _probe_ok(
    spec: Any, secret: str, *, client: Any = None, timeout_s: float = 20.0
) -> ProbeResult:
    del spec, secret, client, timeout_s
    return ProbeResult(True, 200, "")


def _probe_fail(
    spec: Any, secret: str, *, client: Any = None, timeout_s: float = 20.0
) -> ProbeResult:
    del spec, secret, client, timeout_s
    return ProbeResult(False, 401, "test call failed (HTTP 401)")


def _keys(client: TestClient) -> list[dict[str, Any]]:
    listed = client.get("/api/keys", headers=_admin())
    assert listed.status_code == 200
    rows = listed.json()["keys"]
    assert isinstance(rows, list)
    return rows


def test_post_without_admin_token_is_401(home: Path) -> None:
    client = _client()
    response = client.post(_ADD, json={"provider": "groq", "secret": _SECRET})
    assert response.status_code == 401
    assert _SECRET not in response.text
    assert _keys(client) == []


def test_get_add_endpoint_is_405(home: Path) -> None:
    response = _client().get(_ADD, headers=_admin())
    assert response.status_code == 405


def test_right_token_and_probe_adds_without_echoing_the_key(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("openmw.openvault.vault.key_add.probe_catalog_chat", _probe_ok)
    client = _client()
    response = client.post(
        _ADD,
        json={"provider": "groq", "secret": _SECRET},
        headers=_admin(),
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"label", "masked_id", "outcome"}
    assert body["outcome"] == "added"
    assert body["label"] == "Groq"
    assert body["masked_id"]
    assert _SECRET not in response.text
    assert _SECRET not in str(response.headers)
    stored = _keys(client)
    assert len(stored) == 1
    assert stored[0]["provider"] == "groq"
    assert _SECRET not in str(stored)


def test_failed_probe_stores_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openmw.openvault.vault.key_add.probe_catalog_chat", _probe_fail)
    client = _client()
    response = client.post(
        _ADD,
        json={"provider": "groq", "secret": _SECRET},
        headers=_admin(),
    )
    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"label", "masked_id", "outcome"}
    assert body["outcome"] == "test call failed (HTTP 401)"
    assert body["masked_id"] == ""
    assert _SECRET not in response.text
    assert _keys(client) == []


def test_duplicate_is_reported_and_not_stored_twice(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("openmw.openvault.vault.key_add.probe_catalog_chat", _probe_ok)
    client = _client()
    headers = _admin()
    first = client.post(_ADD, json={"provider": "groq", "secret": _SECRET}, headers=headers)
    assert first.status_code == 200
    assert first.json()["outcome"] == "added"
    second = client.post(_ADD, json={"provider": "groq", "secret": _SECRET}, headers=headers)
    assert second.status_code == 200
    body = second.json()
    assert body["outcome"] == "duplicate"
    assert body["label"] == "Groq"
    assert body["masked_id"]
    assert _SECRET not in second.text
    assert len(_keys(client)) == 1


def test_key_never_in_logs_or_response(
    home: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("openmw.openvault.vault.key_add.probe_catalog_chat", _probe_ok)
    client = _client()
    with caplog.at_level(logging.DEBUG), capture_logs() as logs:
        denied = client.post(_ADD, json={"provider": "groq", "secret": _SECRET})
        added = client.post(
            _ADD,
            json={"provider": "groq", "secret": _SECRET},
            headers=_admin(),
        )
    assert denied.status_code == 401
    assert added.status_code == 200
    blob = "\n".join([caplog.text, repr(logs), denied.text, added.text, str(added.headers)])
    assert _SECRET not in blob


def test_page_omits_deepseek_and_cloudflare_and_stays_in_catalog(home: Path) -> None:
    response = _client().get("/provider-cards")
    assert response.status_code == 200
    html = response.text
    lowered = html.lower()
    assert "deepseek" not in lowered
    assert "cloudflare" not in lowered
    assert "sign in with chatgpt" not in lowered
    ids = re.findall(r'data-provider="([a-z0-9_]+)"', html)
    assert ids
    catalog_ids = {spec.id for spec in _catalog()}
    for provider_id in ids:
        assert provider_id in catalog_ids
        assert get_provider(provider_id) is not None
    assert "deepseek" not in ids
    assert "Get key" in html
    assert 'type="password"' in html
    assert "Test and add" in html
    assert 'data-section="free"' in html
    assert 'data-section="premium"' in html
    assert "Paste your API key." in html
    assert "subscription" not in lowered
    assert not re.search(r'src="https?:', html)
    for card in iter_cards():
        assert f'data-provider="{card.id}"' in html
        assert card.register_url in html
        assert card.icon.startswith("/provider-cards/icons/")
        assert icon_path(f"{card.id}.svg") is not None


def test_icons_are_local_and_licensed() -> None:
    notice = (
        Path(__file__).resolve().parents[1]
        / "openmw"
        / "openvault"
        / "static"
        / "provider_cards"
        / "NOTICE"
    )
    text = notice.read_text(encoding="utf-8")
    assert "CC0-1.0" in text
    assert "hotlinked" in text
    for card in iter_cards():
        path = icon_path(f"{card.id}.svg")
        assert path is not None
        assert path.suffix == ".svg"
        body = path.read_text(encoding="utf-8")
        assert "<image" not in body
        assert "xlink:href" not in body
        assert 'href="http' not in body


def test_icon_route_serves_local_svg_and_rejects_others(home: Path) -> None:
    client = _client()
    ok = client.get("/provider-cards/icons/groq.svg")
    assert ok.status_code == 200
    assert "svg" in ok.headers["content-type"]
    assert ok.text.startswith("<svg")
    missing = client.get("/provider-cards/icons/deepseek.svg")
    assert missing.status_code == 404
    assert _SECRET not in missing.text


def test_sealed_vault_stores_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openmw.openvault.vault.key_add.probe_catalog_chat", _probe_ok)
    client = _client()
    headers = _admin()
    locked = client.post("/api/vault/lock", headers=headers)
    assert locked.status_code == 200
    response = client.post(
        _ADD,
        json={"provider": "groq", "secret": _SECRET},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["outcome"] == "vault is sealed"
    assert _SECRET not in response.text
    assert _keys(client) == []


def test_cards_json_is_catalog_only_and_bad_body_is_not_echoed(home: Path) -> None:
    client = _client()
    listed = client.get("/api/providers/cards")
    assert listed.status_code == 200
    ids: list[str] = []
    for section in listed.json()["sections"]:
        for card in section["cards"]:
            ids.append(card["id"])
            assert get_provider(card["id"]) is not None
            assert card["register_url"].startswith("https://")
    assert ids
    assert "deepseek" not in ids
    assert "cloudflare" not in ids
    raw = client.post(
        _ADD,
        content=_SECRET.encode(),
        headers={**_admin(), "content-type": "application/json"},
    )
    assert raw.status_code == 400
    assert _SECRET not in raw.text
    assert _keys(client) == []


def test_console_page_does_not_hardcode_excluded_providers() -> None:
    root = Path(__file__).resolve().parents[2]
    page = (root / "apps" / "web" / "src" / "app" / "providers" / "page.tsx").read_text(
        encoding="utf-8"
    )
    lowered = page.lower()
    assert "test and add" in lowered
    assert "get key" in lowered
    assert 'type="password"' in page
    assert "deepseek" not in lowered
    assert "cloudflare" not in lowered
    assert "sign in with chatgpt" not in lowered


def _catalog() -> list[Any]:
    from openmw.openvault.vault.providers import PROVIDER_CATALOG

    return list(PROVIDER_CATALOG)

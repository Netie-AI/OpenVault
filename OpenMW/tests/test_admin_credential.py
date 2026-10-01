"""Admin credential for key/secret routes and /keys, including loopback (#83).

The token is not an ov_ key. Tests in this module do not inject it: each call
sends the header it means to send.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import inject_admin_credential
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.admin_token import (
    ADMIN_HEADER,
    admin_token_path,
    ensure_admin_token,
    path_needs_admin,
)
from openmw.openvault.vault.providers import get_provider

_CHAT = {
    "model": "auto",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 1,
}
_WRONG = "wrong-admin-token-not-the-file"


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


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(
        create_app(mock_health=True, enable_precheck_loop=False, cortex_url="http://127.0.0.1:9"),
        client=(host, 5555),
    )


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keyless_loopback_keys_is_401(home: Path) -> None:
    response = _client().get("/api/keys")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "unauthorized"


def test_wrong_admin_token_is_401(home: Path) -> None:
    response = _client().get("/api/keys", headers={ADMIN_HEADER: _WRONG})
    assert response.status_code == 401
    assert _WRONG not in response.text
    assert ensure_admin_token() not in response.text


def test_right_admin_token_is_200(home: Path) -> None:
    token = ensure_admin_token()
    assert not token.startswith("ov_")
    response = _client().get("/api/keys", headers={ADMIN_HEADER: token})
    assert response.status_code == 200
    assert response.json()["keys"] == []
    assert token not in response.text
    assert token not in str(response.headers)


def test_token_never_in_logs_or_bodies(home: Path, caplog: pytest.LogCaptureFixture) -> None:
    token = ensure_admin_token()
    client = _client()
    with caplog.at_level(logging.INFO):
        denied = client.get("/api/keys", headers={ADMIN_HEADER: _WRONG})
        allowed = client.get("/api/keys", headers={ADMIN_HEADER: token})
        denied_secret = client.get("/api/secrets", headers={ADMIN_HEADER: _WRONG})
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert denied_secret.status_code == 401
    blob = "\n".join(
        [
            caplog.text,
            denied.text,
            allowed.text,
            denied_secret.text,
            str(denied.headers),
            str(allowed.headers),
        ]
    )
    assert token not in blob
    assert _WRONG not in blob
    assert "openvault_admin_auth" in caplog.text


def test_freeroute_healthz_and_v1_unchanged(home: Path) -> None:
    client = _client()
    health = client.get("/api/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    status = client.get("/api/freeroute/status")
    assert status.status_code == 200
    assert status.json()["ok"] is True
    chat = client.post("/v1/chat/completions", json=_CHAT)
    assert chat.status_code == 503
    assert chat.json()["error"]["type"] == "openvault_no_keys"
    token = ensure_admin_token()
    assert token not in health.text
    assert token not in status.text
    assert token not in chat.text


def test_admin_token_does_not_replace_a_remote_api_key(home: Path) -> None:
    token = ensure_admin_token()
    remote = _client("203.0.113.10")
    response = remote.get("/api/keys", headers={ADMIN_HEADER: token})
    assert response.status_code == 401
    assert token not in response.text


def test_admin_paths_are_keys_secrets_and_signing_keys() -> None:
    assert path_needs_admin("/api/keys")
    assert path_needs_admin("/api/secrets/passwords")
    assert path_needs_admin("/api/apikeys")
    assert path_needs_admin("/api/vault/status")
    assert path_needs_admin("/keys/jwks")
    assert path_needs_admin("/keys/services")
    assert path_needs_admin("/api/accounts/acct/keys")
    assert not path_needs_admin("/api/freeroute/status")
    assert not path_needs_admin("/api/healthz")
    assert not path_needs_admin("/v1/chat/completions")
    assert not path_needs_admin("/.well-known/jwks.json")
    assert not path_needs_admin("/api/local/grants")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_token_file_is_created_0600(home: Path) -> None:
    _client()
    path = admin_token_path()
    assert path == home / "admin_token"
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    first = path.read_text(encoding="utf-8").strip()
    assert first == ensure_admin_token()
    assert not first.startswith("ov_")


def test_token_path_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "nested" / "admin_token"
    monkeypatch.setenv("OPENVAULT_ADMIN_TOKEN_PATH", str(dest))
    token = ensure_admin_token()
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8").strip() == token
    assert ensure_admin_token() == token
    if sys.platform != "win32":
        assert dest.stat().st_mode & 0o777 == 0o600


def test_add_key_script_sends_file_token_not_argv(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = ensure_admin_token()
    script = Path(__file__).resolve().parents[2] / "scripts" / "add_key.py"
    add_key = _load(script, "ov_add_key")
    seen: dict[str, str] = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"keys":[]}'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    def _urlopen(req: Any, timeout: float = 20) -> _Resp:
        found = {key.lower(): value for key, value in req.header_items()}
        seen["token"] = found.get("x-openvault-admin", "")
        return _Resp()

    monkeypatch.setattr(add_key.urllib.request, "urlopen", _urlopen)
    listed = add_key.call("http://127.0.0.1:5000", "/api/keys")
    assert listed == {"keys": []}
    assert seen["token"] == token
    assert "--admin-token" not in add_key.sys.argv
    assert token not in " ".join(sys.argv)


def test_secret_retrieve_sends_admin_header(home: Path) -> None:
    token = ensure_admin_token()
    retrieve = _load(
        Path(__file__).resolve().parents[2] / "apps" / "cli" / "secret_retrieve.py",
        "ov_secret_retrieve",
    )
    seen: list[dict[str, str]] = []

    def http(
        method: str, url: str, headers: dict[str, str] | None, body: bytes | None
    ) -> tuple[int, dict[str, Any], str]:
        del method, body
        seen.append(dict(headers or {}))
        if url.endswith("/api/vault/status"):
            return 200, {"sealed": False}, "{}"
        if url.endswith("/api/keys"):
            return 200, {"keys": [{"id": "k1", "label": "k1"}]}, "{}"
        return 200, {"secret": "provider-secret"}, "{}"

    got = retrieve.retrieve_secret("http://127.0.0.1:9", "k1", kind_hint="key", http=http)
    assert got["secret"] == "provider-secret"
    assert seen
    assert all(item.get(ADMIN_HEADER) == token for item in seen)
    dumped = json.dumps(got)
    assert token not in dumped


def test_cli_admin_http_attaches_token_and_grant_does_not(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = ensure_admin_token()
    cli = _load(
        Path(__file__).resolve().parents[2] / "apps" / "cli" / "openvault_cli.py",
        "ov_openvault_cli",
    )
    seen: dict[str, dict[str, str]] = {}

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b"{}"

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

    def _urlopen(req: Any, timeout: float = 10) -> _Resp:
        found = {key.lower(): value for key, value in req.header_items()}
        seen[req.full_url] = found
        return _Resp()

    monkeypatch.setattr(cli.urllib.request, "urlopen", _urlopen)
    code, body = cli._http_json("GET", "http://127.0.0.1:5000/api/keys")
    assert code == 200
    assert body == {}
    assert seen["http://127.0.0.1:5000/api/keys"]["x-openvault-admin"] == token
    cli._http_json("POST", "http://127.0.0.1:5000/api/local/grants", {"client_name": "x"})
    grant_headers = seen["http://127.0.0.1:5000/api/local/grants"]
    assert "x-openvault-admin" not in grant_headers
    assert token not in json.dumps(grant_headers)


def test_key_add_probe_does_not_send_the_admin_token(home: Path) -> None:
    """Provider probes must not receive the vault admin credential."""
    from openmw.openvault.vault.key_add import probe_catalog_chat

    token = ensure_admin_token()
    spec = get_provider("groq")
    assert spec is not None
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["admin"] = request.headers.get(ADMIN_HEADER)
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"choices": []})

    result = probe_catalog_chat(
        spec,
        "gsk-unit-only",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert result.ok is True
    assert seen["admin"] is None
    assert seen["authorization"] == "Bearer gsk-unit-only"
    assert token not in (seen["authorization"] or "")

"""SEC-GUARD (#72): fail-closed auth on /api and /keys, docs off, GET is read-only.

The route walk hits every declared (path, method) pair, including POST/PUT/
PATCH/DELETE. Handlers without their own loopback check are still covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import issue_key
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.mesh.local_mesh import connect_pack_path, mesh_path
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.http_guard import AUTH_ALLOWLIST
from openmw.openvault.vault.store import KeyVault

_REMOTE = "203.0.113.10"
_SPOOF_HEADERS: tuple[dict[str, str], ...] = (
    {
        "X-Forwarded-For": "127.0.0.1",
        "X-Real-IP": "127.0.0.1",
        "Host": "localhost",
    },
    {
        "X-Forwarded-For": "198.51.100.7",
        "X-Real-IP": "198.51.100.7",
        "Host": "localhost",
    },
)
# Floor, not the walk list: a GET-only walk must fail. Paths stay off the allowlist.
_MUTATING_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("PUT", "/api/local/mesh/config"),
        ("POST", "/api/ship/github/pat"),
        ("PUT", "/api/fallback"),
        ("PUT", "/api/orchestration/selection"),
        ("PUT", "/api/route/strategy"),
        ("PUT", "/api/route/targets"),
        ("POST", "/api/accounts"),
        ("POST", "/api/cloud/shares"),
        ("POST", "/api/cloud/sessions"),
        ("PATCH", "/api/keys/{key_id}"),
        ("DELETE", "/api/keys/{key_id}"),
    }
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OPENVAULT_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("OPENVAULT_DEV_DOCS", raising=False)
    return root


def _app(home: Path) -> FastAPI:
    vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    return create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )


def _client(app: FastAPI, host: str) -> TestClient:
    return TestClient(app, client=(host, 5555))


def _walk_guarded_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every declared (path, method) under /api and /keys. No hand-kept list."""
    found: list[tuple[str, str]] = []
    seen: set[int] = set()

    def walk(routes: object) -> None:
        for route in routes:  # type: ignore[union-attr]
            marker = id(route)
            if marker in seen:
                continue
            seen.add(marker)
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if (
                isinstance(path, str)
                and methods
                and (path.startswith("/api/") or path.startswith("/keys/"))
            ):
                for method in methods:
                    found.append((str(method).upper(), path))
            nested = getattr(route, "original_router", None)
            if nested is not None:
                walk(getattr(nested, "routes", []))
            nested_routes = getattr(route, "routes", None)
            if nested_routes is not None and nested_routes is not routes:
                walk(nested_routes)

    walk(app.routes)
    return sorted(set(found))


def test_allowlist_is_exactly_healthz() -> None:
    assert len(AUTH_ALLOWLIST) == 1
    assert frozenset({"/api/healthz"}) == AUTH_ALLOWLIST


def test_route_walk_unauthenticated_remote_is_refused(home: Path) -> None:
    app = _app(home)
    remote = _client(app, _REMOTE)
    guarded = _walk_guarded_routes(app)
    assert guarded, "expected /api and /keys routes on the app"
    methods_seen = {method for method, _path in guarded}
    assert methods_seen >= {"GET", "POST", "PUT", "PATCH", "DELETE"}
    assert set(guarded) >= _MUTATING_PAIRS
    for method, path in guarded:
        if path in AUTH_ALLOWLIST:
            continue
        response = remote.request(method, path)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"
        if method == "HEAD":
            continue
        body = response.json()
        assert "error" in body
        assert body["error"]["message"] == "unauthorized"


def test_healthz_allowlist_answers_unauthenticated_remote(home: Path) -> None:
    remote = _client(_app(home), _REMOTE)
    response = remote.get("/api/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("flag", [None, "0", "false", "1", "true"])
def test_require_api_key_does_not_open_remote(
    home: Path, monkeypatch: pytest.MonkeyPatch, flag: str | None
) -> None:
    if flag is None:
        monkeypatch.delenv("OPENVAULT_REQUIRE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENVAULT_REQUIRE_API_KEY", flag)
    remote = _client(_app(home), _REMOTE)
    response = remote.get("/api/providers/catalog")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "unauthorized"


@pytest.mark.parametrize("headers", _SPOOF_HEADERS)
def test_spoofed_forwarded_headers_do_not_admit_remote(home: Path, headers: dict[str, str]) -> None:
    remote = _client(_app(home), _REMOTE)
    response = remote.get("/api/providers/catalog", headers=headers)
    assert response.status_code in (401, 403)
    assert response.json()["error"]["message"] == "unauthorized"


def test_bad_credential_from_remote_is_403(home: Path) -> None:
    remote = _client(_app(home), _REMOTE)
    response = remote.get(
        "/api/providers/catalog",
        headers={"Authorization": "Bearer ov_not-a-real-key"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["message"] == "unauthorized"


def test_valid_key_from_remote_reaches_a_guarded_route(home: Path) -> None:
    app = _app(home)
    loop = _client(app, "127.0.0.1")
    _key_id, headers = issue_key(loop)
    remote = _client(app, _REMOTE)
    response = remote.get("/api/providers/catalog", headers=headers)
    assert response.status_code == 200
    assert "providers" in response.json()


def test_x_api_key_header_is_accepted_from_remote(home: Path) -> None:
    app = _app(home)
    loop = _client(app, "127.0.0.1")
    issued = loop.post("/api/apikeys", json={"label": "x-api-key", "tier": "free"})
    assert issued.status_code == 200
    token = issued.json()["token"]
    remote = _client(app, _REMOTE)
    response = remote.get("/api/providers/catalog", headers={"X-API-Key": token})
    assert response.status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_peer_still_works_without_a_key(home: Path, host: str) -> None:
    client = _client(_app(home), host)
    response = client.get("/api/providers/catalog")
    assert response.status_code == 200
    assert "providers" in response.json()


def test_docs_are_404_when_the_dev_flag_is_off(home: Path) -> None:
    client = _client(_app(home), "127.0.0.1")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_docs_are_200_when_the_dev_flag_is_on(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVAULT_DEV_DOCS", "1")
    client = _client(_app(home), "127.0.0.1")
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/healthz" in schema.json()["paths"]


def test_get_mesh_and_connect_pack_write_nothing(home: Path) -> None:
    app = _app(home)
    with TestClient(app, client=("127.0.0.1", 5555)) as client:
        mesh = mesh_path()
        pack = connect_pack_path()
        mesh_before = mesh.read_bytes() if mesh.is_file() else None
        pack_before = pack.read_bytes() if pack.is_file() else None
        assert client.get("/api/local/mesh").status_code == 405
        assert client.get("/api/local/connect-pack").status_code == 405
        if mesh_before is None:
            assert not mesh.is_file()
        else:
            assert mesh.read_bytes() == mesh_before
        if pack_before is None:
            assert not pack.is_file()
        else:
            assert pack.read_bytes() == pack_before


def test_console_launch_disables_proxy_headers(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []

    def fake_run(*_args: object, **kwargs: object) -> None:
        seen.append(kwargs)

    import uvicorn
    from typer.testing import CliRunner

    from openmw.cli import app as cli_app
    from openmw.openvault.app import run_console

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(
        cli_app,
        [
            "console",
            "--host",
            "127.0.0.1",
            "--port",
            "9",
            "--no-open-browser",
            "--mock-health",
            "--cortex-url",
            "http://127.0.0.1:9",
        ],
    )
    assert result.exit_code == 0, result.output
    run_console(host="127.0.0.1", port=9, cortex_url="http://127.0.0.1:9", mock_health=True)
    assert len(seen) == 2
    for kwargs in seen:
        assert kwargs.get("proxy_headers") is False

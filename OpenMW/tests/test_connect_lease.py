"""Single-kid lease against a real app. HttpGuard stays on. Refs #160.

Admin is not injected by the test client. A 401 here is the guard, not a stub.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import inject_admin_credential
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from openmw.openvault.app import create_app
from openmw.openvault.paths import keys_db_path
from openmw.openvault.vault.admin_token import admin_headers
from openmw.openvault.vault.http_guard import (
    LEASE_TLS_VERIFY,
    HttpGuardMiddleware,
    lease_post,
    lease_transport_allowed,
)
from openmw.openvault.vault.lease import (
    ERR_EXPIRED,
    ERR_NOT_OWNED,
    ERR_NOT_TENANT,
    ERR_RAW,
    ERR_REF_IN_URL,
    ERR_REF_INVALID,
    ERR_REUSED,
    ERR_UNKNOWN,
    LEASE_MINT_PATH,
    LEASE_REDEEM_PATH,
    OWNER_ASSIGN_PATH,
    REF_PREFIX,
    classify_ref,
    grant_is_complete,
    is_raw_secret_ref,
)
from openmw.openvault.vault.trust import TrustStore

INTENT = {"X-OpenVault-Reveal": "intentional"}
SECRET = "connector-secret-value"
_REMOTE = "203.0.113.10"


@pytest.fixture(autouse=True)
def _do_not_inject_admin() -> Any:
    token = inject_admin_credential.set(False)
    yield
    inject_admin_credential.reset(token)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.delenv("OPENVAULT_ADMIN_TOKEN_PATH", raising=False)
    monkeypatch.delenv("OPENVAULT_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("OPENVAULT_SERVICES_ALLOW", raising=False)
    return root


@pytest.fixture()
def app(home: Path) -> FastAPI:
    return create_app(
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )


def _client(app: FastAPI, host: str = "127.0.0.1") -> TestClient:
    return TestClient(app, client=(host, 5555))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, service_id: str) -> str:
    response = client.post(
        "/keys/services",
        json={"service_id": service_id},
        headers=INTENT,
    )
    assert response.status_code == 200, response.status_code
    token = response.json()["token"]
    assert isinstance(token, str) and token
    return token


def _make_key(client: TestClient, *, custody: str, secret: str = SECRET) -> str:
    response = client.post(
        "/api/keys",
        json={
            "label": "connector",
            "provider": "custom",
            "secret": secret,
            "custody": custody,
        },
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text
    kid = response.json()["id"]
    assert isinstance(kid, str) and kid
    return kid


def _assign(client: TestClient, kid: str, service_id: str) -> None:
    response = client.post(
        OWNER_ASSIGN_PATH,
        json={"kid": kid, "service_id": service_id},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["owner_service_id"] == service_id


def _mint(
    client: TestClient,
    token: str,
    kid: str,
    *,
    space: str = "sales",
    extra: dict[str, object] | None = None,
) -> dict[str, Any]:
    body: dict[str, object] = {"kid": kid, "space": space, "ttl_s": 60}
    if extra:
        body.update(extra)
    response = client.post(LEASE_MINT_PATH, json=body, headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


def _code(response: Any) -> str:
    body = response.json()
    return str(body["error"]["type"])


def _blobs(*parts: object) -> str:
    return "\n".join(str(part) for part in parts)


def _assert_absent(needle: str, blob: str) -> None:
    if needle and needle in blob:
        raise AssertionError("secret material appeared in output")


class _Grab(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _guard_names(app: FastAPI) -> list[str]:
    return [str(layer.cls.__name__) for layer in app.user_middleware]


def test_guard_is_mounted_and_paths_are_exact() -> None:
    assert LEASE_TLS_VERIFY is True
    assert lease_transport_allowed("127.0.0.1", "http") is True
    assert lease_transport_allowed("::1", "http") is True
    assert lease_transport_allowed(_REMOTE, "http") is False
    assert lease_transport_allowed(_REMOTE, "https") is True
    assert lease_transport_allowed("", "https") is False
    assert lease_post("POST", LEASE_MINT_PATH) == "mint"
    assert lease_post("POST", LEASE_REDEEM_PATH) == "redeem"
    assert lease_post("GET", LEASE_MINT_PATH) == ""
    assert lease_post("PUT", LEASE_MINT_PATH) == ""
    assert lease_post("DELETE", LEASE_REDEEM_PATH) == ""
    assert lease_post("POST", LEASE_MINT_PATH + "/") == ""
    assert lease_post("POST", LEASE_REDEEM_PATH + "/x") == ""
    assert "{" not in LEASE_MINT_PATH
    assert "{" not in LEASE_REDEEM_PATH
    assert grant_is_complete("kid", 60, "dms") is True
    assert grant_is_complete("", 60, "dms") is False
    assert grant_is_complete("kid", 0, "dms") is False
    assert grant_is_complete("kid", 60, "") is False
    assert is_raw_secret_ref("AKIAIOSFODNN7EXAMPLE") is True
    assert is_raw_secret_ref("ghp_" + ("a" * 36)) is True
    assert is_raw_secret_ref("sk-ant-api03-not-real") is True
    assert is_raw_secret_ref("xoxb-1234567890") is True
    assert is_raw_secret_ref("s3cret-pass-value-long") is True
    assert is_raw_secret_ref("nope") is False
    assert classify_ref("nope") == "invalid"
    text = Path(classify_ref.__code__.co_filename).read_text(encoding="utf-8")
    # The lease module itself. co_filename is lease.py for classify_ref.
    assert "os.environ" not in text
    assert "getenv" not in text
    assert "verify=False" not in text


def test_real_guard_rejects_admin_header_alone(app: FastAPI) -> None:
    assert "HttpGuardMiddleware" in _guard_names(app)
    assert HttpGuardMiddleware.__name__ == "HttpGuardMiddleware"
    client = _client(app)
    dms = _register(client, "dms")
    kid = _make_key(client, custody="tenant")
    _assign(client, kid, "dms")
    alone = client.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales"},
        headers=admin_headers(),
    )
    assert alone.status_code == 401
    assert _code(alone) == "openvault_unauthenticated"
    assert REF_PREFIX not in alone.text
    assert SECRET not in alone.text
    redeem = client.post(
        LEASE_REDEEM_PATH,
        json={"ref": REF_PREFIX + ("a" * 43)},
        headers=admin_headers(),
    )
    assert redeem.status_code == 401
    assert _code(redeem) == "openvault_unauthenticated"
    both_wrong = client.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales"},
        headers={**admin_headers(), **_bearer("not-a-service-token")},
    )
    assert both_wrong.status_code == 401
    assert _code(both_wrong) == "openvault_unauthenticated"
    # Bearer alone opens the path. Admin is not required.
    opened = client.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales"},
        headers=_bearer(dms),
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["owner_service_id"] == "dms"
    assert opened.json()["tenant_key"] == kid
    assert opened.json()["ttl_s"] == 60


def test_non_owned_kid_is_named_refuse(app: FastAPI) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    other = _register(client, "other")
    kid = _make_key(client, custody="tenant")
    _assign(client, kid, "dms")
    before = client.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales"},
        headers=_bearer(dms),
    )
    # Owned path works; the spoofed service_id in the body is not the owner.
    spoofed = _mint(client, dms, kid, extra={"service_id": "other"})
    assert spoofed["owner_service_id"] == "dms"
    refused = client.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales", "service_id": "dms"},
        headers=_bearer(other),
    )
    assert refused.status_code == 403
    assert _code(refused) == ERR_NOT_OWNED
    _assert_absent(SECRET, refused.text)
    missing = client.post(
        LEASE_MINT_PATH,
        json={"kid": "no-such-kid", "space": "sales"},
        headers=_bearer(dms),
    )
    assert missing.status_code == 403
    assert _code(missing) == ERR_NOT_OWNED
    assert before.status_code == 200


def test_expired_and_reused_leases_refuse(app: FastAPI, home: Path) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    kid = _make_key(client, custody="tenant")
    _assign(client, kid, "dms")
    first = _mint(client, dms, kid, space="one")
    ref = str(first["ref"])
    assert ref.startswith(REF_PREFIX)
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    db = sqlite3.connect(str(keys_db_path()))
    try:
        db.execute(
            "UPDATE key_leases SET expires_at = ? WHERE ref_sha256 = ?",
            (int(time.time()) - 30, digest),
        )
        db.commit()
    finally:
        db.close()
    expired = client.post(LEASE_REDEEM_PATH, json={"ref": ref}, headers=_bearer(dms))
    assert expired.status_code == 403
    assert _code(expired) == ERR_EXPIRED
    _assert_absent(SECRET, expired.text)
    _assert_absent(ref, expired.text)

    live = _mint(client, dms, kid, space="two")
    live_ref = str(live["ref"])
    handler = _Grab()
    logging.getLogger().addHandler(handler)
    try:
        with capture_logs() as logs:
            ok = client.post(LEASE_REDEEM_PATH, json={"ref": live_ref}, headers=_bearer(dms))
            again = client.post(LEASE_REDEEM_PATH, json={"ref": live_ref}, headers=_bearer(dms))
    finally:
        logging.getLogger().removeHandler(handler)
    assert ok.status_code == 200, ok.text
    assert ok.json()["secret"] == SECRET
    assert ok.json()["tenant_key"] == kid
    assert ok.json()["ttl_s"] == 60
    assert ok.json()["owner_service_id"] == "dms"
    assert ok.json()["space"] == "two"
    assert again.status_code == 403
    assert _code(again) == ERR_REUSED
    _assert_absent(SECRET, again.text)
    blob = _blobs(logs, handler.lines, (home / "secret_audit.jsonl").read_text(encoding="utf-8"))
    _assert_absent(SECRET, blob)
    _assert_absent(live_ref, blob)
    raw_db = keys_db_path().read_bytes()
    assert live_ref.encode("utf-8") not in raw_db
    assert ref.encode("utf-8") not in raw_db


def test_raw_secret_shaped_refs_refuse(app: FastAPI) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    headers = _bearer(dms)
    shapes = [
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_" + ("b" * 36),
        "sk-ant-api03-not-a-real-key",
        "xoxb-999999-not-real",
        "s3cret-pass-value-long",
    ]
    handler = _Grab()
    logging.getLogger().addHandler(handler)
    try:
        with capture_logs() as logs:
            for shape in shapes:
                response = client.post(LEASE_REDEEM_PATH, json={"ref": shape}, headers=headers)
                assert response.status_code == 400, shape
                assert _code(response) == ERR_RAW
                _assert_absent(shape, response.text)
                _assert_absent(shape, _blobs(logs, handler.lines))
    finally:
        logging.getLogger().removeHandler(handler)
    short = client.post(LEASE_REDEEM_PATH, json={"ref": "nope"}, headers=headers)
    assert short.status_code == 400
    assert _code(short) == ERR_REF_INVALID
    unknown = REF_PREFIX + ("c" * 43)
    missing = client.post(LEASE_REDEEM_PATH, json={"ref": unknown}, headers=headers)
    assert missing.status_code == 404
    assert _code(missing) == ERR_UNKNOWN
    _assert_absent(unknown, missing.text)


def test_ref_in_the_url_is_not_accepted(app: FastAPI) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    kid = _make_key(client, custody="tenant")
    _assign(client, kid, "dms")
    minted = _mint(client, dms, kid)
    ref = str(minted["ref"])
    headers = _bearer(dms)
    in_path = client.post(f"{LEASE_REDEEM_PATH}/{ref}", json={"ref": ref}, headers=headers)
    assert in_path.status_code == 401
    _assert_absent(ref, in_path.text)
    _assert_absent(SECRET, in_path.text)
    with_admin = client.post(
        f"{LEASE_REDEEM_PATH}/{ref}",
        json={"ref": ref},
        headers={**headers, **admin_headers()},
    )
    assert with_admin.status_code != 200
    _assert_absent(SECRET, with_admin.text)
    _assert_absent(ref, with_admin.text)
    queried = client.post(
        f"{LEASE_REDEEM_PATH}?ref={ref}",
        json={"ref": ref},
        headers=headers,
    )
    assert queried.status_code == 400
    assert _code(queried) == ERR_REF_IN_URL
    _assert_absent(ref, queried.text)
    _assert_absent(SECRET, queried.text)
    # The URL attempts did not consume the lease.
    ok = client.post(LEASE_REDEEM_PATH, json={"ref": ref}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["secret"] == SECRET
    reveal = client.get(
        f"/api/keys/{kid}/secret",
        headers={**headers, **INTENT},
    )
    assert reveal.status_code == 401
    _assert_absent(SECRET, reveal.text)


def test_other_methods_stay_on_the_admin_gate(app: FastAPI) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    headers = _bearer(dms)
    for method in ("GET", "PUT", "DELETE", "PATCH"):
        response = client.request(method, LEASE_MINT_PATH, headers=headers)
        assert response.status_code == 401, method
        assert _code(response) == "openvault_unauthenticated"
    slash = client.post(LEASE_MINT_PATH + "/", json={"kid": "x", "space": "y"}, headers=headers)
    assert slash.status_code == 401
    owner = client.post(
        OWNER_ASSIGN_PATH,
        json={"kid": "x", "service_id": "dms"},
        headers=headers,
    )
    assert owner.status_code == 401
    listed = client.get("/api/keys", headers=admin_headers())
    assert listed.status_code == 200
    assert listed.json()["keys"] == []


def test_pooled_key_and_grant_row(app: FastAPI) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    pooled = _make_key(client, custody="pooled", secret="pooled-secret-value-xx")
    _assign(client, pooled, "dms")
    refused = client.post(
        LEASE_MINT_PATH,
        json={"kid": pooled, "space": "sales"},
        headers=_bearer(dms),
    )
    assert refused.status_code == 403
    assert _code(refused) == ERR_NOT_TENANT
    _assert_absent("pooled-secret-value-xx", refused.text)
    kid = _make_key(client, custody="tenant")
    bare = client.get("/api/keys", headers=admin_headers()).json()["keys"]
    row = next(item for item in bare if item["id"] == kid)
    assert row["owner_service_id"] is None
    _assign(client, kid, "dms")
    minted = _mint(client, dms, kid, space="warehouse")
    connection = sqlite3.connect(str(keys_db_path()))
    try:
        stored = connection.execute(
            "SELECT tenant_key, ttl_s, owner_service_id, space_id FROM key_leases"
        ).fetchone()
        assert stored is not None
        assert stored[0] == kid
        assert stored[1] == 60
        assert stored[2] == "dms"
        assert stored[3] == "warehouse"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO key_leases (
                  ref_sha256, tenant_key, ttl_s, owner_service_id, space_id,
                  expires_at, created_at
                ) VALUES ('aa', '', 60, 'dms', 's', 9, 9)
                """
            )
    finally:
        connection.close()
    assert minted["tenant_key"] == kid
    assert minted["owner_service_id"] == "dms"
    assert minted["ttl_s"] == 60


def test_plaintext_non_loopback_is_refused(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    local = _client(app)
    dms = _register(local, "dms")
    kid = _make_key(local, custody="tenant")
    _assign(local, kid, "dms")
    remote = _client(app, _REMOTE)
    blocked = remote.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales"},
        headers={**_bearer(dms), "X-Forwarded-Proto": "https"},
    )
    assert blocked.status_code == 403
    assert _code(blocked) == "openvault_forbidden"
    _assert_absent(SECRET, blocked.text)
    status, body = _raw(
        app,
        "POST",
        LEASE_MINT_PATH,
        scheme="https",
        host=_REMOTE,
        headers=_bearer(dms),
        payload={"kid": kid, "space": "sales"},
    )
    assert status == 200, body
    assert b"ovlease_" in body
    monkeypatch.setattr(
        "openmw.openvault.vault.http_guard.LEASE_TLS_VERIFY",
        False,
    )
    status, body = _raw(
        app,
        "POST",
        LEASE_MINT_PATH,
        scheme="https",
        host=_REMOTE,
        headers=_bearer(dms),
        payload={"kid": kid, "space": "other"},
    )
    assert status == 403
    assert b"ovlease_" not in body
    # Loopback does not need TLS. The lock only closes the non-loopback path.
    still = local.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "local"},
        headers=_bearer(dms),
    )
    assert still.status_code == 200, still.text


def test_root_path_does_not_skip_the_guard(app: FastAPI) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    kid = _make_key(client, custody="tenant")
    _assign(client, kid, "dms")
    status, body = _raw(
        app,
        "POST",
        "/mounted" + LEASE_MINT_PATH,
        root_path="/mounted",
        headers=_bearer(dms),
        payload={"kid": kid, "space": "mounted"},
    )
    assert status == 200, body
    status, body = _raw(
        app,
        "GET",
        "/mounted" + LEASE_MINT_PATH,
        root_path="/mounted",
        headers=_bearer(dms),
    )
    assert status == 401
    status, body = _raw(
        app,
        "POST",
        "/mounted" + LEASE_MINT_PATH + "/",
        root_path="/mounted",
        headers=_bearer(dms),
        payload={"kid": kid, "space": "mounted"},
    )
    assert status == 401
    assert b"ovlease_" not in body


def test_revoked_bearer_and_rotation(app: FastAPI) -> None:
    client = _client(app)
    dms = _register(client, "dms")
    kid = _make_key(client, custody="tenant")
    _assign(client, kid, "dms")
    TrustStore(seal=app.state.seal).revoke_service("dms")
    revoked = client.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales"},
        headers=_bearer(dms),
    )
    assert revoked.status_code == 401
    # Re-register rotates the token. The revoked bearer is not enough, so
    # this uses the admin header. The lease path itself still does not.
    rotated_svc = client.post(
        "/keys/services",
        json={"service_id": "dms"},
        headers={**INTENT, **admin_headers()},
    )
    assert rotated_svc.status_code == 200, rotated_svc.text
    fresh = str(rotated_svc.json()["token"])
    assert fresh != dms
    opened = _mint(client, fresh, kid)
    rotated = client.post(
        f"/api/keys/{kid}/rotate",
        json={"new_secret": "rotated-secret-value"},
        headers=admin_headers(),
    )
    assert rotated.status_code == 200, rotated.text
    new_id = rotated.json()["id"]
    stale = client.post(
        LEASE_MINT_PATH,
        json={"kid": kid, "space": "sales"},
        headers=_bearer(fresh),
    )
    assert stale.status_code == 403
    assert _code(stale) == ERR_NOT_OWNED
    newborn = client.post(
        LEASE_MINT_PATH,
        json={"kid": new_id, "space": "sales"},
        headers=_bearer(fresh),
    )
    assert newborn.status_code == 403
    assert _code(newborn) == ERR_NOT_OWNED
    assert opened["ref"].startswith(REF_PREFIX)


def _raw(
    app: FastAPI,
    method: str,
    path: str,
    *,
    scheme: str = "http",
    host: str = "127.0.0.1",
    root_path: str = "",
    headers: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, bytes]:
    sent: list[dict[str, Any]] = []
    raw_body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    sent_body = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if sent_body:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent_body = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    encoded = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    if payload is not None:
        encoded.append((b"content-type", b"application/json"))
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": encoded,
        "client": (host, 5555),
        "server": ("testserver", 80),
        "root_path": root_path,
    }
    asyncio.run(app(scope, receive, send))
    status = next(int(item["status"]) for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return status, body

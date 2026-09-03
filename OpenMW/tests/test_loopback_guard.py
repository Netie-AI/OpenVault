"""Peer-address normalisation for the custody loopback guard.

Regression for a console that could read keys but not create one: Next forwards
the browser's address in ``X-Forwarded-For``, uvicorn trusts that header from
loopback and rewrites ``request.client.host``, and a dual-stack socket spells
localhost ``::ffff:127.0.0.1`` -- which the guard did not recognise. Every
custody mutation from the UI came back "key create is loopback-only".

The guard was right to exist and right to be strict. It was simply reading a
spelling of "this machine" that it had never been taught.

Run: uv run pytest tests/test_loopback_guard.py -q
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openmw.openvault.app import _LOOPBACK_HOSTS, _normalise_host, create_app
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.mark.parametrize(
    "raw",
    [
        "127.0.0.1",
        "::1",
        "localhost",
        # The form that caused the bug: IPv4-mapped IPv6, as forwarded by Node.
        "::ffff:127.0.0.1",
        "::FFFF:127.0.0.1",
        # Bracketed and port-suffixed spellings of the same thing.
        "[::1]",
        "[::ffff:127.0.0.1]",
        "127.0.0.1:5000",
        "  127.0.0.1  ",
        "LOCALHOST",
    ],
)
def test_every_spelling_of_this_machine_is_accepted(raw: str) -> None:
    """R-0005: a control that refuses legitimate work is a failure, not a win."""
    assert _normalise_host(raw) in _LOOPBACK_HOSTS, f"{raw!r} is loopback and must pass"


@pytest.mark.parametrize(
    "raw",
    [
        "1.2.3.4",
        "192.168.1.50",
        "10.0.0.7",
        # An IPv4-mapped *remote* address must not ride in on the same fix.
        "::ffff:1.2.3.4",
        "::ffff:192.168.1.50",
        "[::ffff:8.8.8.8]",
        "2001:4860:4860::8888",
        "evil.example.com",
        # Near-misses that must not be waved through by a sloppy prefix strip.
        "127.0.0.1.evil.com",
        "localhost.evil.com",
        "notlocalhost",
        "",
        "   ",
    ],
)
def test_everything_else_is_still_remote(raw: str) -> None:
    """The point of the guard is that a vault reachable from the LAN is not a vault."""
    assert _normalise_host(raw) not in _LOOPBACK_HOSTS, f"{raw!r} must stay remote"


def _client_from(host: str, tmp_path) -> TestClient:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    app = create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )
    return TestClient(app, client=(host, 5555))


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_console_can_actually_create_a_key(host: str, tmp_path) -> None:
    """The endpoint, not the helper.

    A passing unit test on ``_normalise_host`` would not have caught this: the
    bug was that the guard never called it. This asserts the thing the console
    receives -- a created key -- from every address a local browser presents.
    """
    client = _client_from(host, tmp_path / host.replace(":", "_"))
    res = client.post(
        "/api/keys",
        json={"label": "probe", "provider": "google", "secret": "x" * 24, "role": "free"},
    )
    assert res.status_code == 200, f"{host} -> {res.status_code} {res.text[:160]}"
    assert res.json()["provider"] == "google"


@pytest.mark.parametrize("host", ["1.2.3.4", "::ffff:1.2.3.4", "192.168.1.50"])
def test_the_lan_still_cannot_create_a_key(host: str, tmp_path) -> None:
    """A vault reachable from the LAN is not a vault. The fix must not widen this."""
    client = _client_from(host, tmp_path / host.replace(":", "_").replace(".", "-"))
    res = client.post(
        "/api/keys",
        json={"label": "probe", "provider": "google", "secret": "x" * 24, "role": "free"},
    )
    assert res.status_code == 403, f"{host} -> {res.status_code}"
    assert "loopback-only" in res.text


def test_normalisation_matches_the_typescript_guard() -> None:
    """One rule, two implementations -- the drift between them was the bug.

    Mirrors ``isLoopbackHost`` in apps/web/src/server/authz/routeGuard.ts: strip
    brackets, strip a single trailing port, strip the ``::ffff:`` prefix,
    casefold. If that file changes, this should change with it.
    """
    assert _normalise_host("[::ffff:127.0.0.1]:5000") == "127.0.0.1"
    assert _normalise_host("[2001:db8::1]") == "2001:db8::1"
    # A bare IPv6 address has many colons and no port, so it must survive intact
    # rather than being truncated at the first colon.
    assert _normalise_host("2001:db8::1") == "2001:db8::1"

"""Loopback app grant: other local app asks, human grants, token once.

The pairing-code cases below are the point of the file. Loopback answers "is
this machine", never "is this the process I meant" -- OpenVault's own
docs/SECRETS_CUSTODY.md says any process running as the user reaches
127.0.0.1 -- so the code is the only thing that binds an approval to the app
that asked (KB A-0009). Every negative here is a decision that used to succeed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.vault.app_grants import MAX_CODE_ATTEMPTS
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ovhome"
    monkeypatch.setenv("OPENVAULT_HOME", str(root))
    monkeypatch.setenv("OPENVAULT_KEY", Fernet.generate_key().decode())
    return root


@pytest.fixture()
def api(home: Path) -> FastAPI:
    vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    return create_app(vault=vault, mock_health=True, enable_precheck_loop=False)


@pytest.fixture()
def client(api: FastAPI) -> TestClient:
    return TestClient(api, client=("127.0.0.1", 5555))


def test_grant_flow_delivers_token_once(client: TestClient, home: Path) -> None:
    started = client.post("/api/local/grants", json={"client_name": "MyApp"}).json()
    assert started["status"] == "pending"
    code = started["user_code"]
    assert code
    grant_id = started["grant_id"]
    pending = client.post(f"/api/local/grants/{grant_id}/poll")
    assert pending.status_code == 202
    decided = client.post(
        f"/api/local/grants/{grant_id}/decide", json={"approve": True, "user_code": code}
    )
    assert decided.status_code == 200, decided.text
    first = client.post(f"/api/local/grants/{grant_id}/poll")
    assert first.status_code == 200
    token = first.json()["token"]
    assert token.startswith("ov_")
    second = client.post(f"/api/local/grants/{grant_id}/poll")
    assert second.json()["token"] is None
    disk = (home / "app_grants.json").read_text(encoding="utf-8")
    assert "ov_" not in disk


def test_grant_deny_is_403_on_poll(client: TestClient) -> None:
    started = client.post("/api/local/grants", json={"client_name": "Nope"}).json()
    grant_id = started["grant_id"]
    client.post(
        f"/api/local/grants/{grant_id}/decide",
        json={"approve": False, "user_code": started["user_code"]},
    )
    poll = client.post(f"/api/local/grants/{grant_id}/poll")
    assert poll.status_code == 403


def test_grant_is_loopback_only(home: Path) -> None:
    vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
    app = create_app(vault=vault, mock_health=True, enable_precheck_loop=False)
    remote = TestClient(app, client=("192.168.1.50", 5555))
    res = remote.post("/api/local/grants", json={"client_name": "Lan"})
    assert res.status_code == 401


# --- The pairing code is the boundary (A-0009) ---


def test_decide_without_user_code_is_refused(client: TestClient) -> None:
    """The shape every existing caller had: loopback + approve, nothing else."""
    started = client.post("/api/local/grants", json={"client_name": "Silent"}).json()
    grant_id = started["grant_id"]
    refused = client.post(f"/api/local/grants/{grant_id}/decide", json={"approve": True})
    assert refused.status_code == 403, refused.text
    assert client.post(f"/api/local/grants/{grant_id}/poll").status_code == 202


def test_decide_with_wrong_user_code_is_refused(client: TestClient) -> None:
    started = client.post("/api/local/grants", json={"client_name": "Guesser"}).json()
    grant_id = started["grant_id"]
    wrong = "0000" if started["user_code"] != "0000" else "FFFF"
    refused = client.post(
        f"/api/local/grants/{grant_id}/decide", json={"approve": True, "user_code": wrong}
    )
    assert refused.status_code == 403, refused.text
    # Still pending, so a refusal is not a silent denial of the honest app either.
    assert client.post(f"/api/local/grants/{grant_id}/poll").status_code == 202


def test_wrong_code_deny_cannot_cancel_someone_elses_grant(client: TestClient) -> None:
    started = client.post("/api/local/grants", json={"client_name": "Victim"}).json()
    grant_id = started["grant_id"]
    refused = client.post(
        f"/api/local/grants/{grant_id}/decide", json={"approve": False, "user_code": "ZZZZ"}
    )
    assert refused.status_code == 403, refused.text
    assert client.post(f"/api/local/grants/{grant_id}/poll").status_code == 202


def test_second_local_client_cannot_approve_another_apps_grant(api: FastAPI) -> None:
    """Byte-identical requests from the same machine, and only one can approve.

    This is A-0009 stated as a test: both clients clear every gate the endpoint
    had before -- same loopback host, same port, same headers -- and the only
    thing that separates them is the code the first one was handed.
    """
    honest = TestClient(api, client=("127.0.0.1", 5555))
    attacker = TestClient(api, client=("127.0.0.1", 5555))
    started = honest.post("/api/local/grants", json={"client_name": "HonestApp"}).json()
    grant_id = started["grant_id"]

    # The attacker reads every grant endpoint it is allowed to and learns no code.
    listed = attacker.get("/api/local/grants")
    assert listed.status_code == 200
    fetched = attacker.get(f"/api/local/grants/{grant_id}")
    assert fetched.status_code == 200
    assert "user_code" not in listed.text
    assert "user_code" not in fetched.json()

    stolen = attacker.post(f"/api/local/grants/{grant_id}/decide", json={"approve": True})
    assert stolen.status_code == 403, stolen.text
    assert honest.post(f"/api/local/grants/{grant_id}/poll").status_code == 202

    granted = honest.post(
        f"/api/local/grants/{grant_id}/decide",
        json={"approve": True, "user_code": started["user_code"]},
    )
    assert granted.status_code == 200, granted.text
    assert honest.post(f"/api/local/grants/{grant_id}/poll").json()["token"].startswith("ov_")


def test_decide_response_never_echoes_the_code(client: TestClient) -> None:
    started = client.post("/api/local/grants", json={"client_name": "Quiet"}).json()
    grant_id = started["grant_id"]
    decided = client.post(
        f"/api/local/grants/{grant_id}/decide",
        json={"approve": True, "user_code": started["user_code"]},
    )
    assert decided.status_code == 200, decided.text
    assert "user_code" not in decided.json()


def test_repeated_wrong_codes_burn_the_grant(client: TestClient) -> None:
    """Four hex characters is 65536 guesses and loopback has no rate limit."""
    started = client.post("/api/local/grants", json={"client_name": "Brute"}).json()
    grant_id = started["grant_id"]
    for _ in range(MAX_CODE_ATTEMPTS):
        res = client.post(
            f"/api/local/grants/{grant_id}/decide", json={"approve": True, "user_code": "ZZZZ"}
        )
        assert res.status_code == 403, res.text
    assert client.post(f"/api/local/grants/{grant_id}/poll").status_code == 403
    # Even the real code cannot revive it -- the grant is spent, not just wrong.
    after = client.post(
        f"/api/local/grants/{grant_id}/decide",
        json={"approve": True, "user_code": started["user_code"]},
    )
    assert after.status_code == 400, after.text


def test_code_is_accepted_lowercase_and_padded(client: TestClient) -> None:
    """A control that refuses legitimate work is a failure (KB R-0005).

    The human retypes the code off another screen; case and a stray space are
    typing, not an attack.
    """
    started = client.post("/api/local/grants", json={"client_name": "Typist"}).json()
    grant_id = started["grant_id"]
    typed = f"  {started['user_code'].lower()} "
    decided = client.post(
        f"/api/local/grants/{grant_id}/decide", json={"approve": True, "user_code": typed}
    )
    assert decided.status_code == 200, decided.text
    assert client.post(f"/api/local/grants/{grant_id}/poll").json()["token"].startswith("ov_")


def test_non_ascii_code_is_refused_not_crashed(client: TestClient) -> None:
    """compare_digest raises on non-ASCII str; a 500 here would be a free DoS."""
    started = client.post("/api/local/grants", json={"client_name": "Unicode"}).json()
    grant_id = started["grant_id"]
    refused = client.post(
        f"/api/local/grants/{grant_id}/decide", json={"approve": True, "user_code": "éé"}
    )
    assert refused.status_code == 403, refused.text


# --- The label rule is checked when the grant is asked for, not when it is granted ---
#
# ``api_keys.issue`` validates the label only at Grant time. A grant whose label
# fails that rule used to be accepted, shown to the human as pending, and then
# refused on the Grant click -- a request that could never be approved. "DMS
# grade52 armed (local)" did exactly that: the parentheses are outside the rule.

LABEL_RULE = "label may only contain letters, digits and . : @ / - _ space"


def _pending_ids(client: TestClient) -> set[str]:
    listed = client.get("/api/local/grants")
    assert listed.status_code == 200, listed.text
    return {row["grant_id"] for row in listed.json()["grants"]}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"client_name": "DMS", "label": "DMS grade52 armed (local)"}, id="parens"),
        pytest.param({"client_name": "DMS", "label": "dms\nnewline"}, id="newline"),
        pytest.param({"client_name": "DMS (local)"}, id="empty-label-bad-client-name"),
        pytest.param({"client_name": "DMS (local)", "label": "   "}, id="blank-label-bad-name"),
    ],
)
def test_grant_with_label_outside_the_rule_is_refused_at_start(
    client: TestClient, body: dict[str, str]
) -> None:
    before = _pending_ids(client)
    res = client.post("/api/local/grants", json=body)
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == LABEL_RULE
    assert "user_code" not in res.text
    assert _pending_ids(client) == before


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"client_name": "DMS", "label": "a" * 81}, id="label-81"),
        pytest.param({"client_name": "b" * 81}, id="empty-label-client-name-81"),
    ],
)
def test_grant_with_label_over_80_is_refused_not_truncated(
    client: TestClient, body: dict[str, str]
) -> None:
    before = _pending_ids(client)
    res = client.post("/api/local/grants", json=body)
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == "label must be at most 80 characters"
    assert _pending_ids(client) == before


@pytest.mark.parametrize(
    ("body", "expected_label"),
    [
        pytest.param(
            {"client_name": "DMS", "label": "DMS grade52 armed local:v1@host/x-y_z"},
            "DMS grade52 armed local:v1@host/x-y_z",
            id="every-allowed-char",
        ),
        pytest.param({"client_name": "DMS", "label": "c" * 80}, "c" * 80, id="label-80"),
        pytest.param({"client_name": "MyApp"}, "MyApp", id="empty-label-uses-client-name"),
    ],
)
def test_valid_label_is_issued_exactly(
    client: TestClient, body: dict[str, str], expected_label: str
) -> None:
    started = client.post("/api/local/grants", json=body)
    assert started.status_code == 200, started.text
    grant_id = started.json()["grant_id"]
    assert started.json()["label"] == expected_label
    assert grant_id in _pending_ids(client)
    decided = client.post(
        f"/api/local/grants/{grant_id}/decide",
        json={"approve": True, "user_code": started.json()["user_code"]},
    )
    assert decided.status_code == 200, decided.text
    polled = client.post(f"/api/local/grants/{grant_id}/poll").json()
    assert polled["token"].startswith("ov_")
    keys = client.get("/api/apikeys").json()["keys"]
    issued = [k for k in keys if k["key_id"] == polled["api_key_id"]]
    assert len(issued) == 1
    assert issued[0]["label"] == expected_label

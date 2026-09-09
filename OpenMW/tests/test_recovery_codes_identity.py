"""Backup codes and identity documents: the two custody kinds added for the
founder's "give me a place to put them".

Companion to ``test_secrets_custody.py``, which pins loopback + intent + audit
on passwords and cards. Two things are specific to these kinds and are what
most of this file is about:

* **A backup code is single-use.** Handing one out without spending it is how
  the same code goes into two prompts, the second is rejected, and the user
  concludes their codes are broken. So there is no reveal for a code set at
  all - only a consume that marks and returns inside one transaction.
* **A code set is one sealed payload.** That payload is EVERY code, so the
  ordinary reveal route must refuse it rather than return the lot.

The pasting tests are not politeness. Providers print codes as ``1234 5678``,
and a splitter that treats a space as a separator turns every code into two
codes that will never work while doubling the count of logins the user believes
they have - discovered only while locked out.

Codes below are invented; identity numbers are format-shaped, not real.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openmw.openvault.app import create_app

REVEAL_HEADER = {"X-OpenVault-Reveal": "intentional"}


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test in this file gets its own vault. Autouse, not opt-in.

    ``create_app()`` with no ``OPENVAULT_HOME`` falls back to ``~/.openvault``,
    which on a developer machine is the REAL vault holding real passwords. A
    suite that forgets the override writes fake records in among them, and the
    fake ones are indistinguishable from real ones at a glance - a "Gmail backup
    codes" row a user might later trust.

    It also makes assertions honest. ``/api/secrets`` orders by ``kind ASC``, so
    ``secrets[0]`` on a shared database is whichever kind sorts first across
    everything the machine has ever stored, not the record the test just made.
    Two tests here failed exactly that way before this fixture existed.
    """
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    return tmp_path


CODES = ["abcd-efgh", "ijkl-mnop", "qrst-uvwx"]
IC_NUMBER = "900101-01-1234"
PASSPORT = "A12345678"


def _client(host: str = "127.0.0.1") -> TestClient:
    return TestClient(create_app(mock_health=True), client=(host, 5555))


def _make_codes(client: TestClient, codes=None, label: str = "Gmail backup codes") -> dict:
    res = client.post(
        "/api/secrets/recovery-codes",
        json={
            "label": label,
            "codes": codes if codes is not None else CODES,
            "username": "me@gmail.com",
            "url": "https://accounts.google.com",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _make_identity(client: TestClient, number: str = IC_NUMBER, doc_type: str = "nric") -> dict:
    res = client.post(
        "/api/secrets/identity",
        json={"label": "My IC", "doc_type": doc_type, "number": number},
    )
    assert res.status_code == 200, res.text
    return res.json()


# --- what a code set looks like from outside ------------------------------


def test_creating_a_code_set_returns_counts_and_never_a_code() -> None:
    client = _client()
    record = _make_codes(client)
    assert record["kind"] == "recovery_codes"
    assert record["codes_total"] == 3
    assert record["codes_unused"] == 3
    assert record["masked"] == "3 of 3 unused"
    assert "abcd-efgh" not in json.dumps(record)


def test_listing_never_decrypts_and_never_shows_a_code() -> None:
    client = _client()
    _make_codes(client)
    body = client.get("/api/secrets").json()
    assert body["secrets"][0]["masked"] == "3 of 3 unused"
    for code in CODES:
        assert code not in json.dumps(body)


def test_codes_are_not_in_the_database_file_in_the_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_codes(client)
    blob = (tmp_path / "keys.db").read_bytes()
    for code in CODES:
        assert code.encode() not in blob


# --- single use is the whole point ----------------------------------------


def test_consuming_spends_one_code_and_decrements_the_count() -> None:
    client = _client()
    record = _make_codes(client)

    res = client.post(f"/api/secrets/{record['id']}/consume-code", headers=REVEAL_HEADER)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] in CODES
    assert body["remaining"] == 2

    listed = client.get("/api/secrets").json()["secrets"][0]
    assert listed["codes_unused"] == 2
    assert listed["codes_total"] == 3
    assert listed["masked"] == "2 of 3 unused"


def test_the_same_code_is_never_handed_out_twice() -> None:
    client = _client()
    record = _make_codes(client)
    seen = set()
    for expected_remaining in (2, 1, 0):
        res = client.post(f"/api/secrets/{record['id']}/consume-code", headers=REVEAL_HEADER)
        assert res.status_code == 200
        body = res.json()
        assert body["code"] not in seen, "a spent code was issued again"
        seen.add(body["code"])
        assert body["remaining"] == expected_remaining
    assert seen == set(CODES)


def test_an_exhausted_set_says_so_rather_than_404ing() -> None:
    """ "Not found" would read as "your codes are gone"; they are used up."""
    client = _client()
    record = _make_codes(client, codes=["only-one"])
    client.post(f"/api/secrets/{record['id']}/consume-code", headers=REVEAL_HEADER)

    res = client.post(f"/api/secrets/{record['id']}/consume-code", headers=REVEAL_HEADER)
    assert res.status_code == 409
    assert "generate a new set" in res.json()["detail"]


def test_reveal_refuses_a_code_set_and_names_the_right_route() -> None:
    # One payload here is every code. Reading them without spending one
    # desynchronises the count, so the wholesale path must not exist.
    client = _client()
    record = _make_codes(client)
    res = client.get(f"/api/secrets/{record['id']}/reveal", headers=REVEAL_HEADER)
    assert res.status_code == 409
    assert "consume_recovery_code" in res.json()["detail"]
    for code in CODES:
        assert code not in res.text


def test_consuming_something_that_is_not_a_code_set_is_refused() -> None:
    client = _client()
    pw = client.post("/api/secrets/passwords", json={"label": "Mail", "password": "hunter2"}).json()
    res = client.post(f"/api/secrets/{pw['id']}/consume-code", headers=REVEAL_HEADER)
    assert res.status_code == 409
    assert "not recovery codes" in res.json()["detail"]


# --- how humans actually paste --------------------------------------------


def test_a_code_containing_a_space_survives_pasting() -> None:
    """The regression this splitter exists for.

    Providers print eight-digit codes as "1234 5678". Splitting on whitespace
    yields six unusable codes and a count of six logins that do not exist.
    """
    client = _client()
    record = _make_codes(client, codes="1234 5678\n8765 4321\n1111 2222")
    assert record["codes_total"] == 3, "a space inside a code is not a separator"

    got = {
        client.post(
            f"/api/secrets/{record['id']}/consume-code",
            headers=REVEAL_HEADER,
        ).json()["code"]
        for _ in range(3)
    }
    assert got == {"1234 5678", "8765 4321", "1111 2222"}


def test_a_numbered_list_loses_its_numbering_but_not_its_codes() -> None:
    client = _client()
    record = _make_codes(client, codes="1. abcd-efgh\n2) ijkl-mnop\n- qrst-uvwx")
    assert record["codes_total"] == 3
    code = client.post(f"/api/secrets/{record['id']}/consume-code", headers=REVEAL_HEADER).json()[
        "code"
    ]
    assert code == "abcd-efgh", "the list marker must not become part of the code"


def test_a_duplicated_paste_does_not_overstate_how_many_logins_are_left() -> None:
    client = _client()
    record = _make_codes(client, codes="aaaa\nbbbb\naaaa")
    assert record["codes_total"] == 2


def test_an_empty_paste_is_refused_with_a_reason() -> None:
    client = _client()
    res = client.post("/api/secrets/recovery-codes", json={"label": "Empty", "codes": "  \n \n"})
    assert res.status_code == 400
    assert "no recovery codes" in res.json()["detail"]


# --- identity documents ----------------------------------------------------


def test_an_identity_number_is_masked_to_the_last_four() -> None:
    client = _client()
    record = _make_identity(client)
    assert record["kind"] == "identity"
    assert record["doc_type"] == "nric"
    assert record["masked"].endswith("1234")
    assert IC_NUMBER not in json.dumps(record)


def test_an_identity_number_is_sealed_on_disk_and_absent_from_listing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_identity(client, PASSPORT, "passport")
    assert PASSPORT.encode() not in (tmp_path / "keys.db").read_bytes()
    assert PASSPORT not in json.dumps(client.get("/api/secrets").json())


def test_an_identity_number_reveals_exactly_as_stored() -> None:
    """Punctuation is preserved: an IC is typed into forms as it is printed."""
    client = _client()
    record = _make_identity(client)
    res = client.get(f"/api/secrets/{record['id']}/reveal", headers=REVEAL_HEADER)
    assert res.status_code == 200
    assert res.json()["secret"] == IC_NUMBER


def test_a_malformed_identity_number_is_refused_without_echoing_it() -> None:
    client = _client()
    res = client.post(
        "/api/secrets/identity",
        json={"label": "Bad", "doc_type": "passport", "number": "A1<script>2345"},
    )
    assert res.status_code == 400
    assert "A1<script>2345" not in res.text


def test_an_unknown_document_type_is_refused_by_the_schema() -> None:
    client = _client()
    res = client.post(
        "/api/secrets/identity",
        json={"label": "X", "doc_type": "blood_type", "number": "A12345678"},
    )
    assert res.status_code == 422


# --- the same three controls as every other custody route ------------------


def test_the_new_write_routes_are_loopback_only() -> None:
    remote = _client(host="10.0.0.7")
    assert (
        remote.post("/api/secrets/recovery-codes", json={"label": "X", "codes": CODES}).status_code
        == 403
    )
    assert (
        remote.post(
            "/api/secrets/identity", json={"label": "X", "doc_type": "nric", "number": IC_NUMBER}
        ).status_code
        == 403
    )


def test_consuming_a_code_needs_explicit_intent() -> None:
    # 428 Precondition Required, matching /api/secrets/{id}/reveal: the caller
    # is not forbidden, it just has not stated intent.
    client = _client()
    record = _make_codes(client)
    assert client.post(f"/api/secrets/{record['id']}/consume-code").status_code == 428
    after = client.get("/api/secrets").json()["secrets"][0]
    assert after["codes_unused"] == 3, "a refused consume must not spend a code"


def test_consume_is_audited_by_count_and_never_by_code(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    record = _make_codes(client)

    used = client.post(f"/api/secrets/{record['id']}/consume-code", headers=REVEAL_HEADER).json()[
        "code"
    ]

    text = (tmp_path / "secret_audit.jsonl").read_text(encoding="utf-8")
    entry = json.loads(text.strip().splitlines()[-1])
    assert entry["event"] == "recovery_code_consume"
    assert entry["remaining"] == 2
    assert entry["client"] == "127.0.0.1"
    assert used not in text, "the audit must not become a second place the code lives"


def test_identity_creation_is_audited_by_doc_type_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    client = _client()
    _make_identity(client)
    text = (tmp_path / "secret_audit.jsonl").read_text(encoding="utf-8")
    entry = json.loads(text.strip().splitlines()[-1])
    assert entry["event"] == "identity_create"
    assert entry["doc_type"] == "nric"
    assert IC_NUMBER not in text


# --- the schema migration ---------------------------------------------------


def test_a_database_predating_these_kinds_gains_the_columns(tmp_path, monkeypatch) -> None:
    """Existing installs have the table already, so CREATE TABLE IF NOT EXISTS
    is a no-op for them and the new columns would simply never appear."""
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path))
    db = tmp_path / "keys.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE secrets (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
              secret_blob BLOB NOT NULL, masked TEXT NOT NULL DEFAULT '',
              account_id TEXT, lifecycle TEXT NOT NULL DEFAULT 'active',
              replaced_by TEXT, username TEXT NOT NULL DEFAULT '',
              url TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '',
              last4 TEXT NOT NULL DEFAULT '', exp_month INTEGER, exp_year INTEGER,
              cardholder TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
              updated_at REAL NOT NULL, last_revealed_at REAL
            )
            """
        )
        conn.commit()

    client = _client()
    record = _make_codes(client)
    assert record["codes_total"] == 3, "the migration did not add the new columns"

"""CLI `openvault add`: argv/env refusal, 1-token chat, HMAC dedupe.

No network. The provider key in this file is a fixture, not a credential.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import io
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.fernet import Fernet
from structlog.testing import capture_logs

from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.key_add import (
    add_tested_key,
    cli_main,
    key_fingerprint,
    main,
    mask_key_id,
    probe_catalog_chat,
    read_key,
)
from openmw.openvault.vault.providers import get_provider
from openmw.openvault.vault.store import KeyVault

_SECRET = "sk-unit-test-key-9f3c2a7b-do-not-log"
_MARKER = "RESPBODY-9c2e"


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def _client(
    status: int,
    seen: dict[str, Any],
    *,
    echo: str = "",
    before: Any = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if before is not None:
            before()
        seen["calls"] = int(seen.get("calls", 0)) + 1
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        seen["bearer_ok"] = request.headers.get("Authorization") == f"Bearer {_SECRET}"
        return httpx.Response(status, json={"echo": echo, "marker": _MARKER})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _raise_client(exc: Exception) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.Client(transport=httpx.MockTransport(handler))


def _run(
    vault: KeyVault,
    argv: list[str],
    *,
    stdin: str = "",
    environ: dict[str, str] | None = None,
    client: httpx.Client | None = None,
    stdin_stream: io.TextIOBase | None = None,
) -> tuple[int, str, str, list[Any]]:
    stdout, stderr = io.StringIO(), io.StringIO()
    incoming = stdin_stream if stdin_stream is not None else io.StringIO(stdin)
    with capture_logs() as logs:
        code = cli_main(
            argv,
            environ={} if environ is None else environ,
            stdin=incoming,
            stdout=stdout,
            stderr=stderr,
            vault=vault,
            client=client,
        )
    return code, stdout.getvalue(), stderr.getvalue(), list(logs)


def _blob(out: str, err: str, logs: list[Any]) -> str:
    return out + "\n" + err + "\n" + repr(logs)


def _load_cli() -> Any:
    path = Path(__file__).resolve().parents[2] / "apps" / "cli" / "openvault_cli.py"
    spec = importlib.util.spec_from_file_location("openvault_cli_add_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_argv_key_is_rejected(vault: KeyVault) -> None:
    cases = (
        ["groq", _SECRET],
        ["groq", "--key", _SECRET],
        ["groq", f"--secret={_SECRET}"],
        ["--key", _SECRET],
        [_SECRET],
    )
    for argv in cases:
        code, out, err, logs = _run(vault, argv, stdin=_SECRET)
        assert code == 2
        assert vault.list_keys() == []
        blob = _blob(out, err, logs)
        assert _SECRET not in blob
        assert "command line" in err


@pytest.mark.parametrize("env_name", ["OPENVAULT_KEY", "GROQ_API_KEY"])
def test_env_var_key_is_rejected(vault: KeyVault, env_name: str) -> None:
    code, out, err, logs = _run(
        vault,
        ["groq"],
        stdin=_SECRET,
        environ={env_name: _SECRET},
    )
    assert code == 2
    assert vault.list_keys() == []
    blob = _blob(out, err, logs)
    assert _SECRET not in blob
    assert "environment" in err
    assert env_name in err


def test_stdout_stderr_and_logs_never_contain_the_key(vault: KeyVault) -> None:
    seen: dict[str, Any] = {}
    client = _client(200, seen, echo=_SECRET)
    code, out, err, logs = _run(vault, ["groq"], stdin=_SECRET + "\n", client=client)
    assert code == 0, err
    assert seen["calls"] == 1
    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/chat/completions")
    assert not str(seen["url"]).rstrip("/").endswith("/models")
    assert seen["bearer_ok"] is True
    groq = get_provider("groq")
    assert groq is not None
    body = seen["body"]
    assert body["model"] == groq.chat_models[0]
    assert body["max_tokens"] == 1
    assert body["messages"] == [{"role": "user", "content": "hi"}]

    rows = vault.list_keys()
    assert len(rows) == 1
    record = rows[0]
    assert record.label == "Groq"
    assert record.provider == "groq"
    assert record.role == "free"
    assert record.precheck_status == "ok"
    assert vault.get_secret(record.id) == _SECRET
    assert out == f"Groq {mask_key_id(record.id)}\n"
    assert err == ""

    blob = _blob(out, err, logs)
    assert _SECRET not in blob
    assert _MARKER not in blob
    assert "messages" not in repr(logs)

    material = vault.get_or_create_fingerprint_secret()
    stored = vault.get_fingerprint(record.id)
    assert stored == key_fingerprint(_SECRET, material)
    assert stored == hmac.new(material, _SECRET.encode(), hashlib.sha256).hexdigest()
    assert stored != hashlib.sha256(_SECRET.encode()).hexdigest()
    on_disk = vault.db_path.read_bytes()
    wal = Path(str(vault.db_path) + "-wal")
    if wal.is_file():
        on_disk += wal.read_bytes()
    assert _SECRET.encode() not in on_disk


def test_failed_test_call_stores_nothing(vault: KeyVault) -> None:
    seen: dict[str, Any] = {}
    client = _client(401, seen, echo=_SECRET)
    code, out, err, logs = _run(vault, ["groq"], stdin=_SECRET, client=client)
    assert code == 1
    assert vault.list_keys() == []
    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/chat/completions")
    assert "HTTP 401" in err
    blob = _blob(out, err, logs)
    assert _SECRET not in blob
    assert _MARKER not in blob
    assert out == ""


def test_duplicate_is_refused(vault: KeyVault) -> None:
    first_seen: dict[str, Any] = {}
    code, out, err, _logs = _run(
        vault,
        ["groq"],
        stdin=_SECRET,
        client=_client(200, first_seen),
    )
    assert code == 0, err
    existing = vault.list_keys()[0]
    masked = f"{existing.label} {mask_key_id(existing.id)}"
    assert out.strip() == masked

    second_seen: dict[str, Any] = {}
    code2, out2, err2, logs2 = _run(
        vault,
        ["add", "groq"],
        stdin=_SECRET,
        client=_client(200, second_seen, echo=_SECRET),
    )
    assert code2 == 1
    assert second_seen.get("calls", 0) == 0
    assert vault.list_keys() == [existing]
    assert out2.strip() == masked
    assert "duplicate key refused" in err2
    blob = _blob(out2, err2, logs2)
    assert _SECRET not in blob
    assert _MARKER not in blob


def test_unreachable_and_timeout_store_nothing(vault: KeyVault) -> None:
    code, out, err, logs = _run(
        vault,
        ["groq"],
        stdin=_SECRET,
        client=_raise_client(httpx.ConnectError("down")),
    )
    assert code == 1
    assert vault.list_keys() == []
    assert "unreachable" in err
    assert _SECRET not in _blob(out, err, logs)

    code, out, err, logs = _run(
        vault,
        ["groq"],
        stdin=_SECRET,
        client=_raise_client(httpx.ConnectTimeout("slow")),
    )
    assert code == 1
    assert vault.list_keys() == []
    assert "timeout" in err
    assert _SECRET not in _blob(out, err, logs)


def test_unsafe_provider_id_is_not_echoed(vault: KeyVault) -> None:
    seen: dict[str, Any] = {}
    result = add_tested_key(vault, _SECRET, "ignored", client=_client(200, seen))
    assert result.ok is False
    assert result.error == "unknown catalog id"
    assert _SECRET not in result.error
    assert seen.get("calls", 0) == 0
    assert vault.list_keys() == []

    spec = get_provider("fireworks")
    assert spec is not None
    probed = probe_catalog_chat(spec, _SECRET, client=_client(200, seen))
    assert probed.ok is False
    assert probed.error == "no catalog chat model to test"
    blank = replace(spec, base_url="  ", chat_models=("some-model",))
    probed = probe_catalog_chat(blank, _SECRET, client=_client(200, seen))
    assert probed.ok is False
    assert probed.error == "no catalog base url to test"
    assert seen.get("calls", 0) == 0


def test_unknown_provider_and_missing_model_store_nothing(vault: KeyVault) -> None:
    seen: dict[str, Any] = {}
    client = _client(200, seen)
    code, out, err, logs = _run(vault, ["not_a_provider"], stdin=_SECRET, client=client)
    assert code == 1
    assert "unknown catalog id: not_a_provider" in err
    assert _SECRET not in _blob(out, err, logs)
    assert seen.get("calls", 0) == 0

    code, out, err, logs = _run(vault, ["fireworks"], stdin=_SECRET, client=client)
    assert code == 1
    assert vault.list_keys() == []
    assert "no catalog chat model" in err
    assert seen.get("calls", 0) == 0
    assert _SECRET not in _blob(out, err, logs)


def test_empty_key_and_blank_base_store_nothing(vault: KeyVault) -> None:
    code, _out, err, _logs = _run(vault, ["groq"], stdin="  \n")
    assert code == 1
    assert err.strip() == "no key entered"
    assert vault.list_keys() == []

    spec = get_provider("groq")
    assert spec is not None
    blank = replace(spec, base_url="  ")
    with patch("openmw.openvault.vault.key_add.get_provider", return_value=blank):
        result = add_tested_key(vault, "groq", _SECRET, client=_client(200, {}))
    assert result.ok is False
    assert result.error == "no catalog base url to test"
    assert vault.list_keys() == []


def test_odd_role_falls_back_to_backup(vault: KeyVault) -> None:
    spec = get_provider("groq")
    assert spec is not None
    odd = replace(spec, default_role="nope")
    with patch("openmw.openvault.vault.key_add.get_provider", return_value=odd):
        result = add_tested_key(vault, "groq", _SECRET, client=_client(200, {}))
    assert result.ok is True
    assert vault.list_keys()[0].role == "backup"


def test_sealed_vault_does_not_call_the_provider(vault: KeyVault) -> None:
    vault.seal.lock()
    seen: dict[str, Any] = {}
    code, out, err, logs = _run(vault, ["groq"], stdin=_SECRET, client=_client(200, seen))
    assert code == 1
    assert seen.get("calls", 0) == 0
    assert "vault is sealed" in err
    assert vault.list_keys() == []
    assert _SECRET not in _blob(out, err, logs)


def test_seal_dropped_after_probe_stores_nothing(vault: KeyVault) -> None:
    seen: dict[str, Any] = {}
    client = _client(200, seen, before=vault.seal.lock)
    code, out, err, logs = _run(vault, ["groq"], stdin=_SECRET, client=client)
    assert code == 1
    assert seen["calls"] == 1
    assert vault.list_keys() == []
    assert "vault is sealed" in err
    assert _SECRET not in _blob(out, err, logs)


def test_fingerprint_secret_is_per_vault(tmp_path: Path) -> None:
    first = KeyVault(db_path=tmp_path / "a.db", seal=Seal(Fernet.generate_key()))
    second = KeyVault(db_path=tmp_path / "b.db", seal=Seal(Fernet.generate_key()))
    left = first.get_or_create_fingerprint_secret()
    assert left == first.get_or_create_fingerprint_secret()
    assert left != second.get_or_create_fingerprint_secret()
    assert len(left) == 32
    assert first.find_by_fingerprint("") is None
    assert first.get_fingerprint("missing") is None
    assert mask_key_id("abcd") == "****"
    assert mask_key_id("123456789") == "1234...6789"


def test_existing_db_gains_fingerprint_column(tmp_path: Path) -> None:
    db = tmp_path / "keys.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE keys (
              id TEXT PRIMARY KEY,
              label TEXT NOT NULL,
              provider TEXT NOT NULL,
              role TEXT NOT NULL,
              base_url TEXT NOT NULL DEFAULT '',
              secret_blob BLOB NOT NULL,
              masked TEXT NOT NULL DEFAULT '',
              enabled INTEGER NOT NULL DEFAULT 1,
              priority INTEGER NOT NULL DEFAULT 100,
              precheck_status TEXT NOT NULL DEFAULT 'unknown',
              last_latency_ms REAL,
              last_error TEXT,
              last_precheck_at REAL,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              account_id TEXT,
              lifecycle TEXT NOT NULL DEFAULT 'active',
              replaced_by TEXT,
              custody TEXT NOT NULL DEFAULT 'pooled'
            )
            """
        )
    opened = KeyVault(db_path=db, seal=Seal(Fernet.generate_key()))
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(keys)")}
        meta = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'vault_meta'"
        ).fetchone()
    assert "key_fp" in cols
    assert meta is not None
    assert opened.list_keys() == []
    plain = opened.create(label="legacy", provider="groq", secret="legacy-secret-value-not-logged")
    assert opened.get_fingerprint(plain.id) is None


def test_help_and_missing_provider(vault: KeyVault) -> None:
    code, out, err, logs = _run(vault, ["--help"])
    assert code == 0
    assert "catalog id" in out
    assert _SECRET not in _blob(out, err, logs)
    code, out, err, _logs = _run(vault, [])
    assert code == 2
    assert "usage:" in err
    assert out == ""


def test_tty_prompt_does_not_echo_the_key(vault: KeyVault) -> None:
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    seen: dict[str, Any] = {}
    with patch("openmw.openvault.vault.key_add.getpass.getpass", return_value=_SECRET) as prompt:
        code, out, err, logs = _run(
            vault,
            ["groq"],
            client=_client(200, seen),
            stdin_stream=_Tty(),
        )
    assert prompt.called
    assert code == 0, err
    assert _SECRET not in _blob(out, err, logs)
    assert vault.get_secret(vault.list_keys()[0].id) == _SECRET


def test_read_key_without_isatty() -> None:
    class _NoIsatty:
        def read(self) -> str:
            return _SECRET + "\n"

    assert read_key(stdin=_NoIsatty(), stderr=io.StringIO()) == _SECRET  # type: ignore[arg-type]


def test_module_main_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["openvault-add", "--help"])
    assert main() == 0
    captured = capsys.readouterr()
    assert "catalog id" in captured.out
    assert _SECRET not in captured.out
    assert _SECRET not in captured.err


def test_read_key_when_isatty_fails() -> None:
    class _Bad(io.StringIO):
        def isatty(self) -> bool:
            raise OSError("no tty")

    assert read_key(stdin=_Bad(_SECRET + "\n"), stderr=io.StringIO()) == _SECRET


def test_probe_client_ignores_process_env(vault: KeyVault, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"marker": _MARKER})

    def factory(**kwargs: Any) -> httpx.Client:
        seen["trust_env"] = kwargs.get("trust_env")
        seen["follow_redirects"] = kwargs.get("follow_redirects")
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        return real(
            timeout=float(timeout),
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("openmw.openvault.vault.key_add.httpx.Client", factory)
    code, out, err, logs = _run(vault, ["groq"], stdin=_SECRET)
    assert code == 0, err
    assert seen["trust_env"] is False
    assert seen["follow_redirects"] is False
    assert seen["method"] == "POST"
    assert str(seen["url"]).endswith("/chat/completions")
    assert _SECRET not in _blob(out, err, logs)


def test_launcher_refuses_argv_key_without_echoing_it(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_cli()
    assert module.refuse_add_command_line(["up"]) is None
    assert module.refuse_add_command_line(["add", "groq"]) is None
    assert module.refuse_add_command_line(["add", "--help"]) is None
    code = module.refuse_add_command_line(["add", "groq", _SECRET])
    captured = capsys.readouterr()
    assert code == 2
    assert _SECRET not in captured.out
    assert _SECRET not in captured.err
    assert "command line" in captured.err
    code = module.refuse_add_command_line(["add", _SECRET])
    captured = capsys.readouterr()
    assert code == 2
    assert _SECRET not in captured.err


def test_launcher_spawns_add_with_the_provider_only(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_cli()
    seen: dict[str, Any] = {}

    def fake_call(cmd: list[str], cwd: str | None = None) -> int:
        seen["cmd"] = list(cmd)
        seen["cwd"] = cwd
        return 0

    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(module.subprocess, "call", fake_call)
    code = module.cmd_add(argparse.Namespace(provider="groq"))
    assert code == 0
    assert seen["cmd"][-2:] == ["openmw.openvault.vault.key_add", "groq"]
    assert seen["cmd"].count("groq") == 1
    assert _SECRET not in " ".join(seen["cmd"])
    assert str(seen["cwd"]).endswith("OpenMW")

    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    assert module.cmd_add(argparse.Namespace(provider="groq")) == 1
    assert module.cmd_add(argparse.Namespace(provider=_SECRET)) == 2
    captured = capsys.readouterr()
    assert _SECRET not in captured.out
    assert _SECRET not in captured.err
    assert "command line" in captured.err

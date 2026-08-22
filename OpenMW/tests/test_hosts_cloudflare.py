"""Cloudflare Pages adapter.

The behaviour under test is mostly refusal. This adapter's whole reason for
existing is that the previous "host" step reported ``simulated`` in a way that
read like a successful deploy, so the cases that matter are the ones where it
must decline rather than the happy path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openmw.openvault.ship.hosts.cloudflare_pages import (
    CloudflarePagesAdapter,
    normalize_project_name,
    parse_wrangler_url,
)


class TestProjectName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("OpenVault", "openvault"),
            ("my repo name", "my-repo-name"),
            ("Weird__Name!!", "weird-name"),
            ("---trim---", "trim"),
            ("9lives", "9lives"),
            ("", "openvault-app"),
            ("!!!", "openvault-app"),
        ],
    )
    def test_normalises(self, raw: str, expected: str) -> None:
        assert normalize_project_name(raw) == expected

    def test_length_capped(self) -> None:
        assert len(normalize_project_name("x" * 200)) <= 58

    def test_never_ends_with_dash(self) -> None:
        # A trailing dash is rejected by Cloudflare with an opaque error.
        assert not normalize_project_name(("a" * 57) + " b").endswith("-")


class TestUrlParsing:
    def test_extracts_url(self) -> None:
        out = "Uploading... done.\nDeployment complete! https://abc123.my-app.pages.dev\n"
        assert parse_wrangler_url(out) == "https://abc123.my-app.pages.dev"

    def test_strips_trailing_punctuation(self) -> None:
        assert parse_wrangler_url("see https://a.pages.dev.") == "https://a.pages.dev"

    def test_absent_url_is_empty(self) -> None:
        assert parse_wrangler_url("Uploaded 3 files") == ""


class TestPreflight:
    def test_missing_token_is_actionable(self) -> None:
        pre = CloudflarePagesAdapter(api_token=None, account_id="acct").preflight()
        assert pre.ready is False
        assert "api-tokens" in pre.blocker

    def test_missing_account_is_actionable(self) -> None:
        pre = CloudflarePagesAdapter(api_token="tok", account_id="").preflight()
        assert pre.ready is False
        assert "account id" in pre.blocker.lower()


class TestDeployRefusals:
    """Every one of these used to be reported as a successful 'simulated' host."""

    def test_missing_artifact_dir(self, tmp_path: Path) -> None:
        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        result = adapter.deploy(tmp_path / "nope", project="app")
        assert result.ok is False
        assert result.url == ""
        assert "not a directory" in result.detail

    def test_empty_artifact_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "out"
        empty.mkdir()
        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        result = adapter.deploy(empty, project="app")
        assert result.ok is False
        assert "empty" in result.detail

    def test_no_credentials_refuses_before_network(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        (out / "index.html").write_text("hi", encoding="utf-8")
        result = CloudflarePagesAdapter(api_token=None, account_id=None).deploy(out, project="app")
        assert result.ok is False
        assert result.url == ""

    def test_success_without_a_url_is_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A zero exit code with no URL must not be reported as deployed.

        Guessing `https://{project}.pages.dev` here would produce a link that
        404s while the UI claims the site is live.
        """
        out = tmp_path / "out"
        out.mkdir()
        (out / "index.html").write_text("hi", encoding="utf-8")

        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        monkeypatch.setattr(
            adapter,
            "preflight",
            lambda: __import__(
                "openmw.openvault.ship.hosts.base", fromlist=["Preflight"]
            ).Preflight(ready=True),
        )
        monkeypatch.setattr(adapter, "ensure_project", lambda p, **kw: (True, "app"))
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.cloudflare_pages.shutil.which",
            lambda _n: "wrangler",
        )
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.cloudflare_pages.subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, b"Uploaded 3 files\n", b""),
        )

        result = adapter.deploy(out, project="app")
        assert result.ok is False
        assert result.url == ""
        assert "no pages.dev URL" in result.detail

    def test_nonzero_exit_reports_the_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "out"
        out.mkdir()
        (out / "index.html").write_text("hi", encoding="utf-8")

        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        monkeypatch.setattr(
            adapter,
            "preflight",
            lambda: __import__(
                "openmw.openvault.ship.hosts.base", fromlist=["Preflight"]
            ).Preflight(ready=True),
        )
        monkeypatch.setattr(adapter, "ensure_project", lambda p, **kw: (True, "app"))
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.cloudflare_pages.shutil.which",
            lambda _n: "wrangler",
        )
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.cloudflare_pages.subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 1, b"", b"Authentication error [code: 10000]"
            ),
        )

        result = adapter.deploy(out, project="app")
        assert result.ok is False
        assert "exited 1" in result.detail
        assert "Authentication error" in result.log

    def test_happy_path_returns_the_parsed_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "out"
        out.mkdir()
        (out / "index.html").write_text("hi", encoding="utf-8")

        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        monkeypatch.setattr(
            adapter,
            "preflight",
            lambda: __import__(
                "openmw.openvault.ship.hosts.base", fromlist=["Preflight"]
            ).Preflight(ready=True),
        )
        monkeypatch.setattr(adapter, "ensure_project", lambda p, **kw: (True, "app"))
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.cloudflare_pages.shutil.which",
            lambda _n: "wrangler",
        )
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.cloudflare_pages.subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 0, b"Success! https://dead-beef.app.pages.dev\n", b""
            ),
        )

        result = adapter.deploy(out, project="app")
        assert result.ok is True
        assert result.url == "https://dead-beef.app.pages.dev"


class TestAttachDomain:
    def test_blank_hostname_refused(self) -> None:
        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        assert adapter.attach_domain(project="app", hostname="  ").ok is False

    def test_external_registrar_gets_paste_ready_records(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A domain bought at Spaceship cannot be wired by us — say what to do."""
        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        monkeypatch.setattr(
            adapter, "_api", lambda *a, **k: (False, {}, "zone not found (code 8000007)")
        )
        result = adapter.attach_domain(project="My App", hostname="netie.ai")
        assert result.ok is False
        assert result.required_records
        record = result.required_records[0]
        assert record["type"] == "CNAME"
        assert record["name"] == "netie.ai"
        assert record["value"] == "my-app.pages.dev"

    def test_already_attached_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = CloudflarePagesAdapter(api_token="tok", account_id="acct")
        monkeypatch.setattr(adapter, "_api", lambda *a, **k: (False, {}, "Domain already exists"))
        assert adapter.attach_domain(project="app", hostname="netie.ai").ok is True

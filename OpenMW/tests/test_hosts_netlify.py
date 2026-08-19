"""Netlify host adapter — mocked HTTP only; never hit api.netlify.com.

Honesty cases matter more than the happy path: missing token, ready deploy with
no observed URL, and empty artifacts must all refuse (never invent *.netlify.app).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openmw.openvault.ship.hosts import ADAPTERS, adapter_ids
from openmw.openvault.ship.hosts.netlify import (
    NetlifyAdapter,
    from_vault,
    normalize_project_name,
    observed_url_from_deploy,
)


class TestHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("My App", "my-app"),
            ("", "openvault-app"),
            ("Weird__Name!!", "weird-name"),
        ],
    )
    def test_normalize_project_name(self, raw: str, expected: str) -> None:
        assert normalize_project_name(raw) == expected

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"ssl_url": "https://demo.netlify.app"}, "https://demo.netlify.app"),
            (
                {"deploy_ssl_url": "https://deploy--demo.netlify.app"},
                "https://deploy--demo.netlify.app",
            ),
            ({"url": "http://legacy.example"}, "http://legacy.example"),
            ({"ssl_url": "", "url": ""}, ""),
            ({}, ""),
            ({"ssl_url": "not-a-url"}, ""),
        ],
    )
    def test_observed_url_from_deploy(self, body: dict[str, Any], expected: str) -> None:
        assert observed_url_from_deploy(body) == expected


class TestRegistry:
    def test_netlify_registered_alongside_peers(self) -> None:
        assert "netlify" in ADAPTERS
        assert "coolify" in ADAPTERS
        assert "cloudflare_pages" in ADAPTERS
        assert ADAPTERS["netlify"] is NetlifyAdapter
        assert "netlify" in adapter_ids()
        assert "vercel" not in ADAPTERS


class TestPreflight:
    def test_missing_token_is_actionable(self) -> None:
        pre = NetlifyAdapter(api_token=None).preflight()
        assert pre.ready is False
        assert "netlify" in pre.blocker.lower()
        assert "token" in pre.blocker.lower()
        assert pre.facts == {}

    def test_bad_token_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = NetlifyAdapter(api_token="bad")

        def fake_request(method, url, **kwargs):  # noqa: ANN001, ARG001
            resp = MagicMock()
            resp.status_code = 401
            resp.content = b'{"message":"Unauthorized"}'
            resp.json.return_value = {"message": "Unauthorized"}
            return resp

        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.netlify.httpx.request", fake_request
        )
        pre = adapter.preflight()
        assert pre.ready is False
        assert "rejected the token" in pre.blocker.lower()

    def test_ready_when_user_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = NetlifyAdapter(api_token="tok", site_id="site-1")

        def fake_request(method, url, **kwargs):  # noqa: ANN001, ARG001
            resp = MagicMock()
            resp.status_code = 200
            if url.endswith("/user"):
                body: Any = {"email": "dev@example.com", "full_name": "Dev"}
            elif "/sites/" in url:
                body = {
                    "id": "site-1",
                    "name": "demo",
                    "ssl_url": "https://demo.netlify.app",
                }
            else:
                body = {}
            resp.content = b"{}"
            resp.json.return_value = body
            return resp

        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.netlify.httpx.request", fake_request
        )
        pre = adapter.preflight()
        assert pre.ready is True
        assert pre.facts["email"] == "dev@example.com"
        assert pre.facts["current_url"] == "https://demo.netlify.app"

    def test_bad_site_id_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = NetlifyAdapter(api_token="tok", site_id="missing")

        def fake_request(method, url, **kwargs):  # noqa: ANN001, ARG001
            resp = MagicMock()
            if url.endswith("/user"):
                resp.status_code = 200
                resp.content = b"{}"
                resp.json.return_value = {"email": "dev@example.com"}
                return resp
            resp.status_code = 404
            resp.content = b'{"message":"Not Found"}'
            resp.json.return_value = {"message": "Not Found"}
            return resp

        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.netlify.httpx.request", fake_request
        )
        pre = adapter.preflight()
        assert pre.ready is False
        assert "not reachable" in pre.blocker.lower()


class TestDeploy:
    def _adapter(self) -> NetlifyAdapter:
        return NetlifyAdapter(api_token="tok", site_id="site-1")

    def _artifact(self, tmp_path: Path) -> Path:
        out = tmp_path / "dist"
        out.mkdir()
        (out / "index.html").write_text("<html>hi</html>", encoding="utf-8")
        return out

    def test_no_credentials_refuses_before_network(self, tmp_path: Path) -> None:
        artifact = self._artifact(tmp_path)
        result = NetlifyAdapter(api_token=None).deploy(artifact, project="app")
        assert result.ok is False
        assert result.url == ""
        assert "token" in result.detail.lower()

    def test_empty_artifact_refuses(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        result = self._adapter().deploy(empty, project="app")
        assert result.ok is False
        assert result.url == ""
        assert "empty" in result.detail.lower()

    def test_ready_without_url_is_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Netlify said ready but gave no URL — refuse, do not invent one."""
        adapter = self._adapter()
        artifact = self._artifact(tmp_path)

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            if path == "/user":
                return True, {"email": "dev@example.com"}, ""
            if path == "/sites/site-1":
                return True, {"id": "site-1", "name": "demo", "ssl_url": ""}, ""
            if path == "/sites/site-1/deploys":
                return True, {
                    "id": "dep-1",
                    "state": "ready",
                    "ssl_url": "",
                    "deploy_ssl_url": "",
                    "url": "",
                }, ""
            return False, {}, f"unexpected {path}"

        monkeypatch.setattr(adapter, "_api", fake_api)
        result = adapter.deploy(artifact, project="demo")
        assert result.ok is False
        assert result.url == ""
        assert "ssl_url" in result.detail.lower() or "no" in result.detail.lower()
        assert result.deployment_ref == "dep-1"

    def test_happy_path_returns_observed_ssl_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._adapter()
        artifact = self._artifact(tmp_path)

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            if path == "/user":
                return True, {"email": "dev@example.com"}, ""
            if path == "/sites/site-1":
                return True, {
                    "id": "site-1",
                    "name": "demo",
                    "ssl_url": "https://demo.netlify.app",
                }, ""
            if path == "/sites/site-1/deploys":
                return True, {
                    "id": "dep-9",
                    "state": "ready",
                    "ssl_url": "https://demo.netlify.app",
                }, ""
            return False, {}, f"unexpected {path}"

        monkeypatch.setattr(adapter, "_api", fake_api)
        result = adapter.deploy(artifact, project="demo")
        assert result.ok is True
        assert result.url == "https://demo.netlify.app"
        assert result.deployment_ref == "dep-9"

    def test_poll_then_observed_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._adapter()
        artifact = self._artifact(tmp_path)
        polls = {"n": 0}

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            if path == "/user":
                return True, {"email": "dev@example.com"}, ""
            if path == "/sites/site-1":
                return True, {"id": "site-1", "name": "demo"}, ""
            if path == "/sites/site-1/deploys":
                return True, {"id": "dep-2", "state": "building"}, ""
            if path == "/deploys/dep-2":
                polls["n"] += 1
                if polls["n"] < 2:
                    return True, {"id": "dep-2", "state": "building"}, ""
                return True, {
                    "id": "dep-2",
                    "state": "ready",
                    "ssl_url": "https://ready.example.netlify.app",
                }, ""
            return False, {}, f"unexpected {path}"

        monkeypatch.setattr(adapter, "_api", fake_api)
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.netlify.time.sleep", lambda *_: None
        )
        result = adapter.deploy(artifact, project="demo")
        assert result.ok is True
        assert result.url == "https://ready.example.netlify.app"
        assert polls["n"] >= 2

    def test_deploy_with_no_deploy_id_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._adapter()
        artifact = self._artifact(tmp_path)

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            if path == "/user":
                return True, {"email": "dev@example.com"}, ""
            if path == "/sites/site-1":
                return True, {"id": "site-1", "name": "demo"}, ""
            if path == "/sites/site-1/deploys":
                return True, {"state": "ready", "ssl_url": "https://x.netlify.app"}, ""
            return False, {}, f"unexpected {path}"

        monkeypatch.setattr(adapter, "_api", fake_api)
        result = adapter.deploy(artifact, project="demo")
        assert result.ok is False
        assert result.url == ""
        assert "no deploy id" in result.detail.lower()


class TestAttachDomain:
    def test_blank_hostname_refused(self) -> None:
        adapter = NetlifyAdapter(api_token="tok", site_id="site-1")
        assert adapter.attach_domain(project="app", hostname="  ").ok is False

    def test_already_on_site_is_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = NetlifyAdapter(api_token="tok", site_id="site-1")

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            return True, {
                "id": "site-1",
                "custom_domain": "netie.ai",
                "default_domain": "demo.netlify.app",
            }, ""

        monkeypatch.setattr(adapter, "_api", fake_api)
        result = adapter.attach_domain(project="app", hostname="netie.ai")
        assert result.ok is True

    def test_missing_default_domain_does_not_invent_cname(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = NetlifyAdapter(api_token="tok", site_id="site-1")

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            if method == "GET" and path.startswith("/sites/"):
                return True, {
                    "id": "site-1",
                    "custom_domain": "",
                    "default_domain": "",
                }, ""
            return False, {}, "refused by Netlify"

        monkeypatch.setattr(adapter, "_api", fake_api)
        result = adapter.attach_domain(project="app", hostname="netie.ai")
        assert result.ok is False
        assert result.required_records
        # Must not invent a concrete *.netlify.app target.
        assert "netlify.app" not in result.required_records[0]["value"]


class TestFromVault:
    def test_reads_provider_and_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NETLIFY_SITE_ID", "env-site")
        adapter = from_vault(lambda _p: "vault-token")
        assert adapter.credential_provider == "netlify"
        assert adapter._token == "vault-token"
        assert adapter._site_id == "env-site"

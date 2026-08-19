"""Coolify host adapter — mocked HTTP only; never hit a live instance.

Honesty cases matter more than the happy path: missing creds, missing app UUID,
finished deploy with no observed URL must all refuse (never invent a live URL).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openmw.openvault.ship.hosts import ADAPTERS, adapter_ids
from openmw.openvault.ship.hosts.coolify import (
    CoolifyAdapter,
    from_vault,
    normalize_base_url,
    normalize_project_name,
    observed_url_from_fqdn,
)


class TestHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://coolify.example.com/", "https://coolify.example.com"),
            ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
            ("not-a-url", ""),
            ("", ""),
            ("ftp://bad", ""),
        ],
    )
    def test_normalize_base_url(self, raw: str, expected: str) -> None:
        assert normalize_base_url(raw) == expected

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
        ("raw", "expected"),
        [
            ("https://app.example.com", "https://app.example.com"),
            ("https://a.com,https://b.com", "https://a.com"),
            ("app.example.com", "https://app.example.com"),
            ("", ""),
            ("  ", ""),
        ],
    )
    def test_observed_url_from_fqdn(self, raw: str, expected: str) -> None:
        assert observed_url_from_fqdn(raw) == expected


class TestRegistry:
    def test_coolify_registered_alongside_cf(self) -> None:
        assert "coolify" in ADAPTERS
        assert "cloudflare_pages" in ADAPTERS
        assert ADAPTERS["coolify"] is CoolifyAdapter
        assert "coolify" in adapter_ids()


class TestPreflight:
    def test_missing_base_url_is_actionable(self) -> None:
        pre = CoolifyAdapter(
            base_url=None, api_token="tok", app_uuid="uuid-1"
        ).preflight()
        assert pre.ready is False
        assert "COOLIFY_URL" in pre.blocker
        assert pre.facts == {}

    def test_missing_token_is_actionable(self) -> None:
        pre = CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token=None,
            app_uuid="uuid-1",
        ).preflight()
        assert pre.ready is False
        assert "coolify" in pre.blocker.lower()
        assert "token" in pre.blocker.lower()

    def test_missing_app_uuid_is_actionable(self) -> None:
        pre = CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token="tok",
            app_uuid="",
        ).preflight()
        assert pre.ready is False
        assert "COOLIFY_APP_UUID" in pre.blocker

    def test_bad_token_refuses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token="bad",
            app_uuid="uuid-1",
        )

        def fake_request(method, url, **kwargs):  # noqa: ANN001, ARG001
            resp = MagicMock()
            resp.status_code = 401
            resp.content = b'{"message":"Unauthenticated"}'
            resp.json.return_value = {"message": "Unauthenticated"}
            return resp

        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.coolify.httpx.request", fake_request
        )
        pre = adapter.preflight()
        assert pre.ready is False
        assert "rejected the token" in pre.blocker.lower()

    def test_ready_when_teams_and_app_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token="tok",
            app_uuid="app-uuid",
        )

        def fake_request(method, url, **kwargs):  # noqa: ANN001, ARG001
            resp = MagicMock()
            resp.status_code = 200
            if url.endswith("/teams"):
                body: Any = [{"name": "Personal"}]
            elif "/applications/" in url:
                body = {
                    "uuid": "app-uuid",
                    "name": "demo",
                    "fqdn": "https://demo.example.com",
                }
            else:
                body = {}
            resp.content = b"{}"
            resp.json.return_value = body
            return resp

        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.coolify.httpx.request", fake_request
        )
        pre = adapter.preflight()
        assert pre.ready is True
        assert pre.facts["base_url"] == "https://coolify.example.com"
        assert pre.facts["current_fqdn"] == "https://demo.example.com"


class TestDeploy:
    def _adapter(self) -> CoolifyAdapter:
        return CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token="tok",
            app_uuid="app-uuid",
        )

    def test_no_credentials_refuses_before_network(self, tmp_path: Path) -> None:
        result = CoolifyAdapter(
            base_url=None, api_token=None, app_uuid=None
        ).deploy(tmp_path, project="app")
        assert result.ok is False
        assert result.url == ""
        assert "COOLIFY_URL" in result.detail

    def test_finished_without_url_is_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Coolify said finished but gave no URL — refuse, do not invent one."""
        adapter = self._adapter()
        calls: list[str] = []

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            calls.append(f"{method} {path}")
            if path == "/teams":
                return True, [{"name": "Personal"}], ""
            if path == "/applications/app-uuid":
                # Preflight ok; post-deploy fqdn also empty.
                return True, {"uuid": "app-uuid", "name": "demo", "fqdn": ""}, ""
            if path == "/deploy":
                return True, {
                    "deployments": [{"deployment_uuid": "dep-1", "status": "queued"}]
                }, ""
            if path == "/deployments/dep-1":
                return True, {"status": "finished", "deployment_url": "", "logs": "ok"}, ""
            return False, {}, f"unexpected {path}"

        monkeypatch.setattr(adapter, "_api", fake_api)
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.coolify.time.sleep", lambda *_: None
        )

        result = adapter.deploy(tmp_path, project="demo")
        assert result.ok is False
        assert result.url == ""
        assert "no deployment_url" in result.detail.lower() or "fqdn" in result.detail.lower()
        assert result.deployment_ref == "dep-1"

    def test_happy_path_returns_observed_deployment_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._adapter()

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            if path == "/teams":
                return True, [{"name": "Personal"}], ""
            if path == "/applications/app-uuid":
                return True, {
                    "uuid": "app-uuid",
                    "name": "demo",
                    "fqdn": "https://demo.example.com",
                }, ""
            if path == "/deploy":
                return True, {
                    "deployments": [{"deployment_uuid": "dep-9", "status": "queued"}]
                }, ""
            if path == "/deployments/dep-9":
                return True, {
                    "status": "finished",
                    "deployment_url": "https://demo.example.com",
                    "logs": "built",
                }, ""
            return False, {}, f"unexpected {path}"

        monkeypatch.setattr(adapter, "_api", fake_api)
        monkeypatch.setattr(
            "openmw.openvault.ship.hosts.coolify.time.sleep", lambda *_: None
        )

        result = adapter.deploy(tmp_path, project="demo")
        assert result.ok is True
        assert result.url == "https://demo.example.com"
        assert result.deployment_ref == "dep-9"

    def test_deploy_with_no_deployment_uuid_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = self._adapter()

        def fake_api(method, path, **kwargs):  # noqa: ANN001, ARG001
            if path == "/teams":
                return True, [], ""
            if path == "/applications/app-uuid":
                return True, {"uuid": "app-uuid", "name": "demo"}, ""
            if path == "/deploy":
                return True, {"deployments": []}, ""
            return False, {}, f"unexpected {path}"

        monkeypatch.setattr(adapter, "_api", fake_api)
        result = adapter.deploy(tmp_path, project="demo")
        assert result.ok is False
        assert result.url == ""
        assert "no deployment UUID" in result.detail


class TestAttachDomain:
    def test_blank_hostname_refused(self) -> None:
        adapter = CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token="tok",
            app_uuid="app-uuid",
        )
        assert adapter.attach_domain(project="app", hostname="  ").ok is False

    def test_already_on_app_is_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token="tok",
            app_uuid="app-uuid",
        )
        monkeypatch.setattr(
            adapter,
            "_api",
            lambda *a, **k: (
                True,
                {"uuid": "app-uuid", "fqdn": "https://netie.ai"},
                "",
            ),
        )
        result = adapter.attach_domain(project="app", hostname="netie.ai")
        assert result.ok is True

    def test_missing_domain_gets_paste_ready_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = CoolifyAdapter(
            base_url="https://coolify.example.com",
            api_token="tok",
            app_uuid="app-uuid",
        )
        monkeypatch.setattr(
            adapter,
            "_api",
            lambda *a, **k: (
                True,
                {"uuid": "app-uuid", "fqdn": "https://other.example.com"},
                "",
            ),
        )
        result = adapter.attach_domain(project="app", hostname="netie.ai")
        assert result.ok is False
        assert result.required_records
        assert result.required_records[0]["name"] == "netie.ai"


class TestFromVault:
    def test_reads_provider_and_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("COOLIFY_URL", "https://coolify.example.com")
        monkeypatch.setenv("COOLIFY_APP_UUID", "env-uuid")
        adapter = from_vault(lambda _p: "vault-token")
        assert adapter.credential_provider == "coolify"
        assert adapter._token == "vault-token"
        assert adapter._base == "https://coolify.example.com"
        assert adapter._app_uuid == "env-uuid"

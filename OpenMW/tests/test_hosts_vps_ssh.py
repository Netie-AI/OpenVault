"""VPS-over-SSH host adapter — fake transport only, never a live box.

The cases that matter here are the ones where a PaaS lies to you: a container
that started but never answered, a proxy config that would take the box down, a
deploy that reports a URL nobody fetched, and a secret that ends up in argv or
in the response body. Each has a test that fails if the adapter starts lying.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from openmw.openvault.ship.detect import DetectedStack
from openmw.openvault.ship.hosts import ADAPTERS, adapter_ids
from openmw.openvault.ship.hosts.vps_ssh import (
    CADDY_SITE_DIR,
    RunResult,
    VpsConfigError,
    VpsSshAdapter,
    from_vault,
    normalize_hostname,
    normalize_project_name,
    other_colour,
    plan_from_stack,
    port_base,
    render_caddy_site,
    render_dockerfile,
    render_env_file,
    tar_directory,
)

# ─── Fake transport ──────────────────────────────────────────────────────────


class FakeRunner:
    """Records every script and file, answers from substring rules."""

    def __init__(
        self,
        *,
        uid: str = "0",
        docker: str = "Docker version 27.1.1",
        caddy: str = "v2.8.4",
        ip: str = "203.0.113.10",
        rules: dict[str, RunResult] | None = None,
    ) -> None:
        self.scripts: list[str] = []
        self.puts: list[tuple[str, bytes, str]] = []
        self._uid = uid
        self._docker = docker
        self._caddy = caddy
        self._ip = ip
        self._rules = rules or {}

    def run(self, script: str, *, timeout_s: float | None = None) -> RunResult:
        self.scripts.append(script)
        for needle, result in self._rules.items():
            if needle in script:
                return result
        if "echo openvault-ok" in script:
            return RunResult(0, "openvault-ok\n")
        if script.strip() == "id -u":
            return RunResult(0, f"{self._uid}\n")
        if "sudo -n true" in script:
            return RunResult(1, stderr="sudo: a password is required")
        if "PRETTY_NAME" in script:
            return RunResult(0, "Debian GNU/Linux 12 (bookworm)\n")
        if "docker --version" in script:
            return RunResult(0, f"{self._docker}\n")
        if "caddy version" in script:
            return RunResult(0, f"{self._caddy}\n")
        if "command -v apt-get" in script:
            return RunResult(0, "yes\n")
        if "route get" in script:
            return RunResult(0, f"{self._ip}\n")
        if "docker ps" in script:
            return RunResult(0, "")
        if "echo yes || echo no" in script:
            return RunResult(0, "no\n")
        return RunResult(0, "")

    def put_bytes(self, remote_path: str, data: bytes, *, mode: str = "600") -> RunResult:
        self.puts.append((remote_path, data, mode))
        return RunResult(0, "")

    # -- assertions helpers -------------------------------------------------

    def ran(self, needle: str) -> bool:
        return any(needle in s for s in self.scripts)

    def written(self, suffix: str) -> tuple[str, bytes, str]:
        for entry in self.puts:
            if entry[0].endswith(suffix):
                return entry
        raise AssertionError(f"nothing written ending in {suffix}: {[p for p, _, _ in self.puts]}")


def node_stack(**over: Any) -> DetectedStack:
    base = dict(
        project_path=".",
        primary="node",
        confidence=0.9,
        framework="express",
        category="backend",
        install_command="npm ci",
        build_command="npm run build",
        start_command="npm start",
        production_port=3000,
        build_image="node:22",
    )
    base.update(over)
    return DetectedStack(**base)  # type: ignore[arg-type]


def ok_probe(status: int = 200) -> Callable[..., Any]:
    class _Resp:
        status_code = status

    def _get(url: str, **kwargs: Any) -> Any:
        return _Resp()

    return _get


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "index.js").write_text("console.log('hi')", encoding="utf-8")
    return tmp_path


# ─── Pure helpers ────────────────────────────────────────────────────────────


class TestNormalizers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("app.example.com", "app.example.com"),
            ("https://App.Example.com/", "app.example.com"),
            ("EXAMPLE.COM.", "example.com"),
            ("", ""),
            ("bad host", ""),
            ("evil.com { }", ""),
            ("a.com;rm -rf /", ""),
            ("a.com\nother.com", ""),
            ("$(whoami).com", ""),
        ],
    )
    def test_hostname_rejects_anything_shell_or_caddy_could_read(
        self, raw: str, expected: str
    ) -> None:
        assert normalize_hostname(raw) == expected

    def test_project_name_is_container_safe(self) -> None:
        assert normalize_project_name("My App!!") == "my-app"
        assert normalize_project_name("") == "openvault-app"

    def test_port_base_is_stable_and_colours_never_collide(self) -> None:
        assert port_base("shop", "blue") == port_base("shop", "blue")
        assert port_base("shop", "blue") != port_base("shop", "green")
        assert abs(port_base("shop", "blue") - port_base("shop", "green")) >= 2
        assert other_colour("blue") == "green"
        assert other_colour("green") == "blue"


class TestRenderers:
    def test_env_file_round_trips_awkward_values(self) -> None:
        body = render_env_file({"API_KEY": 'a b"c$d', "N": "1"})
        assert body.splitlines() == ['API_KEY=a b"c$d', "N=1"]

    def test_env_file_refuses_newline_value(self) -> None:
        with pytest.raises(VpsConfigError, match="newline"):
            render_env_file({"K": "line1\nline2"})

    def test_env_file_refuses_bad_name(self) -> None:
        with pytest.raises(VpsConfigError, match="invalid env name"):
            render_env_file({"2BAD": "x"})

    def test_dockerfile_uses_detected_image_and_commands(self) -> None:
        text = render_dockerfile(
            build_image="node:22",
            install_command="npm ci",
            build_command="npm run build",
            start_command="npm start",
            port=3000,
        )
        assert "FROM node:22" in text
        assert "RUN npm ci" in text
        assert "ENV PORT=3000" in text
        assert "CMD npm start" in text

    def test_dockerfile_without_a_start_command_is_refused(self) -> None:
        """A container that stays up doing nothing passes `docker ps` and serves 502s."""
        with pytest.raises(VpsConfigError, match="no start command"):
            render_dockerfile(
                build_image="node:22",
                install_command="npm ci",
                build_command="npm run build",
                start_command="  ",
                port=3000,
            )

    def test_caddy_proxy_block_load_balances_every_replica(self) -> None:
        text = render_caddy_site(
            hostname="app.example.com",
            site_address="app.example.com",
            upstreams=[21000, 21001],
        )
        assert "app.example.com {" in text
        assert "reverse_proxy 127.0.0.1:21000 127.0.0.1:21001" in text
        assert "lb_policy least_conn" in text
        assert "health_uri /" in text

    def test_caddy_static_block_serves_files(self) -> None:
        text = render_caddy_site(
            hostname="",
            site_address="http://203.0.113.10",
            static_root="/srv/openvault/site/current",
        )
        assert "http://203.0.113.10 {" in text
        assert "root * /srv/openvault/site/current" in text
        assert "file_server" in text

    def test_proxy_site_without_upstreams_is_a_config_error(self) -> None:
        with pytest.raises(VpsConfigError):
            render_caddy_site(hostname="a.com", site_address="a.com")

    def test_tar_skips_git_and_node_modules(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x", encoding="utf-8")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "big.js").write_text("y" * 1000, encoding="utf-8")
        blob = tar_directory(tmp_path)
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            names = tar.getnames()
        assert "app.py" in names
        assert not [n for n in names if n.startswith((".git", "node_modules"))]


class TestPlan:
    def test_long_running_stack_is_a_container(self) -> None:
        plan = plan_from_stack(node_stack(), project="Shop", hostname="shop.example.com")
        assert (plan.mode, plan.project, plan.site_address) == (
            "container",
            "shop",
            "shop.example.com",
        )

    def test_build_output_without_a_server_process_is_static(self) -> None:
        stack = node_stack(category="static", start_command="", output_directory="dist")
        plan = plan_from_stack(stack, project="site", server_ip="203.0.113.10")
        assert plan.mode == "static"
        assert plan.site_address == "http://203.0.113.10"

    def test_stack_with_both_output_and_server_stays_a_container(self) -> None:
        """Serving a fullstack app's dist/ as files silently kills its API."""
        stack = node_stack(output_directory="dist", start_command="npm start")
        plan = plan_from_stack(stack, project="app", hostname="a.example.com")
        assert plan.mode == "container"

    def test_bad_hostname_refuses_before_anything_runs(self) -> None:
        with pytest.raises(VpsConfigError, match="not a valid hostname"):
            plan_from_stack(node_stack(), project="app", hostname="evil.com { }")

    def test_no_hostname_and_no_ip_is_refused(self) -> None:
        with pytest.raises(VpsConfigError, match="site address"):
            plan_from_stack(node_stack(), project="app")


class TestRegistration:
    def test_vps_is_a_registered_adapter(self) -> None:
        assert "vps_ssh" in adapter_ids()
        assert ADAPTERS["vps_ssh"] is VpsSshAdapter


# ─── Preflight ───────────────────────────────────────────────────────────────


class TestPreflight:
    def test_missing_host_is_actionable(self) -> None:
        pre = VpsSshAdapter(host="", runner=FakeRunner()).preflight()
        assert not pre.ready
        assert "OPENVAULT_VPS_HOST" in pre.blocker

    def test_unreachable_box_names_the_target(self) -> None:
        runner = FakeRunner(rules={"echo openvault-ok": RunResult(255, stderr="Permission denied")})
        pre = VpsSshAdapter(host="203.0.113.10", runner=runner).preflight()
        assert not pre.ready
        assert "203.0.113.10" in pre.blocker
        assert "Permission denied" in pre.blocker

    def test_non_root_without_sudo_refuses(self) -> None:
        runner = FakeRunner(uid="1000")
        pre = VpsSshAdapter(host="203.0.113.10", user="deploy", runner=runner).preflight()
        assert not pre.ready
        assert "sudo" in pre.blocker

    def test_ready_box_reports_what_it_found(self) -> None:
        runner = FakeRunner()
        pre = VpsSshAdapter(host="203.0.113.10", runner=runner).preflight()
        assert pre.ready
        assert pre.facts["docker"].startswith("Docker version")
        assert pre.facts["server_ip"] == "203.0.113.10"
        assert "bookworm" in pre.facts["os"]

    def test_missing_tools_on_non_apt_box_refuse_rather_than_pretend(self) -> None:
        runner = FakeRunner(
            docker="",
            caddy="",
            rules={"command -v apt-get": RunResult(0, "no\n")},
        )
        pre = VpsSshAdapter(host="203.0.113.10", runner=runner).preflight()
        assert not pre.ready
        assert "docker" in pre.blocker and "caddy" in pre.blocker


# ─── Deploy ──────────────────────────────────────────────────────────────────


class TestDeployRefusals:
    def test_no_stack_means_no_guessing(self, project: Path) -> None:
        adapter = VpsSshAdapter(host="203.0.113.10", runner=FakeRunner())
        result = adapter.deploy(project, project="app")
        assert not result.ok
        assert "detected stack" in result.detail

    def test_missing_local_directory_refuses(self, tmp_path: Path) -> None:
        adapter = VpsSshAdapter(host="203.0.113.10", runner=FakeRunner(), stack=node_stack())
        result = adapter.deploy(tmp_path / "nope", project="app")
        assert not result.ok
        assert "not a directory" in result.detail

    def test_failed_build_returns_the_log_not_a_url(self, project: Path) -> None:
        runner = FakeRunner(
            rules={"docker build": RunResult(1, stderr="npm ERR! missing script: build")}
        )
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=node_stack())
        result = adapter.deploy(project, project="app", hostname="app.example.com")
        assert not result.ok
        assert result.url == ""
        assert "npm ERR!" in result.detail
        assert not runner.ran("caddy reload")

    def test_unrunnable_stack_refuses_before_building(self, project: Path) -> None:
        runner = FakeRunner()
        stack = node_stack(start_command="", output_directory="")
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=stack)
        result = adapter.deploy(project, project="app", hostname="app.example.com")
        assert not result.ok
        assert "no start command" in result.detail
        assert not runner.ran("docker build")

    def test_unhealthy_replica_never_switches_traffic(self, project: Path) -> None:
        runner = FakeRunner(rules={"http://127.0.0.1:": RunResult(1, "down\n")})
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=node_stack())
        result = adapter.deploy(project, project="app", hostname="app.example.com")
        assert not result.ok
        assert "still serving" in result.detail
        assert not runner.ran("caddy reload")
        # And the half-started colour is cleaned up rather than left running.
        assert runner.ran("docker rm -f $(docker ps -aq --filter name=ov-app-")

    def test_proxy_rejecting_config_is_reported_as_traffic_unchanged(self, project: Path) -> None:
        runner = FakeRunner(rules={"caddy validate": RunResult(1, "invalid caddy config\n")})
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=node_stack())
        result = adapter.deploy(project, project="app", hostname="app.example.com")
        assert not result.ok
        assert "traffic unchanged" in result.detail

    def test_container_up_but_url_dead_is_a_failure(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The customer-facing layer is the assertion. A running container is not a site."""
        import httpx

        from openmw.openvault.ship.hosts import vps_ssh as mod

        def _boom(url: str, **kwargs: Any) -> Any:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(mod.httpx, "get", _boom)
        runner = FakeRunner(rules={"-H 'Host: app.example.com'": RunResult(0, "")})
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=node_stack())
        result = adapter.deploy(project, project="app", hostname="app.example.com")
        assert not result.ok
        assert result.url == ""


class TestDeployHappyPath:
    def _deploy(
        self,
        project: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        runner: FakeRunner,
        **kwargs: Any,
    ) -> Any:
        from openmw.openvault.ship.hosts import vps_ssh as mod

        monkeypatch.setattr(mod.httpx, "get", ok_probe())
        adapter = VpsSshAdapter(
            host="203.0.113.10", runner=runner, stack=kwargs.pop("stack", node_stack()), replicas=2
        )
        return adapter.deploy(project, project="app", hostname="app.example.com", **kwargs)

    def test_live_url_only_after_something_answered(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner()
        result = self._deploy(project, monkeypatch, runner=runner)
        assert result.ok
        assert result.url == "https://app.example.com"
        assert "2 replica(s)" in result.detail

    def test_both_replicas_end_up_behind_the_proxy(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner()
        self._deploy(project, monkeypatch, runner=runner)
        _, site, _ = runner.written("site.caddy")
        text = site.decode("utf-8")
        base = port_base("app", "green")  # nothing running -> first colour is green
        assert f"reverse_proxy 127.0.0.1:{base} 127.0.0.1:{base + 1}" in text
        assert runner.ran(f"-p 127.0.0.1:{base}:3000")
        assert runner.ran(f"-p 127.0.0.1:{base + 1}:3000")

    def test_old_colour_is_removed_only_after_the_switch(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner()
        self._deploy(project, monkeypatch, runner=runner)
        reload_at = next(i for i, s in enumerate(runner.scripts) if "caddy reload" in s)
        teardown_at = next(
            i for i, s in enumerate(runner.scripts) if "--filter name=ov-app-blue-" in s
        )
        assert teardown_at > reload_at

    def test_generated_dockerfile_lands_on_the_box(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner()
        result = self._deploy(project, monkeypatch, runner=runner)
        _, dockerfile, _ = runner.written("Dockerfile")
        assert b"FROM node:22" in dockerfile
        assert "generated Dockerfile" in result.detail

    def test_repo_dockerfile_wins_over_ours(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(rules={"/src/Dockerfile": RunResult(0, "yes\n")})
        result = self._deploy(project, monkeypatch, runner=runner)
        assert "repo Dockerfile" in result.detail
        assert not [p for p, _, _ in runner.puts if p.endswith("Dockerfile")]

    def test_static_site_is_served_as_files(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stack = node_stack(category="static", start_command="", output_directory="dist")
        runner = FakeRunner(rules={"test -d /srv/openvault/app/src/dist": RunResult(0, "yes\n")})
        result = self._deploy(project, monkeypatch, runner=runner, stack=stack)
        assert result.ok
        _, site, _ = runner.written("site.caddy")
        assert b"file_server" in site
        assert b"reverse_proxy" not in site


class TestSecretsAtShip:
    def test_values_reach_the_box_but_never_argv_logs_or_response(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openmw.openvault.ship.hosts import vps_ssh as mod

        monkeypatch.setattr(mod.httpx, "get", ok_probe())
        runner = FakeRunner()
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=node_stack())
        secret = "sk-live-do-not-leak-42"
        result = adapter.deploy(
            project,
            project="app",
            hostname="app.example.com",
            env={"STRIPE_KEY": secret},
        )
        assert result.ok

        path, body, mode = runner.written("/env")
        assert body == f"STRIPE_KEY={secret}\n".encode()
        assert mode == "600", "an env file readable by other users on the box is a leak"
        assert runner.ran(f"--env-file {path}")

        # Nothing that gets logged, echoed, or listed by `ps` may carry the value.
        assert not any(secret in s for s in runner.scripts)
        assert secret not in result.detail
        assert secret not in result.log
        assert secret not in str(result)

    def test_impossible_env_refuses_before_touching_the_box(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner()
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=node_stack())
        result = adapter.deploy(
            project, project="app", hostname="app.example.com", env={"K": "a\nb"}
        )
        assert not result.ok
        assert "newline" in result.detail
        assert not runner.ran("docker run")


# ─── Domain ──────────────────────────────────────────────────────────────────


class TestAttachDomain:
    def test_pointing_here_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openmw.openvault.ship.hosts import vps_ssh as mod

        monkeypatch.setattr(
            mod.socket,
            "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("203.0.113.10", 0))],
        )
        adapter = VpsSshAdapter(host="203.0.113.10", runner=FakeRunner())
        out = adapter.attach_domain(project="app", hostname="app.example.com")
        assert out.ok
        assert "203.0.113.10" in out.detail

    def test_pointing_elsewhere_hands_back_the_exact_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openmw.openvault.ship.hosts import vps_ssh as mod

        monkeypatch.setattr(
            mod.socket,
            "getaddrinfo",
            lambda *a, **k: [(2, 1, 6, "", ("198.51.100.7", 0))],
        )
        adapter = VpsSshAdapter(host="203.0.113.10", runner=FakeRunner())
        out = adapter.attach_domain(project="app", hostname="app.example.com")
        assert not out.ok
        assert out.required_records == [
            {
                "type": "A",
                "name": "app.example.com",
                "value": "203.0.113.10",
                "note": (
                    "Caddy requests the TLS certificate on the first request after "
                    "this resolves"
                ),
            }
        ]

    def test_unresolvable_domain_still_teaches_the_record(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openmw.openvault.ship.hosts import vps_ssh as mod

        def _fail(*a: Any, **k: Any) -> Any:
            raise OSError("Name or service not known")

        monkeypatch.setattr(mod.socket, "getaddrinfo", _fail)
        adapter = VpsSshAdapter(host="203.0.113.10", runner=FakeRunner())
        out = adapter.attach_domain(project="app", hostname="app.example.com")
        assert not out.ok
        assert out.required_records[0]["type"] == "A"

    def test_bad_hostname_refused(self) -> None:
        adapter = VpsSshAdapter(host="203.0.113.10", runner=FakeRunner())
        assert not adapter.attach_domain(project="app", hostname="not a host").ok


class TestFromVault:
    def test_vault_supplies_a_key_path_never_key_material(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENVAULT_VPS_KEY", raising=False)
        monkeypatch.setenv("OPENVAULT_VPS_HOST", "203.0.113.10")
        material = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n"
        adapter = from_vault(lambda _p: material)
        assert adapter.target.key_path == "", "private key material must not become a path"

        adapter = from_vault(lambda _p: "~/.ssh/id_openvault")
        assert adapter.target.key_path == "~/.ssh/id_openvault"
        assert adapter.target.host == "203.0.113.10"


class TestStatus:
    def test_status_reads_the_box_not_our_memory(self) -> None:
        runner = FakeRunner(
            rules={"docker ps": RunResult(0, "ov-app-green-0\tUp 3 minutes\tov-app:171\n")}
        )
        out = VpsSshAdapter(host="203.0.113.10", runner=runner).status("App")
        assert out["replicas"] == [
            {"name": "ov-app-green-0", "status": "Up 3 minutes", "image": "ov-app:171"}
        ]


class TestNoShellInjection:
    """Every remote script must be free of raw, unquoted user input."""

    def test_site_file_path_is_confined_to_our_directory(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from openmw.openvault.ship.hosts import vps_ssh as mod

        monkeypatch.setattr(mod.httpx, "get", ok_probe())
        runner = FakeRunner()
        adapter = VpsSshAdapter(host="203.0.113.10", runner=runner, stack=node_stack())
        adapter.deploy(project, project="../../etc/passwd", hostname="app.example.com")
        for script in runner.scripts:
            assert "/etc/passwd" not in script
        assert any(
            re.search(rf"{re.escape(CADDY_SITE_DIR)}/[a-z0-9-]+\.caddy", s) for s in runner.scripts
        )

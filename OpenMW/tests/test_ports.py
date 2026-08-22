"""Port custody: who is on our port, and which port did the operator pick.

The behaviour under test is the one the operator actually experiences: a real
socket is bound, and the assertions are on the message they are shown and the
file that persists their choice - not on a mocked psutil. A test that mocks the
process lookup would pass on a machine where the lookup does not work.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from openmw.openvault import ports


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never read or write the developer's real ports.json."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OPENVAULT_HOME", str(home))
    for service in ports.SERVICES:
        monkeypatch.delenv(service.env_var, raising=False)
    return home


def _bind(port: int = 0) -> socket.socket:
    """A listener standing in for somebody else's application."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    return srv


class TestNamingTheBlocker:
    def test_a_busy_port_names_the_application_holding_it(self) -> None:
        """ "Port busy" is not actionable; a name and a path are.

        This is the whole point of the feature: the operator has to know what
        to close.
        """
        srv = _bind()
        try:
            port = srv.getsockname()[1]
            listener = ports.describe_listener(port)
            assert listener is not None, "a bound port must not report as free"
            assert listener.pid, "no pid means the operator still cannot act"
            assert listener.name, "no process name means the operator still cannot act"
            assert listener.exe, "the path is what disambiguates two python.exe"
        finally:
            srv.close()

    def test_a_free_port_is_reported_free(self) -> None:
        srv = _bind()
        port = srv.getsockname()[1]
        srv.close()
        assert ports.describe_listener(port) is None

    def test_the_message_tells_the_operator_what_to_close_and_how_to_move(self) -> None:
        srv = _bind()
        try:
            port = srv.getsockname()[1]
            status = ports.PortStatus(
                service=ports.SERVICES_BY_KEY["api"],
                port=port,
                state="foreign",
                listener=ports.describe_listener(port),
            )
            message = ports.blocking_message(status)
            assert str(port) in message
            assert "Blocking application" in message
            assert "openvault ports --set api=" in message, (
                "naming the blocker without offering the fix leaves them stuck"
            )
        finally:
            srv.close()


class TestOurOwnServerIsNotABlocker:
    def test_an_unrecognised_listener_on_our_port_is_foreign(self) -> None:
        srv = _bind(0)
        try:
            port = srv.getsockname()[1]
            status = ports.inspect("api", override=port)
            assert status.state == "foreign"
            assert status.blocked is True
        finally:
            srv.close()

    def test_an_external_services_own_port_is_never_reported_as_blocked(self) -> None:
        """Cortex on :8010 is Cortex doing its job, not a conflict.

        A control that refuses legitimate work is a failure, not a win
        (R-0005). OpenVault reports on other products' ports; it does not
        treat them as intruders or reconfigure them.
        """
        srv = _bind()
        try:
            port = srv.getsockname()[1]
            status = ports.inspect("cortex", override=port)
            assert status.blocked is False
        finally:
            srv.close()


class TestThePortChoicePersists:
    def test_a_saved_port_survives_and_is_used_next_time(self, isolated_home: Path) -> None:
        assert ports.resolve_port("api") == 5000
        written = ports.set_port("api", 5050)
        assert written.is_file()
        assert ports.resolve_port("api") == 5050, "the choice did not persist"
        saved = json.loads(written.read_text(encoding="utf-8"))
        assert saved["ports"]["api"] == 5050

    def test_precedence_explicit_beats_env_beats_saved_beats_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A one-off --port must not silently rewrite a saved preference."""
        assert ports.resolve_port("api") == 5000
        ports.set_port("api", 5050)
        assert ports.resolve_port("api") == 5050
        monkeypatch.setenv("OPENVAULT_API_PORT", "5060")
        assert ports.resolve_port("api") == 5060
        assert ports.resolve_port("api", override=5070) == 5070
        # and the saved choice is untouched by either
        monkeypatch.delenv("OPENVAULT_API_PORT")
        assert ports.resolve_port("api") == 5050

    def test_a_port_we_do_not_own_cannot_be_reconfigured(self) -> None:
        """PRODUCT_ROLES: Cortex and AirGPT are other repos."""
        with pytest.raises(ValueError, match="not ours to reconfigure"):
            ports.set_port("cortex", 9010)

    def test_an_out_of_range_port_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1-65535"):
            ports.set_port("api", 99999)

    def test_a_corrupt_ports_file_falls_back_to_defaults_instead_of_failing_to_boot(
        self, isolated_home: Path
    ) -> None:
        """A bad settings file must not be able to stop the stack starting."""
        ports.ports_file().write_text("{ this is not json", encoding="utf-8")
        assert ports.resolve_port("api") == 5000

    def test_a_non_numeric_env_var_is_ignored_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENVAULT_API_PORT", "not-a-port")
        assert ports.resolve_port("api") == 5000


def test_every_service_declares_whether_we_may_change_its_port() -> None:
    """An external service must say why it is fixed, or the refusal is opaque."""
    for service in ports.SERVICES:
        if service.owner == "external":
            assert service.fixed_reason, f"{service.key} refuses silently"
        assert service.env_var and service.default_port


class TestTheExitCodeContractTheLaunchersDependOn:
    """`openvault up` decides whether to abort by shelling out to this command.

    If the exit code stopped meaning "blocked", the launcher would go back to
    silently adopting a stranger's server - the bug this whole module exists
    to stop - and nothing else would notice.
    """

    def _run_ports(self, home: Path) -> tuple[int, str]:
        import os
        import subprocess
        import sys

        env = {**os.environ, "OPENVAULT_HOME": str(home)}
        proc = subprocess.run(
            [sys.executable, "-m", "openmw.cli", "ports"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_exit_is_zero_when_nothing_foreign_holds_our_ports(self, isolated_home: Path) -> None:
        code, out = self._run_ports(isolated_home)
        assert code == 0, out

    def test_exit_is_nonzero_and_names_the_app_when_a_stranger_holds_our_port(
        self, isolated_home: Path
    ) -> None:
        srv = _bind()
        try:
            port = srv.getsockname()[1]
            ports.set_port("api", port)
            code, out = self._run_ports(isolated_home)
            assert code != 0, "a blocked port must fail, or the launcher proceeds anyway"
            assert "BLOCKED" in out
            assert "Blocking application" in out
            assert "python" in out.lower(), "the blocking app must be named"
        finally:
            srv.close()


def test_the_launcher_resolver_agrees_with_the_package(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apps/cli/openvault_cli.py duplicates resolve_port, so pin them together.

    The launcher runs on the system Python and cannot import openmw, so the
    duplication is deliberate. What is not acceptable is the two drifting: the
    launcher would start the API on one port while the console bound another,
    and the failure would look like "the backend is down".
    """
    import importlib.util

    cli_path = Path(__file__).resolve().parents[2] / "apps" / "cli" / "openvault_cli.py"
    assert cli_path.is_file(), cli_path

    for env_value, saved in ((None, None), (None, 5051), ("5052", None), ("5053", 5054)):
        monkeypatch.delenv("OPENVAULT_API_PORT", raising=False)
        ports.ports_file().unlink(missing_ok=True)
        if saved is not None:
            ports.set_port("api", saved)
        if env_value is not None:
            monkeypatch.setenv("OPENVAULT_API_PORT", env_value)

        spec = importlib.util.spec_from_file_location("openvault_cli_ports", cli_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert ports.resolve_port("api") == module.API_PORT, (
            f"launcher resolved {module.API_PORT} but the package resolved "
            f"{ports.resolve_port('api')} for env={env_value} saved={saved}"
        )


def _launcher_module():
    """Load apps/cli/openvault_cli.py by path (it is not an installed package)."""
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "apps" / "cli" / "openvault_cli.py"
    spec = importlib.util.spec_from_file_location("openvault_cli_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheLauncherResolverAgreesWithThePackage:
    """`openvault up` cannot import openmw, so it has its own tiny resolver.

    Duplication is only acceptable while something proves the two agree. Left
    ungated, the saved port would take effect for `openmw console` and silently
    not for `openvault up`, which is the worst of both: a setting that works
    somewhere.
    """

    @pytest.mark.parametrize("saved", [None, 5050, 6001])
    @pytest.mark.parametrize("env_value", [None, "5060"])
    def test_both_resolvers_return_the_same_port(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        saved: int | None,
        env_value: str | None,
    ) -> None:
        launcher = _launcher_module()
        if saved is not None:
            ports.set_port("api", saved)
        if env_value is not None:
            monkeypatch.setenv("OPENVAULT_API_PORT", env_value)

        from_package = ports.resolve_port("api")
        from_launcher = launcher._resolve_port("api", "OPENVAULT_API_PORT", 5000)
        assert from_launcher == from_package, (
            f"launcher resolved {from_launcher}, package resolved {from_package} "
            f"(saved={saved}, env={env_value})"
        )

    def test_the_launcher_reads_the_same_file_the_package_writes(self, isolated_home: Path) -> None:
        """With OPENVAULT_HOME set - which every launcher does before spawning.

        Unset, the two disagree by design: the package defaults to
        ~/.openvault and the launcher to <repo>/.openvault. That is the open
        two-vault-home question, not something this test can paper over.
        """
        launcher = _launcher_module()
        ports.set_port("api", 5099)
        assert launcher._home_dir() == isolated_home
        assert launcher._resolve_port("api", "OPENVAULT_API_PORT", 5000) == 5099

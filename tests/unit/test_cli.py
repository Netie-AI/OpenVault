"""CLI tests via Typer CliRunner — real MockNvmeAdapter through factory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import ModuleType

import pytest
import typer
from typer.testing import CliRunner

import nvme_sentinel.cli as cli_module
from nvme_sentinel.cli import (
    _elevated_cli_parameters,
    _require_admin_or_elevate,
    _resolve_output_paths_in_argv,
    app,
)

runner = CliRunner()


def test_demo_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_demo_creates_report(tmp_path: Path) -> None:
    runner.invoke(app, ["demo", "--output-dir", str(tmp_path)])
    assert (tmp_path / "demo.html").exists()


def test_demo_output_contains_keywords(tmp_path: Path) -> None:
    result = runner.invoke(app, ["demo", "--output-dir", str(tmp_path)])
    out = result.output
    assert "SMART" in out
    assert "Temperature" in out
    assert "DEMO COMPLETE" in out


def test_smart_mock_exits_zero() -> None:
    result = runner.invoke(app, ["smart", "--mock"])
    assert result.exit_code == 0


def test_smart_mock_json() -> None:
    result = runner.invoke(app, ["smart", "--mock", "--json"])
    assert result.exit_code == 0
    json.loads(result.stdout)


def test_info_mock_exits_zero() -> None:
    result = runner.invoke(app, ["info", "--mock"])
    assert result.exit_code == 0


def test_info_contains_model() -> None:
    result = runner.invoke(app, ["info", "--mock"])
    assert "Generic NVMe SSD" in result.output


def test_demo_report_html_content(tmp_path: Path) -> None:
    runner.invoke(app, ["demo", "--output-dir", str(tmp_path)])
    html = (tmp_path / "demo.html").read_text(encoding="utf-8")
    assert "nvme-sentinel" in html
    assert "SMART Health Log" in html
    assert "HEALTHY" in html


def test_smart_mock_with_output(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    result = runner.invoke(app, ["smart", "--mock", "--output", str(report)])
    assert result.exit_code == 0
    assert report.exists()
    assert report.stat().st_size > 1000


def test_smart_mock_shows_telemetry_source() -> None:
    result = runner.invoke(app, ["smart", "--mock"])
    assert result.exit_code == 0
    assert "Telemetry source:" in result.output
    assert "mock" in result.output


def test_collect_mock(tmp_path: Path) -> None:
    out = tmp_path / "snap.json"
    result = runner.invoke(
        app,
        ["collect", "--device", "/dev/mock-nvme0", "--mock", "--output", str(out)],
    )
    assert result.exit_code == 0
    assert out.exists()
    assert "Read-only" in result.output


def test_list_devices_exits_zero() -> None:
    result = runner.invoke(app, ["list-devices"])
    assert result.exit_code == 0


@pytest.mark.windows_only
def test_resolve_output_paths_in_argv_makes_absolute(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / "reports").mkdir(parents=True)
    argv = [
        "collect",
        "--device",
        r"\\.\PhysicalDrive1",
        "--output",
        r"reports\baseline.json",
    ]
    resolved = _resolve_output_paths_in_argv(argv, base=project_root)
    out_idx = resolved.index("--output") + 1
    expected = str((project_root / "reports" / "baseline.json").resolve())
    assert resolved[out_idx] == expected
    assert Path(resolved[out_idx]).is_absolute()


@pytest.mark.windows_only
def test_resolve_output_paths_in_argv_matches_output_resolve(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / "reports").mkdir(parents=True)
    relative = Path(r"..\..\somewhere\baseline.json")
    argv = ["collect", "-o", str(relative)]
    from_argv = _resolve_output_paths_in_argv(argv, base=project_root)[2]
    from_path = (project_root / relative).resolve()
    assert Path(from_argv) == from_path


def test_elevated_cli_parameters_quotes_paths_with_spaces() -> None:
    params = _elevated_cli_parameters(
        [
            "collect",
            "--device",
            r"\\.\PhysicalDrive1",
            "--output",
            r"C:\Users\oojia\NVME Sentinel\reports\baseline_biwin_after.json",
        ]
    )
    assert (
        '--output "C:\\Users\\oojia\\NVME Sentinel\\reports\\baseline_biwin_after.json"' in params
    )
    assert r"\\.\PhysicalDrive1" in params


@pytest.mark.windows_only
def test_elevation_relaunch_uses_resolved_argv_and_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    (project_root / "reports").mkdir(parents=True)
    monkeypatch.chdir(project_root)
    cli_sys: ModuleType = cli_module.sys  # type: ignore[attr-defined]
    cli_ctypes: ModuleType = cli_module.ctypes  # type: ignore[attr-defined]
    cli_typer: ModuleType = cli_module.typer  # type: ignore[attr-defined]
    monkeypatch.setattr(cli_sys, "platform", "win32")
    monkeypatch.setattr(
        cli_sys,
        "executable",
        str(project_root / "python.exe"),
    )
    monkeypatch.setattr(
        cli_sys,
        "argv",
        [
            "nvme-sentinel",
            "collect",
            "-d",
            r"\\.\PhysicalDrive1",
            "-o",
            r"reports\baseline.json",
        ],
    )

    class _FakeShell32:
        @staticmethod
        def IsUserAnAdmin() -> bool:
            return False

    class _FakeWindll:
        shell32 = _FakeShell32()

    monkeypatch.setattr(cli_ctypes, "windll", _FakeWindll())

    captured: list[dict[str, object]] = []

    def _fake_run_elevated(**kwargs: object) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(cli_module, "_run_elevated_windows", _fake_run_elevated)
    monkeypatch.setattr(cli_typer, "prompt", lambda *args, **kwargs: "y")

    with pytest.raises(typer.Exit) as exc_info:
        _require_admin_or_elevate(r"\\.\PhysicalDrive1")

    assert exc_info.value.exit_code == 0
    assert len(captured) == 1
    call = captured[0]
    expected_output = str((project_root / "reports" / "baseline.json").resolve())
    assert call["executable"] == str(project_root / "python.exe")
    assert call["working_directory"] == os.fspath(project_root)
    argv = call["argv"]
    assert isinstance(argv, list)
    assert argv[argv.index("-o") + 1] == expected_output
    params = _elevated_cli_parameters(argv)
    assert expected_output in params
    assert " -o reports\\baseline.json" not in params
    assert " -o reports/baseline.json" not in params


def test_smart_host_proxy(tmp_path: Path) -> None:
    import base64

    from nvme_sentinel.snapshot.schema import DeviceSnapshot
    from nvme_sentinel.telemetry.source import TelemetrySource

    identify = Path("tests/fixtures/identify_ctrl_generic.bin").read_bytes()
    smart = Path("tests/fixtures/smart_healthy.bin").read_bytes()
    snap = DeviceSnapshot(
        device_path="/dev/mock",
        platform="linux",
        telemetry_source=TelemetrySource.MOCK,
        identify_controller_b64=base64.standard_b64encode(identify).decode("ascii"),
        smart_health_b64=base64.standard_b64encode(smart).decode("ascii"),
    )
    path = tmp_path / "proxy.json"
    path.write_text(snap.model_dump_json(), encoding="utf-8")
    result = runner.invoke(app, ["smart", "--device", f"host-proxy://{path}"])
    assert result.exit_code == 0
    assert "host-proxy" in result.output

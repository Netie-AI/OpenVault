"""CLI tests via Typer CliRunner — real MockNvmeAdapter through factory."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nvme_sentinel.cli import app

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

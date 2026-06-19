"""OpenMW CLI smoke tests — mocked detect() and path trace, no GPU/NVMe required."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from openmw.cli import app
from openmw.device_profile import DeviceProfile

runner = CliRunner()

_FAKE_PROFILE = DeviceProfile(
    gpu_name="Fake RTX 4090",
    gpu_vram_gb=24.0,
    gpu_bandwidth_gbps=1000.0,
    system_ram_gb=64.0,
    cpu_cores=16,
    nvme_model="Fake NVMe 990 Pro",
    nvme_seq_read_gbps=7.0,
    nvme_endurance_tbw=1200.0,
    unified_memory=False,
    cpu_inference_mode=False,
)


def test_doctor_writes_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "doctor_out"
    with patch("openmw.cli.detect", return_value=_FAKE_PROFILE):
        result = runner.invoke(app, ["doctor", "--out", str(out_dir)])

    assert result.exit_code == 0, result.output
    profile_path = out_dir / "profile.json"
    report_path = out_dir / "bottleneck_report.html"
    assert profile_path.exists()
    assert report_path.exists()

    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert payload["gpu_name"] == "Fake RTX 4090"
    assert payload["nvme_seq_read_gbps"] == 7.0
    assert "Fake RTX 4090" in result.output


def test_doctor_json_only_skips_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "should_not_exist"
    with patch("openmw.cli.detect", return_value=_FAKE_PROFILE):
        result = runner.invoke(app, ["doctor", "--out", str(out_dir), "--json"])

    assert result.exit_code == 0, result.output
    assert not out_dir.exists()
    payload = json.loads(result.output)
    assert payload["gpu_vram_gb"] == 24.0


def test_route_known_model_human_output() -> None:
    with patch("openmw.cli.detect", return_value=_FAKE_PROFILE):
        result = runner.invoke(app, ["route", "llama-3.3-8b"])

    assert result.exit_code == 0, result.output
    assert "Model:     llama-3.3-8b" in result.output
    assert "Strategy:" in result.output


def test_route_known_model_json_output() -> None:
    with patch("openmw.cli.detect", return_value=_FAKE_PROFILE):
        result = runner.invoke(app, ["route", "llama-3.3-8b", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["model_id"] == "llama-3.3-8b"
    assert payload["gpu_layers"] + payload["cpu_ram_layers"] + payload["nvme_layers"] > 0


def test_route_unknown_model_exits_nonzero() -> None:
    with patch("openmw.cli.detect", return_value=_FAKE_PROFILE):
        result = runner.invoke(app, ["route", "totally-fake-model-xyz"])

    assert result.exit_code == 1
    assert "unknown model_id" in result.output


def test_train_stub_exits_two(tmp_path: Path) -> None:
    fake_dataset = tmp_path / "data.jsonl"
    fake_dataset.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["train", "--dataset", str(fake_dataset)])

    assert result.exit_code == 2
    assert "not yet implemented" in result.output
    assert "training_router.py" in result.output


def test_infer_stub_exits_two() -> None:
    result = runner.invoke(app, ["infer", "--model", "llama-3.3-8b"])

    assert result.exit_code == 2
    assert "not yet implemented" in result.output
    assert "PART 9" in result.output


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for cmd in ("doctor", "route", "train", "infer"):
        assert cmd in result.output

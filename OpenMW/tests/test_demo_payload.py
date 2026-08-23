"""Tests for OpenMW demo payload builder (API sample for apps/web)."""

from __future__ import annotations

import json
from pathlib import Path

from openmw.demo_payload import build_demo_payload, openvault_app_dir, write_demo_payload


def test_openvault_app_dir_exists() -> None:
    app = openvault_app_dir()
    assert (app / "package.json").is_file()
    assert "@openvault/web" in (app / "package.json").read_text(encoding="utf-8")


def test_build_demo_payload_mock_profile() -> None:
    payload = build_demo_payload(use_live_detect=False, model_id="llama-3.3-8b")
    assert payload["schema_version"] == "1"
    assert len(payload["devices"]) == 3
    assert payload["routing"]["model_id"] == "llama-3.3-8b"
    assert payload["middleware_comparison"]["available"] is False
    assert payload["middleware_comparison"]["speedup_pct"] is None
    assert payload["path_trace"]["bottleneck_hop"]
    assert any(h.get("is_synthetic") for h in payload["path_trace"]["hop_timeline"])


def test_write_demo_payload(tmp_path: Path) -> None:
    out = write_demo_payload(tmp_path, use_live_detect=False)
    assert out.name == "demo.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["data_flow"]
    assert any(h["is_bottleneck"] for h in data["data_flow"])

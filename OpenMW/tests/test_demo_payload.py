"""Tests for OpenMW WebUI demo payload builder."""

from __future__ import annotations

import json
from pathlib import Path

from openmw.demo_payload import build_demo_payload, bundled_webui_dir, write_demo_payload


def test_bundled_webui_index_exists() -> None:
    index = bundled_webui_dir() / "index.html"
    assert index.is_file()
    assert "OpenMW" in index.read_text(encoding="utf-8")


def test_build_demo_payload_mock_profile() -> None:
    payload = build_demo_payload(use_live_detect=False, model_id="llama-3.3-8b")
    assert payload["schema_version"] == "1"
    assert len(payload["devices"]) == 3
    assert payload["routing"]["model_id"] == "llama-3.3-8b"
    assert payload["middleware_comparison"]["speedup_pct"] > 0
    assert payload["middleware_comparison"]["optimized_tok_s"] > payload["middleware_comparison"]["baseline_tok_s"]
    assert payload["path_trace"]["bottleneck_hop"]


def test_write_demo_payload(tmp_path: Path) -> None:
    out = write_demo_payload(tmp_path, use_live_detect=False)
    assert out.name == "demo.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["data_flow"]
    assert any(h["is_bottleneck"] for h in data["data_flow"])

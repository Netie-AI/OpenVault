"""Integration smoke checks for mock adapter availability."""

from __future__ import annotations

from pathlib import Path

from nvme_sentinel.adapters.mock import MockNvmeAdapter


def test_integration_mock_adapter_smoke() -> None:
    """Mock adapter can be constructed from generated fixture paths."""
    fixtures = Path("tests/fixtures")
    adapter = MockNvmeAdapter(
        identify_ctrl_path=fixtures / "identify_ctrl_generic.bin",
        identify_ns_path=fixtures / "identify_ns.bin",
        smart_path=fixtures / "smart_healthy.bin",
    )
    assert adapter.is_nvme() is True

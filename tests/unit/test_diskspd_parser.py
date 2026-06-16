"""diskspd XML parser tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from nvme_sentinel.stress.diskspd import parse_diskspd_xml


def test_parse_diskspd_xml_sample() -> None:
    xml_text = Path("tests/fixtures/diskspd_output_sample.xml").read_text(encoding="utf-8")
    result = parse_diskspd_xml(xml_text, "seq_read")
    assert result.tool == "diskspd"
    assert result.read_iops == 100000.0
    assert result.read_bw_mib_s == pytest.approx(400.0, rel=0.01)
    assert result.read_lat_ns_p99 == 800000.0

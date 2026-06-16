"""fio JSON parser tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nvme_sentinel.stress.fio import parse_fio_json


def test_parse_fio_json_sample() -> None:
    raw = json.loads(Path("tests/fixtures/fio_output_sample.json").read_text(encoding="utf-8"))
    result = parse_fio_json(raw, "rand_read_4k")
    assert result.tool == "fio"
    assert result.profile_name == "rand_read_4k"
    assert result.read_iops == 125000.5
    assert result.read_bw_mib_s == pytest.approx(500.0)
    assert result.read_lat_ns_p99 == 890000.0
    assert result.total_errors == 0

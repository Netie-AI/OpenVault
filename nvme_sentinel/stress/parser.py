"""Shared stress result schema for fio and diskspd parsers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StressResult:
    """Normalized benchmark output from fio or diskspd."""

    profile_name: str
    tool: str
    read_iops: float
    write_iops: float
    read_bw_mib_s: float
    write_bw_mib_s: float
    read_lat_ns_p50: float
    read_lat_ns_p99: float
    read_lat_ns_p99_99: float
    write_lat_ns_p50: float
    write_lat_ns_p99: float
    write_lat_ns_p99_99: float
    total_errors: int
    raw: Mapping[str, object]

"""fio JSON output parser and runner."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from nvme_sentinel.hal.exceptions import CapabilityError
from nvme_sentinel.stress.parser import StressResult
from nvme_sentinel.stress.profiles import JobProfile


def parse_fio_json(payload: Mapping[str, object], profile_name: str) -> StressResult:
    """Parse fio --output-format=json into StressResult."""
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("fio JSON missing jobs array")
    job0 = jobs[0]
    if not isinstance(job0, dict):
        raise ValueError("fio job entry must be object")

    read = job0.get("read", {})
    write = job0.get("write", {})
    if not isinstance(read, dict):
        read = {}
    if not isinstance(write, dict):
        write = {}

    def _iops(side: Mapping[str, object]) -> float:
        val = side.get("iops")
        return float(val) if isinstance(val, (int, float)) else 0.0

    def _bw_mib(side: Mapping[str, object]) -> float:
        # fio reports KiB/s in bw
        val = side.get("bw")
        if isinstance(val, (int, float)):
            return float(val) / 1024.0
        return 0.0

    def _lat_ns(side: Mapping[str, object], pct: str) -> float:
        clat = side.get("clat_ns")
        if not isinstance(clat, dict):
            return 0.0
        percentiles = clat.get("percentile")
        if not isinstance(percentiles, dict):
            return 0.0
        val = percentiles.get(pct)
        return float(val) if isinstance(val, (int, float)) else 0.0

    total_errors = 0
    err_val = job0.get("error")
    if isinstance(err_val, int):
        total_errors = err_val

    return StressResult(
        profile_name=profile_name,
        tool="fio",
        read_iops=_iops(read),
        write_iops=_iops(write),
        read_bw_mib_s=_bw_mib(read),
        write_bw_mib_s=_bw_mib(write),
        read_lat_ns_p50=_lat_ns(read, "50.000000"),
        read_lat_ns_p99=_lat_ns(read, "99.000000"),
        read_lat_ns_p99_99=_lat_ns(read, "99.990000"),
        write_lat_ns_p50=_lat_ns(write, "50.000000"),
        write_lat_ns_p99=_lat_ns(write, "99.000000"),
        write_lat_ns_p99_99=_lat_ns(write, "99.990000"),
        total_errors=total_errors,
        raw=payload,
    )


class FioRunner:
    """Run fio with a JobProfile and parse JSON output."""

    def __init__(self, fio_binary: str = "fio") -> None:
        self.fio_binary = fio_binary

    def ensure_available(self) -> None:
        if shutil.which(self.fio_binary) is None:
            raise CapabilityError(f"fio binary not found: {self.fio_binary}")

    def run(self, device_path: str, profile: JobProfile, output_dir: Path) -> StressResult:
        self.ensure_available()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{profile.name}.json"
        ioengine = "libaio" if sys.platform == "linux" else "windowsaio"
        args = [
            self.fio_binary,
            f"--name={profile.name}",
            f"--filename={device_path}",
            f"--rw={profile.rw}",
            f"--bs={profile.block_size_kb}k",
            f"--iodepth={profile.io_depth}",
            f"--numjobs={profile.num_jobs}",
            "--time_based",
            f"--runtime={profile.duration_sec}",
            f"--direct={1 if profile.direct else 0}",
            f"--ioengine={ioengine}",
            "--group_reporting",
            "--output-format=json",
            f"--output={out_file}",
        ]
        if profile.rw == "randrw" and profile.read_percent is not None:
            args.append(f"--rwmixread={profile.read_percent}")
        subprocess.run(
            args,
            check=True,
            timeout=profile.duration_sec + 60,
        )
        payload = json.loads(out_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("fio output root must be object")
        return parse_fio_json(payload, profile.name)

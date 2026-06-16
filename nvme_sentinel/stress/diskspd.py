"""diskspd XML output parser and runner."""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

from nvme_sentinel.hal.exceptions import CapabilityError
from nvme_sentinel.stress.parser import StressResult
from nvme_sentinel.stress.profiles import JobProfile


def parse_diskspd_xml(xml_text: str, profile_name: str) -> StressResult:
    """Parse diskspd -Rxml output into StressResult."""
    root = ET.fromstring(xml_text)
    target = root.find(".//Target")
    if target is None:
        raise ValueError("diskspd XML missing Target")

    def _float_attr(elem: ET.Element | None, name: str) -> float:
        if elem is None:
            return 0.0
        val = elem.get(name)
        if val is None:
            return 0.0
        try:
            return float(val)
        except ValueError:
            return 0.0

    read_iops = _float_attr(target, "ReadIops")
    write_iops = _float_attr(target, "WriteIops")
    read_bw = _float_attr(target, "ReadBytesSec") / (1024.0 * 1024.0)
    write_bw = _float_attr(target, "WriteBytesSec") / (1024.0 * 1024.0)

    def _percentile(pct: str) -> tuple[float, float]:
        bucket = root.find(f".//Latency/Bucket[@percentile='{pct}']")
        if bucket is None:
            return 0.0, 0.0
        read_ns = _float_attr(bucket, "ReadLatency")
        write_ns = _float_attr(bucket, "WriteLatency")
        return read_ns, write_ns

    r50, w50 = _percentile("50.000000")
    r99, w99 = _percentile("99.000000")
    r9999, w9999 = _percentile("99.990000")

    raw: Mapping[str, object] = {"xml_root_tag": root.tag}

    return StressResult(
        profile_name=profile_name,
        tool="diskspd",
        read_iops=read_iops,
        write_iops=write_iops,
        read_bw_mib_s=read_bw,
        write_bw_mib_s=write_bw,
        read_lat_ns_p50=r50,
        read_lat_ns_p99=r99,
        read_lat_ns_p99_99=r9999,
        write_lat_ns_p50=w50,
        write_lat_ns_p99=w99,
        write_lat_ns_p99_99=w9999,
        total_errors=0,
        raw=raw,
    )


class DiskspdRunner:
    """Run diskspd with a JobProfile and parse XML output."""

    def __init__(self, diskspd_binary: str = "diskspd") -> None:
        self.diskspd_binary = diskspd_binary

    def ensure_available(self) -> None:
        if shutil.which(self.diskspd_binary) is None:
            raise CapabilityError(f"diskspd binary not found: {self.diskspd_binary}")

    def run(self, device_path: str, profile: JobProfile, output_dir: Path) -> StressResult:
        self.ensure_available()
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"{profile.name}.xml"
        write_pct = 100
        if profile.rw == "read":
            write_pct = 0
        elif profile.rw == "randrw" and profile.read_percent is not None:
            write_pct = 100 - profile.read_percent

        args: list[str] = [
            self.diskspd_binary,
            f"-b{profile.block_size_kb}K",
            f"-d{profile.duration_sec}",
            f"-t{profile.num_jobs}",
            f"-o{profile.io_depth}",
            f"-w{write_pct}",
            "-Sh",
            "-L",
            "-Rxml",
            device_path,
        ]
        if profile.rw in ("randread", "randwrite", "randrw"):
            args.insert(1, "-r")

        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=profile.duration_sec + 60,
            check=True,
        )
        xml_text = proc.stdout.decode(errors="replace")
        out_file.write_text(xml_text, encoding="utf-8")
        return parse_diskspd_xml(xml_text, profile.name)

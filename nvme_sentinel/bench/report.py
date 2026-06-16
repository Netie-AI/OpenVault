"""Bench run reporting: HTML artifact for clone-and-verify workflow."""

from __future__ import annotations

import html
import json
from pathlib import Path

from nvme_sentinel.bench.schema import BenchRunReport
from nvme_sentinel.telemetry.source import TelemetrySource, is_native_passthrough


def _degraded_telemetry_banner(report: BenchRunReport) -> str:
    """Warn when wear delta cannot reflect real host writes (non-native telemetry)."""
    sources: list[TelemetrySource] = []
    for snap in (report.snapshot_before, report.snapshot_after):
        if (
            not is_native_passthrough(snap.telemetry_source)
            and snap.smart_health is None
            and snap.telemetry_source not in sources
        ):
            sources.append(snap.telemetry_source)
    if not sources:
        return ""
    labels = ", ".join(html.escape(src.label()) for src in sources)
    return f"""
  <div class="banner degraded">
    <strong>Degraded telemetry — wear delta unavailable.</strong>
    Snapshots used {labels}, which does not expose NVMe Data Units Written
    (SMART / Health log page 0x02). A flat zero host-write total here is honest given
    the input, not a measurement failure. Quantified wear accounting requires native NVMe
    admin passthrough (Linux ioctl or Windows DeviceIoControl with stornvme.sys).
  </div>
"""


def render_bench_run_report(report: BenchRunReport, output_path: Path) -> None:
    """Write standalone HTML summarizing wear delta and environment manifest."""
    wd = report.wear_delta
    env = report.env_manifest
    # NVMe Base Spec 2.0c: Data Units Written reported in units of 1000 x 512 bytes.
    host_bytes_gb = wd.tbw_bytes_estimate / 1e9
    host_bytes_tb = wd.tbw_bytes_estimate / 1e12
    stress_block = ""
    if report.stress_result is not None:
        sr = report.stress_result
        stress_block = f"""
        <h2>Stress ({html.escape(sr.tool)} / {html.escape(sr.profile_name)})</h2>
        <ul>
          <li>Read IOPS: {sr.read_iops:.1f}</li>
          <li>Write IOPS: {sr.write_iops:.1f}</li>
          <li>Read BW: {sr.read_bw_mib_s:.2f} MiB/s</li>
          <li>Write BW: {sr.write_bw_mib_s:.2f} MiB/s</li>
        </ul>
        """
    degraded_banner = _degraded_telemetry_banner(report)
    host_writes_line = (
        f"<strong>{host_bytes_gb:.2f} GB ({host_bytes_tb:.6f} TB)</strong>"
        f" ({wd.data_units_written_delta:,} data units written Δ)"
    )

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>nvme-sentinel BenchRunReport</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem;
           background: #0d1117; color: #e6edf3; }}
    h1 {{ color: #58a6ff; }}
    .metric {{ font-size: 1.4rem; margin: 0.5rem 0; }}
    .banner.degraded {{
      background: #3d2c00; border: 1px solid #d29922; color: #f0c14b;
      padding: 1rem 1.25rem; margin: 1rem 0 1.5rem; border-radius: 6px;
      line-height: 1.5;
    }}
    pre {{ background: #161b22; padding: 1rem; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Bench Run — Wear Accounting</h1>
  {degraded_banner}
  <p>Device: <code>{html.escape(env.device_path)}</code></p>
  <p>Enclosure: {html.escape(env.enclosure_class)}</p>
  <div class="metric">Host writes this run: {host_writes_line}</div>
  <h2>Wear delta</h2>
  <ul>
    <li>percentage_used Δ: {wd.percentage_used_delta}</li>
    <li>media errors Δ: {wd.media_and_data_integrity_errors_delta}</li>
    <li>warning temp time Δ (min): {wd.warning_composite_temp_time_minutes_delta}</li>
    <li>critical temp time Δ (min): {wd.critical_composite_temp_time_minutes_delta}</li>
  </ul>
  {stress_block}
  <h2>Environment manifest</h2>
  <pre>{html.escape(json.dumps(env.model_dump(mode="json"), indent=2))}</pre>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")


def save_bench_run_report(report: BenchRunReport, output_path: Path) -> BenchRunReport:
    """Render HTML and return report with html_path set."""
    render_bench_run_report(report, output_path)
    return report.model_copy(update={"html_path": str(output_path)})

"""Bench run reporting: HTML artifact for clone-and-verify workflow."""

from __future__ import annotations

import html
import json
from pathlib import Path

from nvme_sentinel.bench.schema import BenchRunReport


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
    pre {{ background: #161b22; padding: 1rem; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Bench Run — Wear Accounting</h1>
  <p>Device: <code>{html.escape(env.device_path)}</code></p>
  <p>Enclosure: {html.escape(env.enclosure_class)}</p>
  <div class="metric">Host writes this run: <strong>{host_bytes_gb:.2f} GB ({host_bytes_tb:.6f} TB)</strong>
    ({wd.data_units_written_delta:,} data units written Δ)</div>
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

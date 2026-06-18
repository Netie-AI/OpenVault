"""Path trace HTML reporting."""

from __future__ import annotations

import html
import json
from pathlib import Path

from nvme_profiler.schema import CapabilityManifest, PathTraceReport


def _degradation_banner(manifest: CapabilityManifest | None) -> str:
    """Banner when acceleration tiers are degraded."""
    if manifest is None or not manifest.degraded_reasons:
        return ""
    reasons = "<br/>".join(html.escape(r) for r in manifest.degraded_reasons)
    return f"""
  <div class="banner degraded">
    <strong>Degraded acceleration tiers.</strong>
    The probe detected capabilities below the best-case path. This is expected on
    commodity laptops (GeForce, boot NVMe). Enabled tiers:
    {html.escape(", ".join(t.value for t in manifest.enabled_tiers))}.
    <br/><br/>{reasons}
  </div>
"""


def render_path_trace_report(report: PathTraceReport, output_path: Path) -> None:
    """Write standalone HTML for a PathTraceReport."""
    env = report.env_manifest
    manifest = env.capability_manifest
    banner = _degradation_banner(manifest)
    hop_rows = ""
    for hop in report.hop_timeline:
        hop_rows += f"""
        <tr>
          <td>{html.escape(hop.hop_id.value)}</td>
          <td>{hop.duration_ms:.3f}</td>
          <td>{hop.bytes_moved:,}</td>
          <td>{html.escape(hop.notes)}</td>
        </tr>"""
    bottleneck = report.bottleneck_hop.value if report.bottleneck_hop else "n/a"
    gpu_idle = (
        f"{report.gpu_idle_pct_waiting_on_io:.1f}%"
        if report.gpu_idle_pct_waiting_on_io is not None
        else "n/a"
    )
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>nvme-profiler PathTraceReport</title>
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
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #30363d; padding: 0.5rem; text-align: left; }}
    th {{ background: #161b22; }}
    pre {{ background: #161b22; padding: 1rem; overflow: auto; }}
  </style>
</head>
<body>
  <h1>Full-Path Trace</h1>
  {banner}
  <div class="metric">Bottleneck hop: <strong>{html.escape(bottleneck)}</strong></div>
  <div class="metric">GPU idle waiting on I/O: <strong>{gpu_idle}</strong></div>
  <h2>Hop timeline</h2>
  <table>
    <thead><tr><th>Hop</th><th>Duration (ms)</th><th>Bytes</th><th>Notes</th></tr></thead>
    <tbody>{hop_rows}</tbody>
  </table>
  <h2>Environment</h2>
  <pre>{html.escape(json.dumps(env.model_dump(mode="json"), indent=2))}</pre>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")


def save_path_trace_report(report: PathTraceReport, output_path: Path) -> PathTraceReport:
    """Render HTML and return report with html_path set."""
    render_path_trace_report(report, output_path)
    return report.model_copy(update={"html_path": str(output_path)})

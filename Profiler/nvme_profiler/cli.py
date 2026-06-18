"""nvme-profiler CLI."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import structlog
import typer

from nvme_profiler.path_trace import build_mock_path_trace_report
from nvme_profiler.probe import run_capability_probe
from nvme_profiler.report import save_path_trace_report

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

app = typer.Typer(
    name="nvme-profiler",
    help="Full-path bottleneck profiler and capability probe.",
    no_args_is_help=True,
)


@app.command("probe")
def probe_cmd(
    out: Path = typer.Option(
        Path("capability_manifest.json"),
        "--out",
        "-o",
        help="Output path for capability manifest JSON.",
    ),
) -> None:
    """Probe host capabilities and write capability_manifest.json."""
    manifest = run_capability_probe()
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json")
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    typer.echo(f"Wrote {out} ({len(manifest.enabled_tiers)} tiers enabled)")


@app.command("trace-mock")
def trace_mock_cmd(
    out: Path = typer.Option(
        Path("path_trace_report.html"),
        "--out",
        "-o",
        help="Output HTML path trace report.",
    ),
    device: str = typer.Option("/dev/mock-nvme0", "--device", help="Device path label."),
) -> None:
    """Build mock PathTraceReport (CI-friendly, no GPU required)."""
    report = build_mock_path_trace_report(device_path=device)
    save_path_trace_report(report, out)
    bottleneck = report.bottleneck_hop.value if report.bottleneck_hop else "n/a"
    typer.echo(f"Wrote {out} (bottleneck={bottleneck})")


if __name__ == "__main__":
    app()

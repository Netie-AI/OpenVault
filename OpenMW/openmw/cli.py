"""OpenMW CLI — front door for hardware profile, model routing, and session orchestration.

Free tier: ``doctor`` (hardware + bottleneck report) and ``route`` (model fit, no coding).
Stubs (not yet implemented): ``train`` and ``infer`` — see MASTER_HANDOFF PART 12.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import structlog
import typer
from nvme_profiler.path_trace import build_mock_path_trace_report
from nvme_profiler.report import save_path_trace_report

from openmw.demo_payload import bundled_webui_dir, write_demo_payload
from openmw.device_profile import DeviceProfile, detect
from openmw.model_router import ModelRouter

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
)

log = structlog.get_logger()

app = typer.Typer(
    name="openmw",
    help="Hardware-aware local inference/training middleware front door (BYORT).",
    no_args_is_help=True,
)


def _profile_to_dict(profile: DeviceProfile) -> dict[str, object]:
    return {
        "gpu_name": profile.gpu_name,
        "gpu_vram_gb": profile.gpu_vram_gb,
        "gpu_bandwidth_gbps": profile.gpu_bandwidth_gbps,
        "system_ram_gb": profile.system_ram_gb,
        "cpu_cores": profile.cpu_cores,
        "nvme_model": profile.nvme_model,
        "nvme_seq_read_gbps": profile.nvme_seq_read_gbps,
        "nvme_endurance_tbw": profile.nvme_endurance_tbw,
        "unified_memory": profile.unified_memory,
        "cpu_inference_mode": profile.cpu_inference_mode,
    }


@app.command("doctor")
def doctor_cmd(
    out: Path = typer.Option(  # noqa: B008
        Path("openmw_doctor"),
        "--out",
        "-o",
        help="Output directory for profile.json and bottleneck_report.html.",
    ),
    json_only: bool = typer.Option(
        False, "--json", help="Print profile JSON to stdout instead of writing files."
    ),
) -> None:
    """Detect hardware and produce a bottleneck report. Free tier, no coding required."""
    profile = detect()
    payload = _profile_to_dict(profile)

    if json_only:
        typer.echo(json.dumps(payload, indent=2))
        return

    out.mkdir(parents=True, exist_ok=True)
    profile_path = out / "profile.json"
    profile_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Mock path trace until real nsys/Linux hardware path is wired (PART 10 Q2 manual gate).
    report = build_mock_path_trace_report(device_path=profile.nvme_model or "/dev/mock-nvme0")
    report_path = out / "bottleneck_report.html"
    save_path_trace_report(report, report_path)

    bottleneck = report.bottleneck_hop.value if report.bottleneck_hop else "n/a"
    typer.echo(f"Wrote {profile_path}")
    typer.echo(f"Wrote {report_path} (bottleneck={bottleneck})")
    typer.echo(
        f"GPU: {profile.gpu_name or 'none (CPU-only)'} "
        f"({profile.gpu_vram_gb:.1f} GB) | RAM: {profile.system_ram_gb:.1f} GB | "
        f"NVMe: {profile.nvme_seq_read_gbps:.2f} GB/s"
    )


@app.command("route")
def route_cmd(
    model_id: str = typer.Argument(..., help="Registry model id, e.g. llama-3.3-8b"),
    as_json: bool = typer.Option(False, "--json", help="Print RoutingDecision as JSON."),
) -> None:
    """Show the hardware-aware routing decision for a model. No coding required."""
    profile = detect()
    router = ModelRouter()
    try:
        decision = router.route(profile, model_id)
    except KeyError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(decision.__dict__, indent=2))
        return

    typer.echo(f"Model:     {decision.model_id}")
    typer.echo(f"Quant:     {decision.quant_level}")
    typer.echo(
        f"Layers:    gpu={decision.gpu_layers} cpu={decision.cpu_ram_layers} "
        f"nvme={decision.nvme_layers}"
    )
    typer.echo(f"Strategy:  {decision.offload_strategy}")
    typer.echo(f"Est VRAM:  {decision.estimated_vram_gb:.2f} GB")
    typer.echo(f"Est tok/s: {decision.estimated_tok_s:.1f}")
    if decision.kv_quant_recommended:
        typer.echo(
            f"KV quant:  recommended (value={decision.value_quant_bits}b "
            f"key={decision.key_quant_bits}b)"
        )


@app.command("train")
def train_cmd(
    dataset: Path = typer.Option(..., "--dataset", help="Path to training dataset."),  # noqa: B008
) -> None:
    """[Not yet implemented] Hardware-aware training launch via Unsloth bridge.

    Blocked on training_router.py — see MASTER_HANDOFF PART 12, item #2.
    Today, training_config.py is static defaults with no DeviceProfile awareness.
    """
    typer.echo(
        "openmw train is not yet implemented: training_router.py does not exist.\n"
        "See MASTER_HANDOFF.md PART 12 for the planned hardware-aware training formula.",
        err=True,
    )
    raise typer.Exit(code=2)


@app.command("infer")
def infer_cmd(
    model: str = typer.Option(..., "--model", help="Model id to connect for live inference."),
) -> None:
    """[Not yet implemented] VIP runtime connector (vLLM / llama.cpp / LMCache).

    Blocked on OpenMW-Plan PART 9 — hardware-gated on Linux + native NVMe passthrough.
    """
    typer.echo(
        "openmw infer is not yet implemented: VIP runtime connector is OpenMW-Plan PART 9.\n"
        "See MASTER_HANDOFF.md PART 9 pre-flight gate.",
        err=True,
    )
    raise typer.Exit(code=2)


@app.command("console")
def console_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (localhost default)."),
    port: int = typer.Option(5000, "--port", help="HTTP port for OpenVault console."),
    cortex_url: str = typer.Option(
        "http://127.0.0.1:8000",
        "--cortex-url",
        help="Cortex / Netie Engine base URL.",
    ),
    mock_health: bool = typer.Option(
        False,
        "--mock-health",
        help="Use demo hardware profile instead of live detect().",
    ),
    precheck_interval: float = typer.Option(
        60.0,
        "--precheck-interval",
        help="Seconds between continuous API-key health prechecks.",
    ),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-open-browser",
        help="Open the liquid-glass console in a browser.",
    ),
) -> None:
    """Start OpenVault: secure /v1 proxy, key vault, precheck/fallback, Cortex catalog."""
    import webbrowser

    import uvicorn

    from openmw.openvault.app import create_app

    app = create_app(
        cortex_url=cortex_url,
        mock_health=mock_health,
        precheck_interval_s=precheck_interval,
    )
    url = f"http://{host}:{port}/"
    typer.echo(f"OpenVault console at {url}")
    typer.echo(f"Secure API endpoint: {url}v1/chat/completions")
    typer.echo(f"Cortex URL: {cortex_url}")
    if open_browser and host in ("127.0.0.1", "localhost"):
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


@app.command("demo-ui")
def demo_ui_cmd(
    out: Path = typer.Option(  # noqa: B008
        Path("openmw_demo_ui"),
        "--out",
        "-o",
        help="Output directory for demo.json (index.html served from bundled webui/).",
    ),
    model_id: str = typer.Option(
        "llama-3.3-8b",
        "--model",
        help="Model id for routing panel in demo payload.",
    ),
    mock_profile: bool = typer.Option(
        False,
        "--mock-profile",
        help="Use fallback RTX 4050 profile instead of live detect().",
    ),
    serve: bool = typer.Option(
        True,
        "--serve/--no-serve",
        help="Start local HTTP server and open the demo in a browser.",
    ),
    port: int = typer.Option(5000, "--port", help="HTTP port when --serve is set."),
) -> None:
    """Export schema-backed demo JSON and open the liquid-glass WebUI dashboard."""
    import shutil
    import webbrowser
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    webui_src = bundled_webui_dir()
    if not webui_src.is_dir():
        typer.echo(f"Bundled WebUI not found: {webui_src}", err=True)
        raise typer.Exit(code=1)

    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "index.html"
    shutil.copy2(webui_src / "index.html", index_path)
    json_path = write_demo_payload(
        out,
        model_id=model_id,
        use_live_detect=not mock_profile,
    )

    typer.echo(f"Wrote {json_path}")
    typer.echo(f"Wrote {index_path}")

    if not serve:
        typer.echo(f"Open file://{index_path.resolve()} in a browser.")
        return

    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            log.debug("demo_ui_http", message=format % args)

    handler = lambda *args, **kwargs: _QuietHandler(  # noqa: E731
        *args, directory=str(out.resolve()), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/"
    typer.echo(f"Serving demo UI at {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("Demo UI server stopped.")


if __name__ == "__main__":
    app()

"""OpenMW CLI - front door for hardware profile, model routing, and session orchestration.

Free tier: ``doctor`` (hardware + bottleneck report) and ``route`` (model fit, no coding).
Stubs (not yet implemented): ``train`` and ``infer`` - see STATUS.md / PARKING_LOT.md.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import suppress
from pathlib import Path

import structlog
import typer
from nvme_profiler.path_trace import build_mock_path_trace_report
from nvme_profiler.report import save_path_trace_report

from openmw.demo_payload import openvault_app_dir, write_demo_payload
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

    Blocked on training_router.py - see STATUS.md next priorities.
    Today, training_config.py is static defaults with no DeviceProfile awareness.
    """
    typer.echo(
        "openmw train is not yet implemented: training_router.py does not exist.\n"
        "See STATUS.md for the planned hardware-aware training formula.",
        err=True,
    )
    raise typer.Exit(code=2)


@app.command("infer")
def infer_cmd(
    model: str = typer.Option(..., "--model", help="Model id to connect for live inference."),
) -> None:
    """[Not yet implemented] VIP runtime connector (vLLM / llama.cpp / LMCache).

    Blocked on OpenMW-Plan PART 9 - hardware-gated on Linux + native NVMe passthrough.
    """
    typer.echo(
        "openmw infer is not yet implemented: VIP runtime connector is hardware-gated.\n"
        "See STATUS.md / PARKING_LOT.md for the wear PRE-FLIGHT gate.",
        err=True,
    )
    raise typer.Exit(code=2)


@app.command("console")
def console_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (localhost default)."),
    port: int | None = typer.Option(
        None,
        "--port",
        help=(
            "HTTP port. Defaults to the port saved by `openmw ports --set api=...`, "
            "then OPENVAULT_API_PORT, then 5000."
        ),
    ),
    cortex_url: str | None = typer.Option(
        None,
        "--cortex-url",
        help="Cortex / Netie Engine base URL (default: CORTEX_URL or http://127.0.0.1:8010).",
    ),
    openide_url: str = typer.Option(
        "http://127.0.0.1:8765",
        "--openide-url",
        help="FreeIDE local bridge base URL (AirGPT serves FreeIDE on :8765).",
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
    """Start OpenVault: secure /v1 proxy, key vault, precheck/fallback, Cortex+FreeIDE mesh."""
    import webbrowser

    import uvicorn

    from openmw.openvault.app import create_app
    from openmw.openvault.mesh.local_mesh import cortex_base_url
    from openmw.openvault.ports import resolve_port

    # An explicit --port still wins; otherwise honour the port the operator
    # saved with `openmw ports --set`. Without this the saved choice would be
    # written to disk and then ignored at every start, which is the kind of
    # setting that is worse than not having one (R-0011).
    port = resolve_port("api", override=port)
    resolved_cortex = cortex_url if cortex_url is not None else cortex_base_url()
    app = create_app(
        cortex_url=resolved_cortex,
        openide_url=openide_url,
        mock_health=mock_health,
        precheck_interval_s=precheck_interval,
    )
    url = f"http://{host}:{port}/"
    typer.echo(f"OpenVault console at {url}")
    typer.echo(f"Secure API endpoint: {url}v1/chat/completions")
    typer.echo(f"Cortex URL: {resolved_cortex}")
    typer.echo(f"FreeIDE URL: {openide_url}")
    typer.echo(f"Local mesh: {url}api/local/mesh")
    typer.echo(f"Connect pack: {url}api/local/connect-pack")
    if open_browser and host in ("127.0.0.1", "localhost"):
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


@app.command("demo-ui")
def demo_ui_cmd(
    out: Path = typer.Option(  # noqa: B008
        Path("openmw_demo_ui"),
        "--out",
        "-o",
        help="Output directory for demo.json (API payload sample).",
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
        help="Start custody API + Next app and open the browser.",
    ),
    port: int = typer.Option(5000, "--port", help="Custody API port when --serve is set."),
    app_port: int = typer.Option(3010, "--app-port", help="Next.js app port."),
) -> None:
    """Export demo JSON and launch the real OpenVault app (``apps/web``)."""
    import os
    import shutil
    import subprocess
    import webbrowser

    app_dir = openvault_app_dir()
    if not (app_dir / "package.json").is_file():
        typer.echo(f"OpenVault app not found: {app_dir}", err=True)
        raise typer.Exit(code=1)

    out.mkdir(parents=True, exist_ok=True)
    json_path = write_demo_payload(
        out,
        model_id=model_id,
        use_live_detect=not mock_profile,
    )
    typer.echo(f"Wrote {json_path}")
    typer.echo(f"UI: {app_dir}  ->  http://127.0.0.1:{app_port}/")

    if not serve:
        return

    home = os.environ.get("OPENVAULT_HOME", str(Path.home() / ".openvault"))
    env = os.environ.copy()
    env["OPENVAULT_HOME"] = home
    env["OPENVAULT_APP_URL"] = f"http://127.0.0.1:{app_port}/"
    from openmw.openvault.mesh.local_mesh import cortex_base_url

    env["CORTEX_URL"] = env.get("CORTEX_URL") or cortex_base_url()

    uv = shutil.which("uv") or "uv"
    api = subprocess.Popen(
        [
            uv,
            "run",
            "openmw",
            "console",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--cortex-url",
            env["CORTEX_URL"],
            "--no-open-browser",
            "--mock-health",
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
    )
    npm = shutil.which("npm") or ("npm.cmd" if os.name == "nt" else "npm")
    if not (app_dir / "node_modules" / "next").is_dir():
        typer.echo("Installing apps/web dependencies (npm)...")
        subprocess.check_call([npm, "install", "--no-audit", "--no-fund"], cwd=str(app_dir))
    web = subprocess.Popen([npm, "run", "dev"], cwd=str(app_dir), env=env)

    url = f"http://127.0.0.1:{app_port}/"
    typer.echo(f"Custody API  http://127.0.0.1:{port}/")
    typer.echo(f"OpenVault App {url}")
    webbrowser.open(url)
    try:
        api.wait()
    except KeyboardInterrupt:
        typer.echo("Demo stopped.")
    finally:
        for proc in (web, api):
            with suppress(Exception):
                proc.terminate()


@app.command("ports")
def ports_cmd(
    set_: str = typer.Option(
        "",
        "--set",
        help="Save a port for this device, e.g. --set api=5050. Used on every later start.",
    ),
) -> None:
    """Show which application holds each stack port, and pick your own ports.

    "Port busy" is not something an operator can act on. This names the
    application holding the port, with its executable path, so they know what
    to close - and offers a port they can keep instead.
    """
    from openmw.openvault import ports as ports_mod

    if set_:
        if "=" not in set_:
            typer.echo("Use --set <service>=<port>, e.g. --set api=5050")
            raise typer.Exit(2)
        key, _, value = set_.partition("=")
        key, value = key.strip(), value.strip()
        if key not in ports_mod.SERVICES_BY_KEY:
            known = ", ".join(s.key for s in ports_mod.SERVICES)
            typer.echo(f"Unknown service '{key}'. Known: {known}")
            raise typer.Exit(2)
        try:
            path = ports_mod.set_port(key, int(value))
        except ValueError as exc:
            typer.echo(f"Refused: {exc}")
            raise typer.Exit(2) from exc
        typer.echo(f"Saved {key} = {value} in {path}")
        typer.echo("This is used on every later start until you change it.")
        return

    statuses = ports_mod.inspect_all()
    typer.echo(f"Port settings for this device: {ports_mod.ports_file()}")
    typer.echo("")
    for status in statuses:
        mark = {"free": "free  ", "ours": "in use", "foreign": "BLOCKED"}[status.state]
        typer.echo(f"  {mark}  :{status.port:<6}{status.service.label}")
        if status.listener is not None:
            typer.echo(f"          held by {status.listener.describe()}")
        for note in status.notes:
            typer.echo(f"          {note}")
    blocked = [s for s in statuses if s.blocked]
    if not blocked:
        typer.echo("")
        typer.echo("Nothing is blocking OpenVault.")
        return
    for status in blocked:
        typer.echo("")
        typer.echo(ports_mod.blocking_message(status))
    raise typer.Exit(1)


if __name__ == "__main__":
    app()

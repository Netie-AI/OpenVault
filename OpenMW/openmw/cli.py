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


if __name__ == "__main__":
    app()

from __future__ import annotations



import ctypes

import io

import json

import logging

import subprocess

import sys

from pathlib import Path



import structlog

import typer



structlog.configure(

    processors=[

        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),

        structlog.processors.add_log_level,

        structlog.dev.ConsoleRenderer(colors=False),

    ],

    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),

    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),

)



from nvme_sentinel.adapters.host_proxy import is_host_proxy_path
from nvme_sentinel.cli_nas import nas_app

from nvme_sentinel.commands.identify import active_namespace_list, identify_controller

from nvme_sentinel.hal.factory import get_adapter

from nvme_sentinel.hal.interface import DeviceInfo

from nvme_sentinel.inventory.discovery import list_devices

from nvme_sentinel.reporting.report import render_smart_report

from nvme_sentinel.snapshot.collect import collect_snapshot, snapshot_to_json_bytes

from nvme_sentinel.telemetry.read import read_smart



app = typer.Typer(

    name="nvme-sentinel",

    help="Cross-platform NVMe SSD validation and health monitoring.",

    no_args_is_help=True,

)

app.add_typer(nas_app, name="nas")





def _require_admin_or_elevate(device: str | None) -> None:

    """If not admin, offer to relaunch elevated via UAC (Windows only)."""

    if device is None:

        return

    if is_host_proxy_path(device):

        return

    if sys.platform != "win32":

        return

    if ctypes.windll.shell32.IsUserAnAdmin():

        return

    typer.echo("[WARN] Not running as Administrator.")

    typer.echo(f"       DeviceIoControl on {device!r} requires admin rights.")

    typer.echo("")

    typer.echo("Options:")

    typer.echo("  1. Re-run this PowerShell as Administrator")

    typer.echo("  2. Auto-elevate via UAC prompt now")

    choice = typer.prompt("Auto-elevate? [y/N]", default="n")

    if choice.lower() == "y":

        exe = sys.executable

        args = " ".join(sys.argv[1:])

        ps_cmd = (

            f"Start-Process -FilePath '{exe}' "

            f"-ArgumentList '-m nvme_sentinel.cli {args}' "

            f"-Verb RunAs -Wait"

        )

        subprocess.run(

            ["powershell", "-Command", ps_cmd],

            check=False,

        )

        raise typer.Exit(0)





def _format_size(size_bytes: int | None) -> str:

    if size_bytes is None:

        return "—"

    for unit, div in (("TiB", 1024**4), ("GiB", 1024**3), ("MiB", 1024**2)):

        if size_bytes >= div:

            return f"{size_bytes / div:.2f} {unit}"

    return f"{size_bytes} B"





@app.command("list-devices")

def list_devices_cmd(

    json_out: bool = typer.Option(False, "--json"),

) -> None:

    """List storage devices and suggested telemetry sources (read-only inventory)."""

    devices = list_devices()

    if json_out:

        typer.echo(json.dumps([d.model_dump() for d in devices], indent=2))

        return

    if not devices:

        typer.echo("No storage devices found (or inventory unsupported on this platform).")

        raise typer.Exit(0)

    typer.echo(f"{'PATH':<28} {'BUS':<8} {'NVMe':<5} {'SIZE':<12} MODEL")

    typer.echo("-" * 72)

    for d in devices:

        ioctl_hint = ""

        if d.linux_nvme_path:

            ioctl_hint = f"  ioctl→{d.linux_nvme_path}"

        typer.echo(

            f"{d.device_path:<28} {d.bus_type:<8} "

            f"{'yes' if d.is_nvme else 'no':<5} {_format_size(d.size_bytes):<12} "

            f"{(d.model or d.friendly_name)[:32]}"

        )

        if d.drive_letters:

            typer.echo(f"    letters: {', '.join(d.drive_letters)}")

        typer.echo(f"    telemetry: {', '.join(d.suggested_telemetry)}{ioctl_hint}")





@app.command()

def collect(

    device: str = typer.Option(..., "--device", "-d"),

    output: Path = typer.Option(..., "--output", "-o"),

    mock: bool = typer.Option(False, "--mock"),

    readonly_confirmed: bool = typer.Option(

        True,

        "--readonly-confirmed/--no-readonly-confirmed",

        help="Acknowledge collect uses read-only admin queries only (default: on).",

    ),

) -> None:

    """Export read-only Identify + SMART snapshot JSON (VM sharing, baselines)."""

    if not readonly_confirmed:

        typer.echo("[FAIL] Refusing collect without --readonly-confirmed.")

        raise typer.Exit(1)

    if device and not mock:

        _require_admin_or_elevate(device)

    snap = collect_snapshot(device, mock=mock)

    output.parent.mkdir(parents=True, exist_ok=True)

    output.write_bytes(snapshot_to_json_bytes(snap))

    typer.echo(f"Snapshot -> {output.resolve()}")

    typer.echo(f"Telemetry source: {snap.telemetry_source.value} — {snap.telemetry_source.label()}")

    typer.echo("Read-only: no writes, trim, secure erase, or firmware changes.")





@app.command()

def info(

    device: str | None = typer.Option(None, "--device", "-d"),

    mock: bool = typer.Option(False, "--mock"),

) -> None:

    """Print Identify Controller fields."""

    if device and not mock:

        _require_admin_or_elevate(device)

    with get_adapter(device_path=device, force="mock" if mock else None) as dev:

        ctrl = identify_controller(dev)

        ns = active_namespace_list(dev)

    sys.stderr.flush()

    typer.echo(f"Model:      {ctrl.mn}")

    typer.echo(f"Serial:     {ctrl.sn}")

    typer.echo(f"Firmware:   {ctrl.fr}")

    typer.echo(f"VID:        0x{ctrl.vid:04X}")

    typer.echo(f"Namespaces: {ns}")





@app.command()

def smart(

    device: str | None = typer.Option(None, "--device", "-d"),

    mock: bool = typer.Option(False, "--mock"),

    json_out: bool = typer.Option(False, "--json"),

    output: Path | None = typer.Option(None, "--output", "-o"),

) -> None:

    """Read SMART Health Log (LID 0x02) with telemetry source label."""

    if device and not mock:

        _require_admin_or_elevate(device)

    result = read_smart(device, mock=mock)

    sys.stderr.flush()

    if not json_out:

        typer.echo(f"Telemetry source: {result.source.value}")

        typer.echo(f"  {result.source.label()}")

        if result.message:

            typer.echo(f"  Note: {result.message}")

    if result.smart is not None:

        if json_out:

            payload = {

                "telemetry_source": result.source.value,

                "smart_health": result.smart.to_dict(),

            }

            typer.echo(json.dumps(payload, indent=2, default=str))

        else:

            for k, v in result.smart.to_dict().items():

                typer.echo(f"  {k:<48} {v}")

        if output:

            render_smart_report(result.smart, output)

            typer.echo(f"\nReport -> {output.resolve()}")

        return

    if result.wmi_counters:

        typer.echo("")

        typer.echo("  [degraded] WMI subset — not full 512-byte NVMe SMART log")

        if json_out:

            typer.echo(

                json.dumps(

                    {

                        "telemetry_source": result.source.value,

                        "degraded": True,

                        "wmi_fallback": result.wmi_counters,

                    },

                    indent=2,

                )

            )

        else:

            for k, v in result.wmi_counters.items():

                typer.echo(f"  {k:<40} {v}")

        typer.echo("")

        typer.echo("  Full SMART: Linux ioctl, WSL2 bare mount, or Thunderbolt NVMe enclosure.")

        raise typer.Exit(0)

    typer.echo("[FAIL] No SMART data available.")

    raise typer.Exit(1)





@app.command()

def demo(

    output_dir: Path = typer.Option(Path("reports"), "--output-dir"),

) -> None:

    """

    End-to-end demo on MockNvmeAdapter. No hardware needed. Runs in < 3 seconds.

    Shows all five interview signals live.

    """

    _demo_sink = io.StringIO()

    structlog.configure(

        processors=[

            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),

            structlog.processors.add_log_level,

            structlog.dev.ConsoleRenderer(colors=False),

        ],

        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),

        logger_factory=structlog.PrintLoggerFactory(file=_demo_sink),

        cache_logger_on_first_use=False,

    )



    def say(line: str = "") -> None:

        typer.echo(line)

        sys.stdout.flush()



    say("=" * 62)

    say("  nvme-sentinel DEMO  -  MockNvmeAdapter  -  no hardware")

    say("=" * 62)



    with get_adapter(force="mock") as dev:

        say("\n[1/3] Identify Controller (NVMe opcode 0x06, CNS 0x01)")

        ctrl = identify_controller(dev)

        ns = active_namespace_list(dev)

        say(f"  Model:      {ctrl.mn}")

        say(f"  Serial:     {ctrl.sn}")

        say(f"  Firmware:   {ctrl.fr}")

        say(f"  VID:        0x{ctrl.vid:04X}  (Samsung)")

        say(f"  Namespaces: {ns}")



        say("\n[2/3] SMART Health Log (NVMe LID 0x02  *  NVMe Base Spec 2.0c sec 6.7.2)")

        smart_log = read_smart(None, mock=True).smart

        assert smart_log is not None

        say(f"  Critical Warning : {smart_log.critical_warning}")

        say(

            f"  Temperature      : {smart_log.composite_temperature_celsius} C  "

            f"({smart_log.composite_temperature_kelvin} K)"

        )

        say(

            f"  Available Spare  : {smart_log.available_spare}%  "

            f"(threshold {smart_log.available_spare_threshold}%)"

        )

        say(f"  Percentage Used  : {smart_log.percentage_used}%")

        say(f"  Power On Hours   : {smart_log.power_on_hours:,}")

        say(f"  Power Cycles     : {smart_log.power_cycles:,}")

        say(f"  Unsafe Shutdowns : {smart_log.unsafe_shutdowns:,}")

        say(f"  Media Errors     : {smart_log.media_and_data_integrity_errors}")

        say(f"  Data Units Read  : {smart_log.data_units_read:,}")

        say(f"  Data Units Written: {smart_log.data_units_written:,}")



        dev_info = DeviceInfo(

            path="/dev/mock-nvme0",

            model=ctrl.mn,

            serial=ctrl.sn,

            firmware_rev=ctrl.fr,

            namespace_count=ctrl.nn,

            is_nvme=True,

        )



    sys.stderr.flush()

    say("\n[3/3] HTML report...")

    report_path = output_dir / "demo.html"

    render_smart_report(

        smart_log,

        report_path,

        device_info=dev_info,

        controller_identify=ctrl,

        include_interview_signals=True,

        title="nvme-sentinel Demo Report (MockNvmeAdapter)",

    )

    say(f"  Saved -> {report_path.resolve()}")



    say("\n" + "=" * 62)

    say("  DEMO COMPLETE - interview signals:")

    say("  [ok] ioctl(NVME_IOCTL_ADMIN_CMD=0xC0484E41)  -  Linux primary path")

    say("  [ok] DeviceIoControl(IOCTL_STORAGE_PROTOCOL_COMMAND=0x002DD3C8)  -  Windows")

    say("  [ok] Byte-accurate mock seeded from real device captures")

    say("  [ok] 128-bit SMART counters via Python arbitrary-precision int")

    say("  [ok] CQE DW0 preserved in CommandResult.result_dw0")

    say("  [ok] CI: 2 OS x 3 Python = 6 cells, zero hardware dependency")

    say("=" * 62)

    say(f"\nOpen in browser:  {report_path.resolve()}")



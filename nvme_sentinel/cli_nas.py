"""Typer sub-app: Unraid NAS remote collection."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from nvme_sentinel.remote.unraid import (
    UnraidSnapshot,
    collect_unraid_snapshot,
    discover_unraid_disks,
)
from nvme_sentinel.telemetry.source import TelemetrySource

nas_app = typer.Typer(help="Unraid NAS discovery and read-only SMART collection (SSH).")


@nas_app.command("discover")
def nas_discover(
    host: str = typer.Option(..., "--host", "-H", help="Unraid hostname or IP"),
    user: str = typer.Option("root", "--user", "-u"),
    port: int = typer.Option(22, "--port", "-p"),
    identity_file: Path | None = typer.Option(None, "--identity-file", "-i"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List disks on Unraid via SSH (lsblk)."""
    disks = discover_unraid_disks(
        host,
        user=user,
        port=port,
        identity_file=str(identity_file) if identity_file else None,
    )
    if json_out:
        payload = [d.model_dump(mode="json") for d in disks]
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    typer.echo(f"Unraid host: {host}  ({len(disks)} disk(s))")
    for d in disks:
        kind = "NVMe" if d.is_nvme else "HDD/SSD"
        typer.echo(
            f"  {d.device_path:<16} {kind:<6} {d.model or d.name:<24} "
            f"source={d.telemetry_source.value}"
        )


@nas_app.command("collect")
def nas_collect(
    host: str = typer.Option(..., "--host", "-H"),
    output: Path = typer.Option(Path("reports/unraid.json"), "--output", "-o"),
    user: str = typer.Option("root", "--user", "-u"),
    port: int = typer.Option(22, "--port", "-p"),
    identity_file: Path | None = typer.Option(None, "--identity-file", "-i"),
    skip_smart: bool = typer.Option(False, "--skip-smart", help="Discovery only"),
) -> None:
    """Collect read-only SMART for all Unraid disks (smartctl / nvme-cli over SSH)."""
    snap = collect_unraid_snapshot(
        host,
        user=user,
        port=port,
        identity_file=str(identity_file) if identity_file else None,
        include_smart=not skip_smart,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(snap.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"Saved {len(snap.disks)} disk(s) -> {output.resolve()}")
    typer.echo(f"Telemetry path: {TelemetrySource.UNRAID.value} (SSH, read-only)")


@nas_app.command("report")
def nas_report(
    input_path: Path = typer.Option(..., "--input", "-i", help="unraid.json from nas collect"),
    output: Path | None = typer.Option(None, "--output", "-o", help="HTML report path"),
) -> None:
    """Summarize a Unraid collection JSON on stdout or as HTML."""
    snap = UnraidSnapshot.model_validate_json(input_path.read_text(encoding="utf-8"))
    lines = [
        f"# Unraid report — {snap.host}",
        f"Collected: {snap.collected_at.isoformat()}",
        f"Disks: {len(snap.disks)}",
        "",
    ]
    for d in snap.disks:
        kind = "NVMe" if d.is_nvme else "ATA/HDD"
        has_smart = d.nvme_smart_json is not None or d.smart_json is not None
        lines.append(
            f"- **{d.device_path}** ({kind}) model={d.model!r} serial={d.serial!r} "
            f"smart={'yes' if has_smart else 'no'} source={d.telemetry_source.value}"
        )
    text = "\n".join(lines)
    if output:
        html = _unraid_markdown_to_html(text, snap)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html, encoding="utf-8")
        typer.echo(f"Report -> {output.resolve()}")
    else:
        typer.echo(text)


def _unraid_markdown_to_html(md: str, snap: UnraidSnapshot) -> str:
    """Minimal HTML wrapper for Unraid summary."""
    body = "".join(
        f"<li><code>{d.device_path}</code> "
        f"{'NVMe' if d.is_nvme else 'HDD'} "
        f"{d.model} — {d.telemetry_source.value}</li>"
        for d in snap.disks
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Unraid — {snap.host}</title></head>
<body>
<h1>nvme-sentinel Unraid report</h1>
<p>Host: {snap.host}<br>Collected: {snap.collected_at.isoformat()}</p>
<ul>{body}</ul>
<pre>{md}</pre>
</body></html>"""

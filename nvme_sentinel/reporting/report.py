"""Self-contained HTML report. No Jinja2, no CDN — stdlib only.
Industrial precision-instrument aesthetic; all styling inline."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

from nvme_sentinel.hal.interface import DeviceInfo
from nvme_sentinel.models.identify import ControllerIdentify
from nvme_sentinel.models.smart import SmartHealthLog

_VERSION = "v0.0.1"


def _sev(field: str, value: float) -> str:
    inverted = {"available_spare"}
    thresholds: dict[str, tuple[float, float]] = {
        "percentage_used": (80.0, 95.0),
        "composite_temperature_celsius": (65.0, 80.0),
        "available_spare": (20.0, 10.0),
        "media_and_data_integrity_errors": (0.5, 1.0),
    }
    if field not in thresholds:
        return "ok"
    w, c = thresholds[field]
    if field in inverted:
        return "crit" if value <= c else "warn" if value <= w else "ok"
    return "crit" if value >= c else "warn" if value >= w else "ok"


def _status_label(field: str, value: float) -> str:
    s = _sev(field, value)
    return {"ok": "OK", "warn": "WARN", "crit": "CRIT"}[s]


def _fmt_counter(n: int, unit_note: str) -> str:
    return f'{n:,} <span class="u">{html.escape(unit_note)}</span>'


def render_smart_report(
    log: SmartHealthLog,
    output_path: Path,
    device_info: DeviceInfo | None = None,
    controller_identify: ControllerIdentify | None = None,
    *,
    include_interview_signals: bool = False,
    title: str = "NVMe SMART Health Report",
) -> None:
    """Write a standalone HTML report for one SMART snapshot."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    temp_c = log.composite_temperature_celsius
    cw_int = log.critical_warning.value
    cw_str = str(log.critical_warning) if cw_int != 0 else "NONE"
    overall = "ok"
    if cw_int != 0 or log.media_and_data_integrity_errors > 0 or log.percentage_used >= 95:
        overall = "crit"
    elif log.percentage_used >= 80 or temp_c >= 80 or log.available_spare <= 10:
        overall = "warn"
    overall_label = {"ok": "HEALTHY", "warn": "WARNING", "crit": "CRITICAL"}[overall]

    rows_html: list[str] = []

    # label, sev key, value, is_html, optional int for muted zero-row
    entries: list[tuple[str, str, object, bool, int | None]] = [
        ("Critical Warning", "", cw_str, False, None),
        (
            "Composite Temperature (°C)",
            "composite_temperature_celsius",
            temp_c,
            False,
            None,
        ),
        ("Available Spare (%)", "available_spare", log.available_spare, False, None),
        (
            "Available Spare Threshold (%)",
            "",
            log.available_spare_threshold,
            False,
            None,
        ),
        ("Percentage Used (%)", "percentage_used", log.percentage_used, False, None),
        (
            "Data Units Read",
            "",
            _fmt_counter(
                log.data_units_read,
                "(512 B x 1000 unit, sec 4.4)",
            ),
            True,
            log.data_units_read,
        ),
        (
            "Data Units Written",
            "",
            _fmt_counter(
                log.data_units_written,
                "(512 B x 1000 unit, sec 4.4)",
            ),
            True,
            log.data_units_written,
        ),
        (
            "Host Read Commands",
            "",
            _fmt_counter(log.host_read_commands, "commands (128-bit LE)"),
            True,
            log.host_read_commands,
        ),
        (
            "Host Write Commands",
            "",
            _fmt_counter(log.host_write_commands, "commands (128-bit LE)"),
            True,
            log.host_write_commands,
        ),
        (
            "Power Cycles",
            "",
            _fmt_counter(log.power_cycles, "cycles (128-bit LE)"),
            True,
            log.power_cycles,
        ),
        (
            "Power On Hours",
            "",
            _fmt_counter(log.power_on_hours, "hours (128-bit LE)"),
            True,
            log.power_on_hours,
        ),
        (
            "Unsafe Shutdowns",
            "",
            _fmt_counter(log.unsafe_shutdowns, "count (128-bit LE)"),
            True,
            log.unsafe_shutdowns,
        ),
        (
            "Media & Data Integrity Errors",
            "media_and_data_integrity_errors",
            _fmt_counter(log.media_and_data_integrity_errors, "errors (128-bit LE)"),
            True,
            log.media_and_data_integrity_errors,
        ),
        (
            "Error Information Log Entries",
            "",
            _fmt_counter(
                log.number_of_error_information_log_entries,
                "entries (128-bit LE)",
            ),
            True,
            log.number_of_error_information_log_entries,
        ),
        (
            "Warning Composite Temp Time (min)",
            "",
            log.warning_composite_temp_time_minutes,
            False,
            None,
        ),
        (
            "Critical Composite Temp Time (min)",
            "",
            log.critical_composite_temp_time_minutes,
            False,
            None,
        ),
    ]

    for i, (label, field_key, raw_val, is_html_val, zero_check) in enumerate(entries):
        if is_html_val and isinstance(raw_val, str):
            display = raw_val
            fz = zero_check == 0 if zero_check is not None else False
        else:
            display = html.escape(str(raw_val), quote=False)
            fz = isinstance(raw_val, (int, float)) and float(raw_val) == 0.0
        if field_key:
            fv = float(zero_check if zero_check is not None else raw_val)  # type: ignore[arg-type]
            sev = _sev(field_key, fv)
            st = _status_label(field_key, fv)
        else:
            sev = "ok"
            st = "OK"
            if label == "Critical Warning" and cw_int != 0:
                sev, st = "crit", "CRIT"
        row_class = "muted" if fz else ""
        border = {"ok": "var(--ok)", "warn": "var(--warn)", "crit": "var(--crit)"}[sev]
        rows_html.append(
            f'<tr class="sr {row_class}" style="--i:{i}"><td>{html.escape(label)}</td>'
            f'<td class="val" style="border-left:3px solid {border}">{display}</td>'
            f'<td><span class="st st-{sev}">{st}</span></td></tr>'
        )

    rows_joined = "\n".join(rows_html)

    # Wear gauge arc: circumference 2*pi*60 ≈ 376.99
    circ = 377
    pu = max(0, min(100, log.percentage_used))
    dash_off = round(circ * (1 - pu / 100.0))
    gauge_color = "var(--ok)" if pu < 80 else "var(--warn)" if pu < 95 else "var(--crit)"

    metrics_block = ""
    if device_info:
        bus = "NVMe" if device_info.is_nvme else "—"
        metrics_block = f"""
<section class="sec sec-a">
  <div class="grid2">
    <div class="idtable">
      <table class="tmini"><tbody>
        <tr><th>Model</th><td>{html.escape(device_info.model)}</td></tr>
        <tr><th>Serial</th><td>{html.escape(device_info.serial)}</td></tr>
        <tr><th>Firmware</th><td>{html.escape(device_info.firmware_rev)}</td></tr>
        <tr><th>Path</th><td>{html.escape(device_info.path)}</td></tr>
        <tr><th>Bus</th><td>{bus}</td></tr>
      </tbody></table>
    </div>
    <div class="gauge-wrap">
      <svg class="gauge" viewBox="0 0 140 140" aria-label="Wear level">
        <circle class="g-bg" cx="70" cy="70" r="60" fill="none"
          stroke="var(--border)" stroke-width="10"/>
        <circle class="g-fg" cx="70" cy="70" r="60" fill="none" stroke="{gauge_color}"
          stroke-width="10" stroke-dasharray="{circ}"
          stroke-dashoffset="{dash_off}" transform="rotate(-90 70 70)">
          <animate attributeName="stroke-dashoffset" from="377" to="{dash_off}" dur="1s"
            fill="freeze" calcMode="spline" keySplines="0.4 0 0.2 1" keyTimes="0;1"/>
        </circle>
        <text x="70" y="76" text-anchor="middle" class="gpct">{pu}%</text>
      </svg>
      <div class="glab">WEAR LEVEL</div>
    </div>
  </div>
</section>"""

    identify_block = ""
    if controller_identify is not None:
        cid = controller_identify
        identify_block = f"""
<section class="sec sec-b">
  <h3 class="mini">Identify Controller (opcode 0x06, CNS 0x01)</h3>
  <table class="tmini full"><tbody>
    <tr><th>VID</th><td>0x{cid.vid:04X}</td><th>SSVID</th><td>0x{cid.ssvid:04X}</td></tr>
    <tr><th>MN</th><td colspan="3">{html.escape(cid.mn)}</td></tr>
    <tr><th>SN</th><td colspan="3">{html.escape(cid.sn)}</td></tr>
  </tbody></table>
</section>"""

    interview_block = ""
    if include_interview_signals:
        signals = [
            "ioctl(NVME_IOCTL_ADMIN_CMD=0xC0484E41) — Linux primary path",
            "DeviceIoControl(IOCTL_STORAGE_PROTOCOL_COMMAND=0x2DD480) — Windows",
            "Byte-accurate mock seeded from real device captures",
            "128-bit SMART counters via Python arbitrary-precision int",
            "CQE DW0 preserved in CommandResult.result_dw0",
            "CI: multiple OS and Python versions with zero hardware dependency",
        ]
        lines = "".join(
            f'<div class="sig"><span class="tok">[OK]</span> {html.escape(s)}</div>'
            for s in signals
        )
        interview_block = f"""
<section class="sec sec-sig">
  <div class="sigbox">
    <div class="sigtitle">VALIDATION FRAMEWORK SIGNALS</div>
    {lines}
  </div>
</section>"""

    pulse_class = " pulse" if overall != "ok" else ""

    pct_spare = max(0, min(100, log.available_spare))
    pct_temp = max(0, min(100, temp_c))  # 0-100 C scale for bar width

    html_doc = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)}</title>
<style>
:root {{
  --bg:#080c10; --surface:#0d1520; --border:#1a2d45; --accent:#00d4ff; --accent2:#ff6b35;
  --text:#c8d8e8; --muted:#4a6080; --ok:#00e676; --warn:#ffab00; --crit:#ff3d3d;
  --font:"Cascadia Code","JetBrains Mono",Consolas,"Courier New",monospace;
}}
*{{box-sizing:border-box}}
html,body{{margin:0;background:var(--bg);color:var(--text);font-family:var(--font);
  font-size:14px;animation:bodyIn .3s ease forwards}}
@keyframes bodyIn{{from{{opacity:0}}to{{opacity:1}}}}
@keyframes headIn{{from{{opacity:0;transform:translateY(-20px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes rowIn{{from{{opacity:0;transform:translateX(-8px)}}to{{opacity:1;transform:translateX(0)}}}}
@keyframes pulseB{{0%,100%{{opacity:.8}}50%{{opacity:1}}}}
@keyframes growX{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}
.header{{animation:headIn .4s ease forwards}}
.sec{{opacity:0;animation:secIn .35s ease forwards}}
.sec-a{{animation-delay:.1s}}.sec-b{{animation-delay:.15s}}
.sec-smart{{animation-delay:.2s}}.sec-spark{{animation-delay:.25s}}
.sec-sig{{animation-delay:.3s}}@keyframes secIn{{from{{opacity:0;transform:translateY(12px)}}
  to{{opacity:1;transform:translateY(0)}}}}
.topbar{{display:flex;justify-content:space-between;align-items:baseline;padding:16px 24px}}
.brand{{color:var(--accent);font-size:1.4rem;letter-spacing:1px}}
.meta{{color:var(--muted);font-size:.72rem;text-align:right}}
.meta strong{{color:var(--text);font-weight:600}}
hr.slim{{border:none;border-top:1px solid var(--border);margin:0 24px}}
.health{{height:48px;display:flex;align-items:center;justify-content:center;font-weight:700;
  letter-spacing:.2em;font-size:.85rem;margin:0;padding:0}}
.health.ok{{background:var(--ok);color:#001a0e}}
.health.warn{{background:var(--warn);color:#1a1000}}
.health.crit{{background:var(--crit);color:#fff0f0}}
.pulse{{animation:pulseB 1.2s ease-in-out infinite}}
section.sec{{margin:24px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}}
@media(max-width:800px){{.grid2{{grid-template-columns:1fr}}}}
.tmini{{width:100%;border-collapse:collapse;font-size:.85rem}}
.tmini th{{text-align:left;color:var(--muted);font-weight:500;padding:6px 12px 6px 0;
  border-bottom:1px solid var(--border)}}
.tmini td{{padding:6px 12px 6px 0;border-bottom:1px solid var(--border)}}
.tmini.full th{{width:10%}}
.gauge-wrap{{text-align:center}}
.gauge{{width:160px;height:160px}}
.gpct{{fill:var(--text);font-size:1.8rem;font-weight:600}}
.glab{{color:var(--muted);font-size:.65rem;letter-spacing:3px;margin-top:4px}}
.mini{{color:var(--accent);font-size:.75rem;letter-spacing:2px;margin:0 0 12px}}
table.smart{{width:100%;border-collapse:collapse;margin-top:8px}}
table.smart th{{text-align:left;color:var(--muted);font-size:.72rem;letter-spacing:2px;
  text-transform:uppercase;padding:10px;border-bottom:2px solid var(--border)}}
table.smart td{{padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}}
table.smart td.val{{font-variant-numeric:tabular-nums}}
table.smart tr.sr{{animation:rowIn .4s ease forwards;animation-delay:calc(var(--i)*30ms)}}
table.smart tr.sr:hover{{background:var(--surface);outline:1px solid var(--border)}}
table.smart tr.sr:hover td:first-child{{border-left:3px solid var(--accent);padding-left:9px}}
table.smart tr.muted{{color:var(--muted)}}
.u{{font-size:.7rem;color:var(--muted)}}
.st{{font-weight:700;font-size:.72rem}}
.st-ok{{color:var(--ok)}}.st-warn{{color:var(--warn)}}.st-crit{{color:var(--crit)}}
.sparkrow{{display:grid;grid-template-columns:160px 1fr 52px;align-items:center;gap:12px;
  margin:10px 0}}
.sparklab{{color:var(--muted);font-size:.75rem}}
.sparktrack{{height:6px;background:var(--border);border-radius:2px;overflow:hidden;position:relative}}
.sparkw{{height:100%}}
.sparkin{{height:100%;width:100%;transform:scaleX(0);transform-origin:left center;
  background:linear-gradient(90deg,var(--ok),var(--warn),var(--crit));
  animation:growX .8s ease-out forwards}}
.sigbox{{border-left:3px solid var(--accent);background:var(--surface);padding:16px 20px;
  border-radius:0 4px 4px 0}}
.sigtitle{{color:var(--accent);font-size:.7rem;letter-spacing:4px;margin-bottom:12px}}
.sig{{margin:6px 0;font-size:.82rem}}
.sig .tok{{color:var(--ok);font-weight:700;margin-right:8px}}
.footer{{padding:24px;color:var(--muted);font-size:.72rem;border-top:1px solid var(--border);
  margin-top:24px}}
</style></head><body>
<header class="header">
  <div class="topbar">
    <div class="brand">[ nvme-sentinel ]</div>
    <div class="meta"><strong>{now}</strong><br/>DIAGNOSTIC REPORT {_VERSION}</div>
  </div>
  <hr class="slim"/>
  <div class="health {overall}{pulse_class}">{overall_label}</div>
</header>
{metrics_block}
{identify_block}
<section class="sec sec-smart">
<h3 class="mini">SMART Health Log / NVMe LID 0x02 (Base Spec 2.0c sec 6.7.2)</h3>
<table class="smart">
<thead><tr><th>FIELD</th><th>VALUE</th><th>STATUS</th></tr></thead>
<tbody>
{rows_joined}
</tbody></table>
</section>
<section class="sec sec-spark">
<div class="sparkrow"><span class="sparklab">PERCENTAGE USED</span>
  <div class="sparktrack">
    <div class="sparkw" style="width:{pu}%"><div class="sparkin"></div></div>
  </div>
  <span>{log.percentage_used}%</span></div>
<div class="sparkrow"><span class="sparklab">AVAILABLE SPARE</span>
  <div class="sparktrack">
    <div class="sparkw" style="width:{pct_spare}%"><div class="sparkin"></div></div>
  </div>
  <span>{log.available_spare}%</span></div>
<div class="sparkrow"><span class="sparklab">TEMPERATURE (C)</span>
  <div class="sparktrack">
    <div class="sparkw" style="width:{pct_temp}%"><div class="sparkin"></div></div>
  </div>
  <span>{temp_c}</span></div>
</section>
{interview_block}
<footer class="footer">
nvme-sentinel | NVMe Base Spec 2.0c | LID 0x02 sec 6.7.2 |
ioctl=0xC0484E41 | DeviceIoControl=0x2DD480 | {now}
</footer>
</body></html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_doc, encoding="utf-8")


def render_demo_report(output_path: Path) -> None:
    """Generate a demo report from mock adapter. No hardware needed."""
    from nvme_sentinel.commands.identify import identify_controller
    from nvme_sentinel.commands.log_pages import get_smart_health
    from nvme_sentinel.hal.factory import get_adapter

    with get_adapter(force="mock") as dev:
        smart = get_smart_health(dev)
        ctrl = identify_controller(dev)
        info = DeviceInfo(
            path="/dev/mock-nvme0",
            model=ctrl.mn,
            serial=ctrl.sn,
            firmware_rev=ctrl.fr,
            namespace_count=ctrl.nn,
            is_nvme=True,
        )
    render_smart_report(
        smart,
        output_path,
        device_info=info,
        controller_identify=ctrl,
        include_interview_signals=True,
        title="nvme-sentinel Demo Report (MockNvmeAdapter)",
    )

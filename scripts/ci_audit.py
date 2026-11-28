#!/usr/bin/env python3
"""Fail the build on high or critical advisories in what we ship.

Covers apps/web and apps/shell (npm audit) and the OpenMW server lock
(pip-audit on the pinned requirements). security/audit-ignore.txt can skip
an id only when no patched release exists. The reason on that row is required.

Moderate and low findings are printed and do not fail the build. A finding
whose severity cannot be read fails closed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IGNORE_PATH = REPO / "security" / "audit-ignore.txt"
ID_RE = re.compile(
    r"^(?:GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}|CVE-\d{4}-\d+|PYSEC-\d{4}-\d+|\d+)$"
)
GHSA_RE = re.compile(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}")
BLOCKING = {"high", "critical", "unknown"}
SEVERITY_LABELS = {"critical", "high", "moderate", "medium", "low"}


@dataclass(frozen=True)
class Finding:
    source: str
    package: str
    version: str
    severity: str
    ids: tuple[str, ...]
    summary: str


def load_ignores(text: str) -> dict[str, str]:
    """Parse the ignore list. A non-comment line must be an id plus a reason."""
    ignores: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ident, sep, reason = line.partition(" ")
        reason = reason.strip()
        if not sep or not reason or ID_RE.fullmatch(ident) is None:
            raise ValueError(
                f"{IGNORE_PATH}:{lineno}: expected '<id> <reason>' "
                "for an unfixable high or critical advisory"
            )
        ignores[ident] = reason
    return ignores


def partition(
    findings: list[Finding], ignores: dict[str, str]
) -> tuple[list[Finding], list[tuple[Finding, str]]]:
    """Split blocking findings from those the ignore list names."""
    blocked: list[Finding] = []
    ignored: list[tuple[Finding, str]] = []
    for finding in findings:
        if finding.severity not in BLOCKING:
            continue
        reason = next((ignores[ident] for ident in finding.ids if ident in ignores), None)
        if reason is None:
            blocked.append(finding)
        else:
            ignored.append((finding, reason))
    return blocked, ignored


def _npm_ids(via: dict[str, object]) -> tuple[str, ...]:
    ids: list[str] = []
    source = via.get("source")
    if source is not None:
        ids.append(str(source))
    url = via.get("url")
    if isinstance(url, str):
        match = GHSA_RE.search(url)
        if match is not None:
            ids.append(match.group(0))
    return tuple(ids)


def npm_findings(audit: dict[str, object], source: str) -> list[Finding]:
    """High and critical advisories from `npm audit --json`, plus lower ones."""
    vulns = audit.get("vulnerabilities")
    if not isinstance(vulns, dict):
        return []
    findings: list[Finding] = []
    for package, info in vulns.items():
        if not isinstance(info, dict):
            continue
        version = str(info.get("range") or "")
        for via in info.get("via") or []:
            if not isinstance(via, dict):
                continue
            severity = str(via.get("severity") or "unknown").lower()
            title = str(via.get("title") or package)
            findings.append(
                Finding(
                    source=source,
                    package=str(package),
                    version=version,
                    severity=severity,
                    ids=_npm_ids(via),
                    summary=title,
                )
            )
    return findings


def pip_findings(audit: dict[str, object]) -> list[Finding]:
    """One finding per pip-audit vuln. Severity is filled in by the caller."""
    deps = audit.get("dependencies")
    if not isinstance(deps, list):
        return []
    findings: list[Finding] = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        for vuln in dep.get("vulns") or []:
            if not isinstance(vuln, dict):
                continue
            ids = [str(vuln.get("id") or "")]
            aliases = vuln.get("aliases") or []
            if isinstance(aliases, list):
                ids.extend(str(alias) for alias in aliases)
            ids = [ident for ident in ids if ident]
            summary = str(vuln.get("description") or vuln.get("id") or dep.get("name") or "")
            summary = " ".join(summary.split())[:160]
            findings.append(
                Finding(
                    source="server",
                    package=str(dep.get("name") or ""),
                    version=str(dep.get("version") or ""),
                    severity="unknown",
                    ids=tuple(ids),
                    summary=summary,
                )
            )
    return findings


def severity_from_osv(payload: dict[str, object]) -> str | None:
    """Return a normalized label from an OSV record, or None if it has none."""
    specific = payload.get("database_specific")
    if isinstance(specific, dict):
        label = str(specific.get("severity") or "").strip().lower()
        if label in SEVERITY_LABELS:
            return "moderate" if label == "medium" else label
    for item in payload.get("severity") or []:
        if not isinstance(item, dict):
            continue
        score = str(item.get("score") or "").strip()
        try:
            number = float(score)
        except ValueError:
            continue
        if number >= 9.0:
            return "critical"
        if number >= 7.0:
            return "high"
        if number >= 4.0:
            return "moderate"
        return "low"
    return None


def lookup_severity(ids: tuple[str, ...], fetch) -> str:
    """Ask OSV until one id carries a severity label. Unknown fails closed."""
    for ident in ids:
        if not ident or ident.isdigit():
            continue
        payload = fetch(ident)
        if not isinstance(payload, dict):
            continue
        label = severity_from_osv(payload)
        if label is not None:
            return label
    return "unknown"


def fetch_osv(ident: str) -> dict[str, object] | None:
    request = urllib.request.Request(
        f"https://api.osv.dev/v1/vulns/{ident}",
        headers={"User-Agent": "openvault-ci-audit"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def deduped_requirements(text: str) -> str:
    """Drop env markers so the audit does not depend on the runner OS or Python."""
    pins: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pin = line.split(";", 1)[0].strip()
        if pin not in seen:
            seen.add(pin)
            pins.append(pin)
    return "\n".join(pins) + "\n"


def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        timeout=timeout,
        text=True,
        capture_output=True,
    )


def _json_from_stdout(stdout: str) -> dict[str, object]:
    start = stdout.find("{")
    if start < 0:
        raise ValueError("audit command did not return JSON")
    data = json.loads(stdout[start:])
    if not isinstance(data, dict):
        raise ValueError("audit JSON was not an object")
    return data


def collect_npm(app: str) -> tuple[list[Finding], dict[str, int]]:
    proc = _run(["npm", "audit", "--json"], REPO / app, timeout=180)
    if not proc.stdout.strip():
        raise RuntimeError(f"npm audit in {app} produced no output: {proc.stderr.strip()[:400]}")
    data = _json_from_stdout(proc.stdout)
    meta = data.get("metadata")
    counts = {"info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0, "total": 0}
    if isinstance(meta, dict):
        vulns = meta.get("vulnerabilities")
        if isinstance(vulns, dict):
            for key in counts:
                counts[key] = int(vulns.get(key) or 0)
    return npm_findings(data, app), counts


def run_server_audit() -> tuple[list[Finding], int, int]:
    # uv export -o - writes a file named "-", so the pins go to a temp path.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        export_path = Path(handle.name)
    export = _run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-local",
            "--no-hashes",
            "--no-annotate",
            "-o",
            str(export_path),
        ],
        REPO / "OpenMW",
        timeout=120,
    )
    if export.returncode != 0:
        export_path.unlink(missing_ok=True)
        raise RuntimeError(f"uv export failed: {export.stderr.strip()[:400]}")
    pins = deduped_requirements(export_path.read_text(encoding="utf-8"))
    export_path.unlink(missing_ok=True)
    pin_count = len([line for line in pins.splitlines() if line.strip()])
    if pin_count < 10 or "cryptography==" not in pins or "pillow==" not in pins:
        raise RuntimeError("server requirements export did not contain the runtime pins")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(pins)
        req_path = Path(handle.name)
    try:
        audit = _run(
            [
                "uvx",
                "--python",
                "3.12",
                "pip-audit",
                "-r",
                str(req_path),
                "--no-deps",
                "--disable-pip",
                "--progress-spinner",
                "off",
                "--format",
                "json",
            ],
            REPO / "OpenMW",
            timeout=180,
        )
    finally:
        req_path.unlink(missing_ok=True)
    if not audit.stdout.strip():
        raise RuntimeError(f"pip-audit produced no output: {audit.stderr.strip()[:400]}")
    data = _json_from_stdout(audit.stdout)
    findings = pip_findings(data)
    filled: list[Finding] = []
    cache: dict[str, dict[str, object] | None] = {}

    def fetch(ident: str) -> dict[str, object] | None:
        if ident not in cache:
            cache[ident] = fetch_osv(ident)
        return cache[ident]

    for finding in findings:
        filled.append(
            Finding(
                source=finding.source,
                package=finding.package,
                version=finding.version,
                severity=lookup_severity(finding.ids, fetch),
                ids=finding.ids,
                summary=finding.summary,
            )
        )
    return filled, len(findings), pin_count


def _format_finding(finding: Finding) -> str:
    ident = finding.ids[0] if finding.ids else "no-id"
    return (
        f"{finding.source} {finding.severity} {finding.package} {finding.version} "
        f"{ident} {finding.summary}"
    )


def self_test() -> None:
    """Lock the ignore-list and severity rules without calling the network."""
    ignores = load_ignores(
        "# comment\n\nGHSA-g6cj-pr64-35w5 no patched wheel for this interpreter\n"
    )
    assert "GHSA-g6cj-pr64-35w5" in ignores
    try:
        load_ignores("GHSA-g6cj-pr64-35w5\n")
    except ValueError:
        pass
    else:
        raise AssertionError("a row with no reason must fail")
    try:
        load_ignores("not-an-id because\n")
    except ValueError:
        pass
    else:
        raise AssertionError("a bad id must fail")

    high = Finding("server", "cryptography", "49.0.0", "high", ("GHSA-g6cj-pr64-35w5",), "x")
    critical = Finding(
        "apps/web", "next", "16.3.8", "critical", ("111", "GHSA-2464-fhr4-w9xw"), "x"
    )
    moderate = Finding("apps/web", "next", "16.4.1", "moderate", ("222",), "x")
    unknown = Finding("server", "pillow", "12.2.0", "unknown", ("PYSEC-2026-1",), "x")
    blocked, skipped = partition([high, critical, moderate, unknown], ignores)
    assert [item.package for item in blocked] == ["next", "pillow"]
    assert skipped[0][0].package == "cryptography"
    assert partition([moderate], {}) == ([], [])

    assert severity_from_osv({"database_specific": {"severity": "HIGH"}, "severity": []}) == "high"
    assert severity_from_osv({"severity": [{"type": "CVSS_V3", "score": "9.8"}]}) == "critical"
    assert severity_from_osv({"severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N"}]}) is None
    assert (
        lookup_severity(
            ("PYSEC-2026-3552", "GHSA-g6cj-pr64-35w5"),
            lambda ident: (
                {"database_specific": {"severity": "HIGH"}}
                if ident.startswith("GHSA")
                else {"severity": []}
            ),
        )
        == "high"
    )
    pins = deduped_requirements(
        "# header\nfilelock==4.2.1 ; python_full_version >= '3.11'\nfilelock==4.2.1\n"
    )
    assert pins == "filelock==4.2.1\n"
    sample = {
        "vulnerabilities": {
            "next": {
                "range": "<=16.4.0",
                "via": [
                    {
                        "severity": "critical",
                        "title": "RCE",
                        "source": 1242816,
                        "url": "https://github.com/advisories/GHSA-2464-fhr4-w9xw",
                    }
                ],
            }
        }
    }
    parsed = npm_findings(sample, "apps/web")
    assert parsed[0].ids == ("1242816", "GHSA-2464-fhr4-w9xw")
    assert parsed[0].severity == "critical"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on high or critical advisories")
    parser.add_argument("--self-test", action="store_true", help="run the built-in checks and exit")
    args = parser.parse_args(argv)
    self_test()
    if args.self_test:
        print("ci_audit self-test passed")
        return 0

    ignores = load_ignores(IGNORE_PATH.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    web, web_counts = collect_npm("apps/web")
    shell, shell_counts = collect_npm("apps/shell")
    server, server_count, pin_count = run_server_audit()
    findings.extend(web)
    findings.extend(shell)
    findings.extend(server)

    blocked, skipped = partition(findings, ignores)
    print(
        "web npm "
        f"critical={web_counts['critical']} high={web_counts['high']} "
        f"total={web_counts['total']}"
    )
    print(
        "shell npm "
        f"critical={shell_counts['critical']} high={shell_counts['high']} "
        f"total={shell_counts['total']}"
    )
    print(f"server pip-audit vulnerabilities={server_count} pins={pin_count}")
    for finding, reason in skipped:
        print(f"ignored {_format_finding(finding)} reason={reason}")
    if blocked:
        print(f"{len(blocked)} high or critical advisory(s) are not in the ignore list:")
        for finding in blocked:
            print(_format_finding(finding))
        return 1
    print("audit gate passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"audit gate failed: {exc}", file=sys.stderr)
        sys.exit(1)

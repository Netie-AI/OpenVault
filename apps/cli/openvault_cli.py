#!/usr/bin/env python3
"""OpenVault CLI - run the real app stack (apps/web + custody API).

  openvault up          # OpenMW custody API (:5000) + Next OpenVault app (:3010) + browser
  openvault app         # Electron desktop shell -> wraps the same two processes
  openvault doctor      # environment preflight (filesystem, node, npm, ports)
  openvault demo        # mock-health demo: API + app + open browser
  openvault demo-path   # scripted vault->FreeRoute refuse->ship allow->deny (mocks only)

Topology is deliberately two processes. The vendor trees under ``vendor/`` are
upstream *sources we copied from*, not services we run.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def ensure_utf8_stdio() -> None:
    """Make stdout/stderr survive a console that is not UTF-8.

    R-0012 has two halves and this repo only had the fragile one. Substituting
    plain characters holds exactly as long as nobody types an em dash again,
    and it cannot protect text produced elsewhere and printed through here.

    The failure it prevents: spawned by a launcher with redirected or detached
    stdout on Windows, Python picks the ANSI codepage (cp1252). One
    unencodable character then raises UnicodeEncodeError at the print, and the
    process dies before doing any work - which is how the demo stack came up
    without its custody API and failed downstream with an unrelated-looking
    error about /keys/services.

    ``errors="replace"`` is deliberate: a stray character should degrade to a
    question mark, never take the process down. Called before argparse, since
    --help renders the module docstring.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # not a TextIOWrapper (captured, or replaced)
            continue
        # A stream that refuses reconfiguration is not worth dying over - the
        # whole point of this function is that output can never be fatal.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parents[2]
OPENMW = ROOT / "OpenMW"
WEB = ROOT / "apps" / "web"
SHELL = ROOT / "apps" / "shell"


def _home_dir() -> Path:
    """The vault home this launcher and its children will agree on."""
    return Path(os.environ.get("OPENVAULT_HOME", str(ROOT / ".openvault")))


def _resolve_port(service_key: str, env_var: str, default: int) -> int:
    """Environment, then the operator's saved choice, then the default.

    Deliberately a small duplicate of openmw.openvault.ports.resolve_port:
    this launcher runs on the system Python and cannot import the OpenMW
    package. The two are kept in step by
    tests/test_ports.py::test_the_launcher_resolver_agrees_with_the_package,
    so the duplication is a gated invariant rather than a drift risk.
    """
    from_env = os.environ.get(env_var, "").strip()
    if from_env:
        try:
            return int(from_env)
        except ValueError:
            pass
    saved = _home_dir() / "ports.json"
    if saved.is_file():
        try:
            data = json.loads(saved.read_text(encoding="utf-8"))
            value = (data.get("ports") or {}).get(service_key)
            if value is not None:
                return int(value)
        except (OSError, ValueError, TypeError):
            pass
    return default


API_PORT = _resolve_port("api", "OPENVAULT_API_PORT", 5000)
WEB_PORT = _resolve_port("web", "OPENVAULT_WEB_PORT", 3010)


def _wait(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _popen(
    args: list[str],
    cwd: Path,
    env: dict | None = None,
    *,
    log_path: Path | None = None,
) -> subprocess.Popen:
    e = os.environ.copy()
    if env:
        e.update(env)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
        return subprocess.Popen(
            args,
            cwd=str(cwd),
            env=e,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
    return subprocess.Popen(
        args,
        cwd=str(cwd),
        env=e,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _npm() -> str:
    return shutil.which("npm") or ("npm.cmd" if os.name == "nt" else "npm")


def _ensure_web_deps() -> None:
    if (WEB / "node_modules" / "next").is_dir():
        return
    print(f"Installing {WEB} dependencies (npm)...")
    subprocess.check_call([_npm(), "install", "--no-audit", "--no-fund"], cwd=str(WEB))


class PortBlockedError(Exception):
    """A foreign application holds a port the stack needs."""


def _port_owner_report(env: dict[str, str]) -> tuple[int, str]:
    """Ask OpenMW who holds the stack ports. Returns (exit code, output).

    Shelling out rather than importing: this launcher runs on the system
    Python, where psutil is not guaranteed, while OpenMW already declares it.
    One implementation, in the package that owns the vault.
    """
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
    try:
        proc = subprocess.run(
            [uv, "run", "--no-sync", "openmw", "ports"],
            cwd=str(OPENMW),
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (0, f"(could not check port owners: {exc})")
    return (proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def _start_api(env: dict[str, str], *, mock_health: bool = False) -> subprocess.Popen | None:
    if _port_busy(API_PORT):
        # Something is on the port. Reusing our own already-running server is
        # legitimate work; adopting a stranger's is how the console ends up
        # talking to a server that is not ours - or to another stack pointed at
        # a different vault. Find out which before deciding.
        code, report = _port_owner_report(env)
        if code != 0:
            raise PortBlockedError(report.rstrip())
        print(f"Custody API already listening on :{API_PORT} - reusing it.")
        return None
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
    # --no-sync avoids Windows file locks on openmw.exe during rebuild mid-flight.
    args = [
        uv,
        "run",
        "--no-sync",
        "openmw",
        "console",
        "--host",
        "127.0.0.1",
        "--port",
        str(API_PORT),
        "--cortex-url",
        env["CORTEX_URL"],
        "--openide-url",
        env["OPENIDE_URL"],
        "--no-open-browser",
    ]
    if mock_health:
        args.append("--mock-health")
    log_path = Path(env["OPENVAULT_HOME"]) / "logs" / "console.up.log"
    print(f"Custody API log -> {log_path}")
    return _popen(args, OPENMW, env, log_path=log_path)


def _start_web(env: dict[str, str], *, prod: bool = False) -> subprocess.Popen:
    _ensure_web_deps()
    script = "start" if prod else "dev"
    # Log to a file like the API child does. Without this the web server's
    # stdout and stderr went to DEVNULL, so `next start` failing with "Could
    # not find a production build" left no trace anywhere, and the user was
    # told to run `openvault doctor` - which did not check for a build either.
    log_path = Path(env["OPENVAULT_HOME"]) / "logs" / "web.up.log"
    return _popen([_npm(), "run", script], WEB, {**env, "PORT": str(WEB_PORT)}, log_path=log_path)


def _base_env() -> dict[str, str]:
    home = os.environ.get("OPENVAULT_HOME", str(ROOT / ".openvault"))
    return {
        "OPENVAULT_HOME": home,
        "OPENVAULT_APP_URL": f"http://127.0.0.1:{WEB_PORT}/",
        "CORTEX_URL": os.environ.get("CORTEX_URL", "http://127.0.0.1:8010"),
        "OPENIDE_URL": os.environ.get("OPENIDE_URL", "http://127.0.0.1:8765"),
        "PATH": os.environ.get("PATH")
        or next((v for k, v in os.environ.items() if k.upper() == "PATH"), ""),
    }


def cmd_doctor(_: argparse.Namespace) -> int:
    ok = True

    def line(label: str, value: str, good: bool = True) -> None:
        nonlocal ok
        if not good:
            ok = False
        print(f"  {'OK ' if good else 'FAIL'}  {label:<22} {value}")

    print("OpenVault doctor\n")
    print(" paths")
    line("repo root", str(ROOT), ROOT.is_dir())
    line("OpenMW", str(OPENMW), OPENMW.is_dir())
    line("apps/web", str(WEB), (WEB / "package.json").is_file())

    print(" toolchain")
    node = shutil.which("node")
    line("node", node or "not found", bool(node))
    npm = shutil.which("npm")
    line("npm", npm or "not found", bool(npm))
    line(
        "web node_modules",
        "present" if (WEB / "node_modules" / "next").is_dir() else "missing - run `openvault up`",
        (WEB / "node_modules" / "next").is_dir(),
    )

    print(" filesystem")
    if os.name == "nt":
        drive = str(ROOT.drive).rstrip(":") or "C"
        try:
            fs = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Volume -DriveLetter {drive}).FileSystemType",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            ).stdout.strip()
        except Exception:
            fs = "unknown"
        note = (
            "npm only - no hardlinks/symlinks, bun & pnpm cannot install here"
            if fs.upper() == "EXFAT"
            else ""
        )
        line("volume", f"{drive}: {fs} {note}".rstrip(), True)

    build_id = WEB / ".next" / "BUILD_ID"
    line(
        "web prod build",
        "present" if build_id.is_file() else "missing - `--prod` will fail, plain `up` is fine",
        True,
    )

    print(" ports")
    line(f"custody API :{API_PORT}", "listening" if _port_busy(API_PORT) else "free")
    line(f"web app :{WEB_PORT}", "listening" if _port_busy(WEB_PORT) else "free")

    print("\n" + ("All good." if ok else "Some checks failed - see FAIL lines above."))
    return 0 if ok else 1


def _run_stack(args: argparse.Namespace, *, mock_health: bool, open_browser: bool) -> int:
    procs: list[subprocess.Popen] = []
    env = _base_env()
    try:
        api = _start_api(env, mock_health=mock_health)
    except PortBlockedError as blocked:
        print(str(blocked))
        return 1
    if api is not None:
        procs.append(api)
    if not _wait(f"http://127.0.0.1:{API_PORT}/api/healthz", 90):
        print("Custody API did not come up. Run `openvault doctor`.")
        log_path = Path(env["OPENVAULT_HOME"]) / "logs" / "console.up.log"
        if log_path.is_file():
            print(f"--- last 40 lines of {log_path} ---")
            try:
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                print("\n".join(lines[-40:]))
            except OSError as exc:
                print(f"(could not read log: {exc})")
        for p in procs:
            p.terminate()
        return 1

    if _port_busy(WEB_PORT):
        print(f"Web app already listening on :{WEB_PORT} - reusing it.")
    else:
        procs.append(_start_web(env, prod=getattr(args, "prod", False)))

    print(f"OpenVault API  http://127.0.0.1:{API_PORT}/")
    print(f"OpenVault App  http://127.0.0.1:{WEB_PORT}/   <- use this")
    if not _wait(f"http://127.0.0.1:{WEB_PORT}/", 120):
        print("Web app did not come up within 120s.")
        web_log = Path(env["OPENVAULT_HOME"]) / "logs" / "web.up.log"
        if web_log.is_file():
            print(f"--- last 40 lines of {web_log} ---")
            try:
                lines = web_log.read_text(encoding="utf-8", errors="replace").splitlines()
                print("\n".join(lines[-40:]))
            except OSError as exc:
                print(f"(could not read log: {exc})")
        if getattr(args, "prod", False) and not (WEB / ".next" / "BUILD_ID").is_file():
            print(
                "\n--prod runs `next start`, which needs a production build that does "
                "not exist here.\nRun:  npm --prefix apps/web run build   (or drop --prod)"
            )
        for p in procs:
            p.terminate()
        return 1
    print("Ready.")
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{WEB_PORT}/")

    try:
        while True:
            time.sleep(2)
            if any(p.poll() is not None for p in procs):
                break
    except KeyboardInterrupt:
        pass
    for p in procs:
        p.terminate()
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    open_browser = not getattr(args, "no_open_browser", False)
    return _run_stack(args, mock_health=False, open_browser=open_browser)


def cmd_demo(args: argparse.Namespace) -> int:
    """Demo mode: mock device health so the UI always has numbers to show."""
    print("Demo mode - mock health devices, live vault/ship/gate APIs.")
    open_browser = not getattr(args, "no_open_browser", False)
    return _run_stack(args, mock_health=True, open_browser=open_browser)


def cmd_demo_path(args: argparse.Namespace) -> int:
    """Auto-safe one-seat path: vault -> FreeRoute refuse -> ship allow -> gate deny.

    In-process mocks/simulate only. Does not start the UI stack and does not
    claim HT1-HT5. See docs/ONE_SEAT_DEMO.md.
    """
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
    script = OPENMW / "scripts" / "one_seat_demo.py"
    cmd = [uv, "run", "--no-sync", "python", str(script)]
    if getattr(args, "out", None):
        cmd.extend(["--out", str(Path(args.out))])
    print("One-seat demo-path - mocks/simulate ONLY (no live hosts / paid keys).")
    return subprocess.call(cmd, cwd=str(OPENMW))


def cmd_app(_: argparse.Namespace) -> int:
    if not (SHELL / "node_modules").is_dir():
        subprocess.check_call([_npm(), "install", "--no-audit", "--no-fund"], cwd=str(SHELL))
    return subprocess.call([_npm(), "run", "dev"], cwd=str(SHELL))


def main() -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="openvault")
    sub = parser.add_subparsers(dest="cmd", required=True)

    up = sub.add_parser("up", help="Start custody API + OpenVault app + open browser")
    up.add_argument("--prod", action="store_true", help="serve the production build")
    up.add_argument(
        "--no-open-browser",
        action="store_true",
        help="do not open http://127.0.0.1:3010/ after ready",
    )
    up.set_defaults(func=cmd_up)

    demo = sub.add_parser("demo", help="Full demo: mock health + API + app + browser")
    demo.add_argument("--prod", action="store_true", help="serve the production build")
    demo.add_argument(
        "--no-open-browser",
        action="store_true",
        help="do not open http://127.0.0.1:3010/ after ready",
    )
    demo.set_defaults(func=cmd_demo)

    demo_path = sub.add_parser(
        "demo-path",
        help="Scripted one-seat path (vault->FreeRoute refuse->ship allow->deny); mocks only",
    )
    demo_path.add_argument(
        "--out",
        help="JSON evidence path (default: OPENVAULT_HOME/one_seat_demo_evidence.json)",
    )
    demo_path.set_defaults(func=cmd_demo_path)

    sub.add_parser("app", help="Electron desktop shell").set_defaults(func=cmd_app)
    sub.add_parser("doctor", help="Environment preflight").set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

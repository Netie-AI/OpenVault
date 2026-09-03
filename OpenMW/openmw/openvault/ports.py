"""Who is on our ports, and which port did the operator actually choose.

Three things kept going wrong and all three are the same missing answer:

1. A launcher found something on :5000 and silently reused it. Every launcher
   has an "already listening - reusing it" branch, so the stack would adopt a
   foreign server, or another stack's server pointed at a different vault, and
   report success. The user saw an app that painted and then failed.
2. When a port was busy the message was "port busy". That is not actionable.
   The operator needs the application's name and path so they can go close it.
3. Changing a port meant editing source, so nobody did.

This module answers all three, and deliberately does not kill anything: naming
the process is the operator's decision support, not our licence to terminate
somebody else's work.

Port identification uses ``psutil``, already a dependency (pyproject.toml).
Verified on Windows 11 without elevation: all 54 listening sockets resolved to
a process name and executable path, including system processes.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

from openmw.openvault.paths import openvault_home

log = structlog.get_logger()

#: Who owns the service, and therefore whether we may reconfigure its port.
#: PRODUCT_ROLES puts Cortex and AirGPT in other repos: we report on their
#: ports so a conflict is legible, and we never rewrite their configuration.
Owner = Literal["openvault", "external"]


@dataclass(frozen=True)
class Service:
    key: str
    label: str
    default_port: int
    env_var: str
    owner: Owner
    health_path: str = ""
    #: Set when the port cannot be changed from here yet. Surfaced to the user
    #: rather than hidden, because a setting that silently does nothing is
    #: worse than one that refuses.
    fixed_reason: str = ""


SERVICES: tuple[Service, ...] = (
    Service(
        key="api",
        label="OpenVault custody API",
        default_port=5000,
        env_var="OPENVAULT_API_PORT",
        owner="openvault",
        health_path="/api/healthz",
    ),
    Service(
        key="web",
        label="OpenVault console",
        default_port=3010,
        env_var="OPENVAULT_WEB_PORT",
        owner="openvault",
        health_path="/",
        fixed_reason=(
            "apps/web/package.json hardcodes --port 3010 in its dev and start "
            "scripts, so this cannot take effect until those read the env var"
        ),
    ),
    Service(
        key="cortex",
        label="Cortex engine",
        default_port=8010,
        env_var="CORTEX_PORT",
        owner="external",
        health_path="/health",
        fixed_reason="Cortex lives in D:\\Cortex - OpenVault reports, never reconfigures",
    ),
    Service(
        key="openide",
        label="AirGPT / FreeIDE bridge",
        default_port=8765,
        env_var="OPENIDE_PORT",
        owner="external",
        health_path="/health",
        fixed_reason="AirGPT owns this port - OpenVault reports, never reconfigures",
    ),
)

SERVICES_BY_KEY = {s.key: s for s in SERVICES}


def ports_file() -> Path:
    """Where the operator's port choices persist.

    Under OPENVAULT_HOME, deliberately: a port setting belongs with the vault
    it serves, so pointing at a different home gives you that home's stack
    rather than leaking one stack's ports into another.
    """
    return openvault_home() / "ports.json"


def _load_choices() -> dict[str, int]:
    path = ports_file()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A corrupt settings file must not stop the stack from starting; fall
        # back to defaults and say so, rather than failing to boot (R-0011).
        log.warning("ports_file_unreadable", path=str(path), error=str(exc))
        return {}
    out: dict[str, int] = {}
    for key, value in (raw.get("ports") or {}).items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            log.warning("ports_file_bad_entry", key=key, value=value)
    return out


def resolve_port(service_key: str, *, override: int | None = None) -> int:
    """The port this service should use, by precedence.

    explicit argument > environment variable > persisted choice > default.
    The explicit argument wins so a one-off ``--port`` still works without
    rewriting the operator's saved preference.
    """
    service = SERVICES_BY_KEY[service_key]
    if override is not None:
        return int(override)
    from_env = os.environ.get(service.env_var, "").strip()
    if from_env:
        try:
            return int(from_env)
        except ValueError:
            log.warning("ports_env_not_a_number", var=service.env_var, value=from_env)
    saved = _load_choices().get(service_key)
    if saved is not None:
        return saved
    return service.default_port


def set_port(service_key: str, port: int) -> Path:
    """Persist a port choice for this device. Returns the file written."""
    service = SERVICES_BY_KEY[service_key]
    if service.owner != "openvault":
        raise ValueError(f"{service.label} is not ours to reconfigure - {service.fixed_reason}")
    if not (1 <= int(port) <= 65535):
        raise ValueError(f"port must be 1-65535, got {port}")
    path = ports_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, object] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
    ports = dict(current.get("ports") or {})
    ports[service_key] = int(port)
    current["ports"] = ports
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    log.info("ports_choice_saved", service=service_key, port=int(port), path=str(path))
    return path


@dataclass(frozen=True)
class Listener:
    """The process holding a port, as far as this user is allowed to see."""

    port: int
    pid: int | None = None
    name: str = ""
    exe: str = ""
    cmdline: str = ""
    #: True when we could see a listener but not attribute it - the honest
    #: answer on a locked-down box, and not the same as "nothing is there".
    unattributed: bool = False

    def describe(self) -> str:
        if self.unattributed:
            return f"an application this account cannot inspect (port {self.port})"
        who = self.name or "unknown process"
        if self.pid:
            who = f"{who} (pid {self.pid})"
        return f"{who}{chr(10)}    {self.exe}" if self.exe else who


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    """Is anything accepting connections here? Cheap, no privileges needed."""
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def describe_listener(port: int) -> Listener | None:
    """Name the application holding ``port``, or None if it is free.

    Returns a Listener with ``unattributed`` set when something is clearly
    listening but the OS will not tell us whose it is. Saying "something is
    there but I cannot see what" is useful; claiming the port is free is not.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a declared dependency
        return Listener(port=port, unattributed=True) if port_is_open(port) else None

    found: Listener | None = None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr or conn.laddr.port != port:
                continue
            if conn.pid is None:
                found = Listener(port=port, unattributed=True)
                continue
            try:
                proc = psutil.Process(conn.pid)
                return Listener(
                    port=port,
                    pid=conn.pid,
                    name=proc.name(),
                    exe=_safe(proc.exe),
                    cmdline=" ".join(proc.cmdline()[:6]) if _can(proc.cmdline) else "",
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                found = Listener(port=port, pid=conn.pid, unattributed=True)
    except (psutil.AccessDenied, OSError) as exc:
        log.warning("port_scan_denied", port=port, error=str(exc))
        return Listener(port=port, unattributed=True) if port_is_open(port) else None

    if found is not None:
        return found
    # psutil saw nothing, but a socket probe is the authority on reachability.
    return Listener(port=port, unattributed=True) if port_is_open(port) else None


def _safe(getter) -> str:
    try:
        return getter() or ""
    except Exception:
        return ""


def _can(getter) -> bool:
    try:
        getter()
        return True
    except Exception:
        return False


@dataclass
class PortStatus:
    service: Service
    port: int
    state: Literal["free", "ours", "foreign"]
    listener: Listener | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.state == "foreign"


#: Local health check. A dead socket accepts the connection and then never
#: answers, so a generous timeout costs seconds on every single check; the
#: original 1.5s measured at 2.3s per dead port. Loopback health has no honest
#: reason to be slow.
_HEALTH_TIMEOUT_S = 0.5


def _looks_like_openvault(port: int, health_path: str) -> bool:
    """Is the thing on this port a working server of ours, or a stranger's app?

    Only its own answer counts. An earlier version also accepted the process
    command line as evidence, which was wrong in a way worth recording: every
    process launched from ``OpenMW/.venv`` has "openmw" in its argv, so a
    stranger's python script would have been adopted as ours - silently
    reusing a foreign server, which is the exact bug this module exists to
    stop.

    A server that does not answer within the timeout is treated as not ours.
    That is deliberate rather than lax: something we cannot get a health
    response from is not something we should be silently reusing either, and
    the launcher - not this function - is the right place to wait for a server
    that is merely slow to start.
    """
    if not health_path:
        return False
    try:
        import httpx

        resp = httpx.get(f"http://127.0.0.1:{port}{health_path}", timeout=_HEALTH_TIMEOUT_S)
    except Exception:
        return False
    if resp.status_code >= 500:
        return False
    body = resp.text[:2000].lower()
    return "openvault" in body or "openmw" in body


def inspect(service_key: str, *, override: int | None = None) -> PortStatus:
    """Resolve this service's port and say who, if anyone, is on it."""
    service = SERVICES_BY_KEY[service_key]
    port = resolve_port(service_key, override=override)
    listener = describe_listener(port)
    if listener is None:
        return PortStatus(service=service, port=port, state="free")
    if service.owner == "openvault" and _looks_like_openvault(port, service.health_path):
        return PortStatus(
            service=service,
            port=port,
            state="ours",
            listener=listener,
            notes=["an OpenVault server is already running here; it will be reused"],
        )
    if service.owner == "external":
        # Somebody else's service on somebody else's port is the normal case.
        return PortStatus(service=service, port=port, state="ours", listener=listener)
    return PortStatus(service=service, port=port, state="foreign", listener=listener)


def inspect_all(*, overrides: dict[str, int] | None = None) -> list[PortStatus]:
    overrides = overrides or {}
    return [inspect(s.key, override=overrides.get(s.key)) for s in SERVICES]


def blocking_message(status: PortStatus) -> str:
    """What to tell the operator when a foreign application holds our port."""
    listener = status.listener
    who = listener.describe() if listener else "an unidentified application"
    lines = [
        f"Port {status.port} is being used by another application, so the "
        f"{status.service.label} cannot start.",
        "",
        f"  Blocking application: {who}",
        "",
        "Close that application, or move OpenVault to a different port:",
        f"  openvault ports --set {status.service.key}=<port>",
        "",
        "That choice is saved and used on every later start.",
    ]
    if status.service.fixed_reason:
        lines.insert(-1, f"  Note: {status.service.fixed_reason}.")
    return "\n".join(lines)

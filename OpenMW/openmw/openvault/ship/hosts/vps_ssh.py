"""VPS over SSH — the host where OpenVault does the managing.

Every other adapter here hands the artifact to somebody else's control plane
(Cloudflare, Coolify, Netlify). This one is the "we host it" shape the founder
asked for: the user brings one box they rent and one domain they own, and
OpenVault does what a PaaS does on top of it —

  detect the stack -> build it -> run N replicas -> put a load balancer with
  automatic TLS in front -> swap traffic only after the new replicas answer.

Design constraints that are not negotiable:

* **Never report success we did not observe.** The deploy is only ``ok`` once
  something answered on the URL we are about to hand back. A container that
  started is not a site that works.
* **Zero-downtime or refuse.** New replicas come up on a second port block
  (blue/green), get health-checked, and only then does the proxy switch. If the
  new colour never answers, the old one is still serving and we return a
  failure with the build log.
* **Secrets never touch argv or logs.** Env values go over the SSH pipe into a
  0600 file on the box; ``ps`` on the VPS and our own logs see names only.
* Every value interpolated into a remote shell is ``shlex.quote``d, and the
  project/hostname inputs are normalized to a safe charset before that.

The transport is the system OpenSSH client — no new dependency, and the user's
existing key/agent setup keeps working. ``SshRunner`` is the only thing that
touches ``subprocess``; tests drive the adapter through a fake with the same
two methods.
"""

from __future__ import annotations

import io
import os
import re
import shlex
import socket
import subprocess
import tarfile
import time
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import structlog

from openmw.openvault.ship.hosts.base import DeployResult, DomainResult, Preflight

log = structlog.get_logger()

#: Where every OpenVault-managed site lives on the box. One directory per
#: project so a second app on the same VPS cannot collide with the first.
REMOTE_ROOT = "/srv/openvault"
#: Caddy reads every file in here via one import line we append once.
CADDY_SITE_DIR = "/etc/caddy/openvault.d"
CADDY_MAIN = "/etc/caddy/Caddyfile"
CADDY_IMPORT_LINE = f"import {CADDY_SITE_DIR}/*.caddy"

_SSH_CONNECT_TIMEOUT_S = 10
_RUN_TIMEOUT_S = 180.0
_BUILD_TIMEOUT_S = 1800.0
_HEALTH_ATTEMPTS = 30
_HEALTH_SLEEP_S = 2.0
_PUBLIC_PROBE_TIMEOUT_S = 20.0
#: Ports we hand to containers. Bound to 127.0.0.1 only — the proxy is the
#: single public listener, so an app cannot accidentally expose itself.
_PORT_WINDOW_START = 21000
_PORT_WINDOW_SIZE = 200
_COLOUR_STRIDE = 50

_SAFE_NAME = re.compile(r"[^a-z0-9-]+")
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class VpsConfigError(ValueError):
    """The request itself is unusable — bad hostname, bad env name, no host."""


def normalize_project_name(raw: str) -> str:
    """Lowercase kebab, safe as a container name, a directory and a file name."""
    name = _SAFE_NAME.sub("-", (raw or "").strip().lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    return name or "openvault-app"


def normalize_hostname(raw: str) -> str:
    """Return a validated bare hostname, or "" when it is not one.

    Anything that would let a caller smuggle shell metacharacters into a Caddy
    site block or a certificate request must fail here, not later.
    """
    host = (raw or "").strip().lower().rstrip(".")
    if host.startswith("http://"):
        host = host[7:]
    elif host.startswith("https://"):
        host = host[8:]
    host = host.split("/")[0]
    if not host or not _HOSTNAME_RE.match(host):
        return ""
    return host


def port_base(project: str, colour: str) -> int:
    """Deterministic per-project port block, so a redeploy reuses its own ports.

    CRC32 rather than ``hash()`` — the value has to survive process restarts,
    and ``PYTHONHASHSEED`` randomizes ``hash()`` for str.
    """
    digest = zlib.crc32(normalize_project_name(project).encode("utf-8"))
    base = _PORT_WINDOW_START + (digest % _PORT_WINDOW_SIZE) * (_COLOUR_STRIDE * 2)
    return base + (_COLOUR_STRIDE if colour == "green" else 0)


def other_colour(colour: str) -> str:
    return "blue" if colour == "green" else "green"


@dataclass(frozen=True)
class SshTarget:
    host: str
    user: str = "root"
    port: int = 22
    key_path: str = ""

    def label(self) -> str:
        return f"{self.user}@{self.host}:{self.port}"


@dataclass
class RunResult:
    code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def out(self) -> str:
        return self.stdout.strip()

    def tail(self, limit: int = 400) -> str:
        """Short, human-readable failure text for a step detail."""
        text = (self.stderr.strip() or self.stdout.strip()).replace("\r", "")
        return text[-limit:] if len(text) > limit else text


class RemoteRunner(Protocol):
    """The two operations the adapter needs from a box. Fakeable in tests."""

    def run(self, script: str, *, timeout_s: float | None = None) -> RunResult: ...

    def put_bytes(self, remote_path: str, data: bytes, *, mode: str = "600") -> RunResult: ...


class SshRunner:
    """Run shell on the VPS through the system OpenSSH client.

    Scripts travel on stdin (``bash -s``) so nothing we generate has to survive
    a second round of local command-line quoting on Windows.
    """

    def __init__(self, target: SshTarget, *, extra_opts: Sequence[str] = ()) -> None:
        self.target = target
        self._extra = list(extra_opts)

    def _argv(self, remote_command: str) -> list[str]:
        argv = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}",
            "-p",
            str(self.target.port),
        ]
        if self.target.key_path:
            argv += ["-i", self.target.key_path, "-o", "IdentitiesOnly=yes"]
        argv += self._extra
        argv += [f"{self.target.user}@{self.target.host}", remote_command]
        return argv

    def _spawn(self, remote_command: str, data: bytes, timeout_s: float) -> RunResult:
        try:
            # argv list, never shell=True — nothing here goes through a local shell.
            proc = subprocess.run(
                self._argv(remote_command),
                input=data,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
        except FileNotFoundError:
            return RunResult(
                code=127,
                stderr=(
                    "no `ssh` client on this machine — install OpenSSH "
                    "(Windows: Settings > Optional features > OpenSSH Client)"
                ),
            )
        except subprocess.TimeoutExpired:
            return RunResult(code=124, stderr=f"SSH command timed out after {int(timeout_s)}s")
        return RunResult(
            code=proc.returncode,
            stdout=proc.stdout.decode("utf-8", "replace"),
            stderr=proc.stderr.decode("utf-8", "replace"),
        )

    def run(self, script: str, *, timeout_s: float | None = None) -> RunResult:
        body = "set -o pipefail\nset -e\n" + script
        return self._spawn("bash -s", body.encode("utf-8"), timeout_s or _RUN_TIMEOUT_S)

    def put_bytes(self, remote_path: str, data: bytes, *, mode: str = "600") -> RunResult:
        """Stream bytes into a remote file. Content never appears in argv."""
        quoted = shlex.quote(remote_path)
        parent = shlex.quote(str(Path(remote_path).parent).replace("\\", "/"))
        # umask before the redirect so the file is never briefly world-readable.
        written = self._spawn(
            f"sh -c 'umask 077; mkdir -p {parent}; cat > {quoted}'",
            data,
            _RUN_TIMEOUT_S,
        )
        if not written.ok:
            return written
        return self.run(f"chmod {shlex.quote(mode)} {quoted}")


# ─── Rendering: everything that becomes a file on the box ────────────────────


def render_env_file(env: Mapping[str, str]) -> str:
    """``KEY=value`` lines for ``docker run --env-file``.

    Docker's env-file parser is not a shell: it takes the raw rest of the line
    and cannot express a newline. A value containing one is a config error, not
    something to silently truncate.
    """
    lines: list[str] = []
    for name, value in env.items():
        if not _ENV_NAME_RE.match(name):
            raise VpsConfigError(f"invalid env name {name!r}; use [A-Za-z_][A-Za-z0-9_]*")
        if "\n" in value or "\r" in value:
            raise VpsConfigError(
                f"{name}: value contains a newline, which docker --env-file cannot carry"
            )
        lines.append(f"{name}={value}")
    return "\n".join(lines) + ("\n" if lines else "")


def render_dockerfile(
    *,
    build_image: str,
    install_command: str,
    build_command: str,
    start_command: str,
    port: int,
) -> str:
    """A plain Dockerfile from what detection already knows.

    Deliberately not multi-stage: one image the user can read, `docker run`
    themselves, and keep if they ever leave us. A repo that ships its own
    Dockerfile always wins over this — see ``_ensure_dockerfile``.

    Refuses without a start command rather than emitting a container that stays
    up doing nothing: that shape passes ``docker ps`` and fails every request,
    which is the exact "looks deployed, isn't" failure this module exists to
    prevent.
    """
    if not start_command.strip():
        raise VpsConfigError(
            "no start command detected, so we cannot generate a Dockerfile — add a "
            "Dockerfile to the repo, or a start script we can detect"
        )
    image = (build_image or "").strip() or "debian:bookworm-slim"
    lines = [
        "# Generated by OpenVault FreeBuild from stack detection — edit freely,",
        "# a Dockerfile committed to the repo is used instead of this one.",
        f"FROM {image}",
        "WORKDIR /app",
        "COPY . .",
    ]
    if install_command.strip():
        lines.append(f"RUN {install_command.strip()}")
    if build_command.strip():
        lines.append(f"RUN {build_command.strip()}")
    lines += [
        f"ENV PORT={port}",
        f"EXPOSE {port}",
        f"CMD {start_command.strip()}",
    ]
    return "\n".join(lines) + "\n"


def render_caddy_site(
    *,
    hostname: str,
    site_address: str,
    static_root: str = "",
    upstreams: Sequence[int] = (),
) -> str:
    """One Caddy site block: TLS + load balancing, or static file serving.

    ``site_address`` is what Caddy listens on — a hostname (automatic TLS) or
    ``http://<ip>`` when the user has not pointed a domain here yet. Caddy will
    not issue a certificate for a bare IP, and pretending otherwise would give
    the user a site that fails to start.
    """
    header = f"# openvault-managed — {hostname or site_address}"
    if static_root:
        body = "\n".join(
            [
                "    encode zstd gzip",
                f"    root * {static_root}",
                "    try_files {path} {path}/ /index.html",
                "    file_server",
            ]
        )
    else:
        if not upstreams:
            raise VpsConfigError("a proxied site needs at least one upstream port")
        targets = " ".join(f"127.0.0.1:{p}" for p in upstreams)
        body = "\n".join(
            [
                "    encode zstd gzip",
                f"    reverse_proxy {targets} {{",
                "        lb_policy least_conn",
                "        health_uri /",
                "        health_interval 10s",
                "        health_timeout 3s",
                "        fail_duration 30s",
                "    }",
            ]
        )
    return f"{header}\n{site_address} {{\n{body}\n}}\n"


def tar_directory(local_dir: Path, *, exclude: Sequence[str] = ()) -> bytes:
    """Pack a directory into gzipped tar bytes, in-process.

    Python's ``tarfile`` rather than a local ``tar`` binary: Windows ships an
    ok-ish bsdtar but not one we want to depend on, and this keeps the upload a
    single SSH round trip.
    """
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next/cache", ".mypy_cache"}
    skip.update(exclude)
    buf = io.BytesIO()
    root = Path(local_dir)
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(part in skip for part in rel.parts):
                continue
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                continue
            tar.add(path, arcname=str(rel).replace("\\", "/"), recursive=False)
    return buf.getvalue()


@dataclass
class SitePlan:
    """What we are about to put on the box, decided before we touch it."""

    project: str
    mode: str  # "static" | "container"
    site_address: str
    hostname: str = ""
    replicas: int = 2
    port: int = 3000
    build_image: str = ""
    install_command: str = ""
    build_command: str = ""
    start_command: str = ""
    output_directory: str = ""
    root_directory: str = ""

    @property
    def remote_dir(self) -> str:
        return f"{REMOTE_ROOT}/{self.project}"

    @property
    def build_dir(self) -> str:
        """Where the detected commands run — the sub-app in a monorepo."""
        suffix = f"/{self.root_directory}" if self.root_directory else ""
        return f"{self.remote_dir}/src{suffix}"

    def facts(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "replicas": str(self.replicas),
            "app_port": str(self.port),
            "site": self.site_address,
        }


def plan_from_stack(
    stack: Any,
    *,
    project: str,
    hostname: str = "",
    server_ip: str = "",
    replicas: int = 2,
) -> SitePlan:
    """Turn a ``DetectedStack`` into a deploy plan. Pure — touches nothing."""
    name = normalize_project_name(project)
    host = normalize_hostname(hostname)
    if hostname and not host:
        raise VpsConfigError(f"{hostname!r} is not a valid hostname")
    site = host or (f"http://{server_ip}" if server_ip else "")
    if not site:
        raise VpsConfigError("need either a hostname or the server IP to build a site address")

    category = (getattr(stack, "category", "") or "").strip().lower()
    start = (getattr(stack, "start_command", "") or "").strip()
    output = (getattr(stack, "output_directory", "") or "").strip()
    # A static site is one with build output and nothing to keep running. If a
    # stack has both, the long-running process wins — serving its build output
    # as files would drop every API route it has.
    mode = "static" if (category == "static" or (output and not start)) else "container"
    return SitePlan(
        project=name,
        mode=mode,
        site_address=site,
        hostname=host,
        replicas=max(1, min(int(replicas or 1), 8)),
        port=int(getattr(stack, "production_port", 3000) or 3000),
        build_image=(getattr(stack, "build_image", "") or "").strip(),
        install_command=(getattr(stack, "install_command", "") or "").strip(),
        build_command=(getattr(stack, "build_command", "") or "").strip(),
        start_command=start,
        output_directory=output,
        root_directory=(getattr(stack, "root_directory", "") or "").strip(),
    )


# ─── The adapter ─────────────────────────────────────────────────────────────


class VpsSshAdapter:
    """One box the user rents; OpenVault runs the PaaS layer on it."""

    id = "vps_ssh"
    name = "Your VPS (SSH)"
    credential_provider = "vps_ssh"
    # We build on the user's box - that is the point of "we host it".
    needs_local_build = False

    def __init__(
        self,
        *,
        host: str | None,
        user: str = "root",
        port: int = 22,
        key_path: str = "",
        runner: RemoteRunner | None = None,
        stack: Any | None = None,
        replicas: int = 2,
        install_missing: bool = True,
    ) -> None:
        self.target = SshTarget(
            host=(host or "").strip(),
            user=(user or "root").strip() or "root",
            port=int(port or 22),
            key_path=(key_path or "").strip(),
        )
        self._runner = runner
        self._stack = stack
        self._replicas = replicas
        self._install_missing = install_missing
        self._sudo = ""
        self._facts: dict[str, str] = {}

    # -- plumbing ----------------------------------------------------------

    @property
    def runner(self) -> RemoteRunner:
        if self._runner is None:
            self._runner = SshRunner(self.target)
        return self._runner

    def _sh(self, script: str, *, timeout_s: float | None = None) -> RunResult:
        return self.runner.run(script, timeout_s=timeout_s)

    def _root(self, script: str, *, timeout_s: float | None = None) -> RunResult:
        """Run as root, using whatever escalation preflight found to work."""
        return self._sh(f"{self._sudo}{script}" if self._sudo else script, timeout_s=timeout_s)

    def _detect_escalation(self) -> str | None:
        """Return the prefix that gets us root, or None when we cannot."""
        who = self._sh("id -u")
        if not who.ok:
            return None
        if who.out == "0":
            return ""
        if self._sh("sudo -n true").ok:
            return "sudo -n "
        return None

    # -- preflight ---------------------------------------------------------

    def preflight(self) -> Preflight:
        if not self.target.host:
            return Preflight(
                ready=False,
                blocker=(
                    "Set the VPS address (OPENVAULT_VPS_HOST) — the IP or hostname of "
                    "the server you rented. OpenVault deploys to your box, not ours."
                ),
            )

        hello = self._sh("echo openvault-ok")
        if not hello.ok:
            return Preflight(
                ready=False,
                blocker=(
                    f"Cannot SSH to {self.target.label()}: {hello.tail(200)} — check the "
                    "address, that your key is on the box (ssh-copy-id), and that port "
                    f"{self.target.port} is open."
                ),
            )

        escalation = self._detect_escalation()
        if escalation is None:
            return Preflight(
                ready=False,
                blocker=(
                    f"{self.target.user} on this box is not root and has no passwordless "
                    "sudo. OpenVault installs Docker and the proxy, which needs root: "
                    "add the user to sudoers with NOPASSWD, or connect as root."
                ),
            )
        self._sudo = escalation

        facts: dict[str, str] = {"server": self.target.label(), "escalation": escalation or "root"}
        os_name = self._sh('. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"')
        if os_name.ok and os_name.out:
            facts["os"] = os_name.out
        docker = self._sh("docker --version 2>/dev/null || true")
        facts["docker"] = docker.out or "not installed"
        caddy = self._sh("caddy version 2>/dev/null || true")
        facts["proxy"] = (caddy.out.splitlines() or ["not installed"])[0]

        has_apt = self._sh("command -v apt-get >/dev/null && echo yes || echo no").out == "yes"
        missing = [
            tool
            for tool, present in (
                ("docker", bool(docker.out)),
                ("caddy", bool(caddy.out)),
            )
            if not present
        ]
        if missing and not (self._install_missing and has_apt):
            return Preflight(
                ready=False,
                blocker=(
                    f"{', '.join(missing)} missing on the box and this is not an apt "
                    "system, so OpenVault cannot install it. Install "
                    f"{' and '.join(missing)}, then retry."
                ),
                facts=facts,
            )
        if missing:
            facts["will_install"] = ", ".join(missing)

        ip = self._sh("ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}'")
        if ip.ok and ip.out:
            facts["server_ip"] = ip.out
        self._facts = facts
        return Preflight(ready=True, facts=facts)

    def server_ip(self) -> str:
        """Best known public address of the box — for the A record we ask for."""
        return self._facts.get("server_ip") or self.target.host

    # -- host preparation --------------------------------------------------

    def ensure_host(self) -> tuple[bool, str]:
        """Install Docker + Caddy and create our directories. Idempotent."""
        steps = [
            "export DEBIAN_FRONTEND=noninteractive",
            "if ! command -v curl >/dev/null; then apt-get update -qq && "
            "apt-get install -y -qq curl ca-certificates; fi",
            "if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi",
            "if ! command -v caddy >/dev/null; then "
            "apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' "
            "| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && "
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' "
            "> /etc/apt/sources.list.d/caddy-stable.list && "
            "apt-get update -qq && apt-get install -y -qq caddy; fi",
            f"mkdir -p {shlex.quote(CADDY_SITE_DIR)} {shlex.quote(REMOTE_ROOT)}",
            f"touch {shlex.quote(CADDY_MAIN)}",
            # Append the import once. grep -F so the glob is not a pattern.
            f"grep -qF {shlex.quote(CADDY_IMPORT_LINE)} {shlex.quote(CADDY_MAIN)} || "
            f"printf '\\n%s\\n' {shlex.quote(CADDY_IMPORT_LINE)} >> {shlex.quote(CADDY_MAIN)}",
            "systemctl enable --now docker >/dev/null 2>&1 || true",
            "systemctl enable --now caddy >/dev/null 2>&1 || true",
        ]
        res = self._root("\n".join(steps), timeout_s=_BUILD_TIMEOUT_S)
        if not res.ok:
            return False, f"could not prepare the server: {res.tail()}"
        return True, ""

    # -- deploy ------------------------------------------------------------

    def _current_colour(self, project: str) -> str:
        """Which colour is serving now, read from the box, not from memory."""
        running = self._sh(
            f"docker ps --format '{{{{.Names}}}}' --filter name=ov-{shlex.quote(project)}- "
            "2>/dev/null || true"
        )
        return "blue" if "-green-" in running.stdout else "green"

    def _upload_source(self, local_dir: Path, plan: SitePlan) -> tuple[bool, str]:
        src = f"{plan.remote_dir}/src"
        payload = tar_directory(local_dir)
        put = self.runner.put_bytes(f"{plan.remote_dir}/src.tar.gz", payload, mode="600")
        if not put.ok:
            return False, f"upload failed: {put.tail()}"
        res = self._root(
            f"rm -rf {shlex.quote(src)} && mkdir -p {shlex.quote(src)} && "
            f"tar -xzf {shlex.quote(plan.remote_dir + '/src.tar.gz')} -C {shlex.quote(src)} && "
            f"rm -f {shlex.quote(plan.remote_dir + '/src.tar.gz')}",
            timeout_s=_BUILD_TIMEOUT_S,
        )
        if not res.ok:
            return False, f"could not unpack the upload on the server: {res.tail()}"
        return True, ""

    def _ensure_dockerfile(self, plan: SitePlan) -> tuple[bool, str]:
        """Use the repo's Dockerfile when it has one; otherwise generate ours."""
        build_dir = plan.build_dir
        exists = self._sh(
            f"test -f {shlex.quote(build_dir + '/Dockerfile')} && echo yes || echo no"
        )
        if exists.out == "yes":
            return True, "repo Dockerfile"
        try:
            content = render_dockerfile(
                build_image=plan.build_image,
                install_command=plan.install_command,
                build_command=plan.build_command,
                start_command=plan.start_command,
                port=plan.port,
            )
        except VpsConfigError as exc:
            return False, str(exc)
        put = self.runner.put_bytes(f"{build_dir}/Dockerfile", content.encode("utf-8"), mode="644")
        if not put.ok:
            return False, f"could not write the generated Dockerfile: {put.tail()}"
        return True, "generated Dockerfile"

    def _health_check(self, port: int) -> tuple[bool, str]:
        """Poll one replica until it answers. This is the gate on the swap."""
        probe = (
            f"for i in $(seq 1 {_HEALTH_ATTEMPTS}); do "
            f"if curl -fsS -o /dev/null -m 5 http://127.0.0.1:{port}/; then echo up; exit 0; fi; "
            f"sleep {int(_HEALTH_SLEEP_S)}; done; echo down; exit 1"
        )
        res = self._sh(probe, timeout_s=_HEALTH_ATTEMPTS * _HEALTH_SLEEP_S + 30)
        if res.ok:
            return True, ""
        return False, f"replica on port {port} never answered HTTP"

    def _verify_public(self, plan: SitePlan) -> tuple[bool, str, str]:
        """Assert on what the customer receives, at the layer they receive it.

        Preferred: fetch the public URL from *this* machine, over the internet.
        If DNS does not point here yet, fall back to a request through the proxy
        on the box with the right Host header, and say so — a working proxy with
        DNS still pending is a different fact from a live site.
        """
        url = f"https://{plan.hostname}" if plan.hostname else plan.site_address
        try:
            resp = httpx.get(url, timeout=_PUBLIC_PROBE_TIMEOUT_S, follow_redirects=True)
            if resp.status_code < 500:
                return True, url, f"HTTP {resp.status_code} from {url}"
        except httpx.HTTPError as exc:
            outside_error = str(exc)
        else:
            outside_error = f"HTTP {resp.status_code}"

        if not plan.hostname:
            return False, "", f"proxy did not answer on {url}: {outside_error}"

        local = self._sh(
            "curl -fsS -o /dev/null -w '%{http_code}' -m 10 "
            f"-H {shlex.quote('Host: ' + plan.hostname)} http://127.0.0.1/ || true"
        )
        if local.out and local.out[:1] in ("2", "3", "4"):
            return (
                False,
                "",
                (
                    f"the app is serving on the box (proxy answered {local.out} for "
                    f"{plan.hostname}) but {url} is not reachable from here yet: "
                    f"{outside_error}. Point the DNS A record at {self.server_ip()} — "
                    "Caddy issues the certificate on the first request after that."
                ),
            )
        return False, "", f"nothing answered for {plan.hostname} on the box either: {outside_error}"

    def _write_site(
        self, plan: SitePlan, *, static_root: str, upstreams: Sequence[int]
    ) -> tuple[bool, str]:
        site = render_caddy_site(
            hostname=plan.hostname,
            site_address=plan.site_address,
            static_root=static_root,
            upstreams=upstreams,
        )
        path = f"{CADDY_SITE_DIR}/{plan.project}.caddy"
        # Write beside the live file, validate the whole config, then move it in.
        # A bad site block that reaches Caddy takes down every other site on the box.
        staged = f"{REMOTE_ROOT}/{plan.project}/site.caddy"
        put = self.runner.put_bytes(staged, site.encode("utf-8"), mode="644")
        if not put.ok:
            return False, f"could not stage the proxy config: {put.tail()}"
        res = self._root(
            f"cp {shlex.quote(path)} {shlex.quote(path + '.bak')} 2>/dev/null || true\n"
            f"cp {shlex.quote(staged)} {shlex.quote(path)}\n"
            f"if ! caddy validate --config {shlex.quote(CADDY_MAIN)} --adapter caddyfile "
            ">/dev/null 2>&1; then "
            f"  if [ -f {shlex.quote(path + '.bak')} ]; then mv {shlex.quote(path + '.bak')} "
            f"{shlex.quote(path)}; else rm -f {shlex.quote(path)}; fi; "
            "  echo 'invalid caddy config'; exit 1; "
            "fi\n"
            f"caddy reload --config {shlex.quote(CADDY_MAIN)} --adapter caddyfile\n"
            f"rm -f {shlex.quote(path + '.bak')}"
        )
        if not res.ok:
            return False, f"proxy refused the new config (traffic unchanged): {res.tail()}"
        return True, ""

    def deploy(
        self,
        artifact_dir: Path,
        *,
        project: str,
        env: Mapping[str, str] | None = None,
        hostname: str = "",
        stack: Any | None = None,
        source_dir: Path | None = None,
    ) -> DeployResult:
        """Build on the box, health-check, then swap the proxy onto the new colour.

        ``artifact_dir`` is the built output for a static site; ``source_dir``
        (default: ``artifact_dir``) is what gets uploaded for a container build.
        """
        pre = self.preflight()
        if not pre.ready:
            return DeployResult(ok=False, detail=pre.blocker)

        active_stack = stack if stack is not None else self._stack
        if active_stack is None:
            return DeployResult(
                ok=False,
                detail=(
                    "no detected stack — OpenVault needs to know how to build this "
                    "project before it can run it on your VPS"
                ),
            )
        try:
            plan = plan_from_stack(
                active_stack,
                project=project,
                hostname=hostname,
                server_ip=self.server_ip(),
                replicas=self._replicas,
            )
        except VpsConfigError as exc:
            return DeployResult(ok=False, detail=str(exc))

        local = Path(source_dir or artifact_dir)
        if not local.is_dir():
            return DeployResult(ok=False, detail=f"nothing to upload: {local} is not a directory")

        ok, err = self.ensure_host()
        if not ok:
            return DeployResult(ok=False, detail=err)

        ok, err = self._upload_source(local, plan)
        if not ok:
            return DeployResult(ok=False, detail=err)

        if plan.mode == "static":
            return self._deploy_static(plan)
        return self._deploy_container(plan, env or {})

    def _build_static_remotely(self, plan: SitePlan) -> tuple[bool, str]:
        """Build a static site in a throwaway container on the box.

        The point of "we host it" is that the user does not need the toolchain
        locally. Same image detection already picked, mounted over the upload,
        so the output lands where the plan expects it.
        """
        if not (plan.install_command or plan.build_command):
            return False, "this stack has no build command, so the output has to be uploaded"
        image = plan.build_image or "node:22"
        work = plan.build_dir
        script = " && ".join(c for c in (plan.install_command, plan.build_command) if c)
        res = self._root(
            f"docker run --rm -v {shlex.quote(work)}:/app -w /app {shlex.quote(image)} "
            f"sh -lc {shlex.quote(script)}",
            timeout_s=_BUILD_TIMEOUT_S,
        )
        if not res.ok:
            return False, f"build failed on the server: {res.tail()}"
        return True, ""

    def _deploy_static(self, plan: SitePlan) -> DeployResult:
        src = f"{plan.remote_dir}/src"
        rel = "/".join(p for p in (plan.root_directory, plan.output_directory) if p)
        built = f"{src}/{rel}" if rel else src
        release = f"{plan.remote_dir}/releases/{int(time.time())}"

        present = self._sh(f"test -d {shlex.quote(built)} && echo yes || echo no")
        if present.out != "yes":
            ok, err = self._build_static_remotely(plan)
            if not ok:
                return DeployResult(
                    ok=False,
                    detail=(
                        f"no build output at {rel or '.'} in the upload and we could not "
                        f"produce it: {err}"
                    ),
                )

        res = self._root(
            f"test -d {shlex.quote(built)} || {{ echo 'build produced no {rel}'; "
            "exit 1; }\n"
            f"mkdir -p {shlex.quote(plan.remote_dir + '/releases')}\n"
            f"cp -r {shlex.quote(built)} {shlex.quote(release)}\n"
            f"ln -sfn {shlex.quote(release)} {shlex.quote(plan.remote_dir + '/current')}\n"
            f"chmod -R a+rX {shlex.quote(release)}\n"
            # Keep the last three releases so a rollback is a symlink flip.
            f"ls -1dt {shlex.quote(plan.remote_dir + '/releases')}/* | tail -n +4 | "
            "xargs -r rm -rf"
        )
        if not res.ok:
            return DeployResult(ok=False, detail=f"could not publish the files: {res.tail()}")

        ok, err = self._write_site(plan, static_root=f"{plan.remote_dir}/current", upstreams=())
        if not ok:
            return DeployResult(ok=False, detail=err)

        live, url, detail = self._verify_public(plan)
        if not live:
            return DeployResult(ok=False, deployment_ref=release, detail=detail)
        log.info("vps_static_deployed", project=plan.project, url=url)
        return DeployResult(
            ok=True,
            url=url,
            deployment_ref=release,
            detail=f"Live at {url} — static files served by Caddy on your VPS ({detail})",
        )

    def _deploy_container(self, plan: SitePlan, env: Mapping[str, str]) -> DeployResult:
        ok, which = self._ensure_dockerfile(plan)
        if not ok:
            return DeployResult(ok=False, detail=which)

        env_flag = ""
        if env:
            try:
                body = render_env_file(env)
            except VpsConfigError as exc:
                return DeployResult(ok=False, detail=str(exc))
            env_path = f"{plan.remote_dir}/env"
            put = self.runner.put_bytes(env_path, body.encode("utf-8"), mode="600")
            if not put.ok:
                return DeployResult(
                    ok=False, detail=f"could not place the injected secrets: {put.tail()}"
                )
            env_flag = f" --env-file {shlex.quote(env_path)}"

        tag = str(int(time.time()))
        image = f"ov-{plan.project}:{tag}"
        build = self._root(
            f"docker build -t {shlex.quote(image)} {shlex.quote(plan.build_dir)}",
            timeout_s=_BUILD_TIMEOUT_S,
        )
        if not build.ok:
            return DeployResult(
                ok=False,
                detail=f"build failed on the server ({which}): {build.tail()}",
                log=build.stdout + build.stderr,
            )

        colour = self._current_colour(plan.project)
        base = port_base(plan.project, colour)
        ports = [base + i for i in range(plan.replicas)]
        old = other_colour(colour)

        start_lines = [
            f"docker rm -f ov-{plan.project}-{colour}-{i} >/dev/null 2>&1 || true\n"
            f"docker run -d --name ov-{plan.project}-{colour}-{i} "
            f"--restart unless-stopped -p 127.0.0.1:{port}:{plan.port} "
            f"-e PORT={plan.port}{env_flag} {shlex.quote(image)}"
            for i, port in enumerate(ports)
        ]
        started = self._root("\n".join(start_lines), timeout_s=_BUILD_TIMEOUT_S)
        if not started.ok:
            self._root(
                f"docker rm -f $(docker ps -aq --filter name=ov-{plan.project}-{colour}-) "
                ">/dev/null 2>&1 || true"
            )
            return DeployResult(
                ok=False,
                detail=f"containers would not start: {started.tail()}",
                log=started.stdout + started.stderr,
            )

        for port in ports:
            healthy, err = self._health_check(port)
            if not healthy:
                logs = self._root(
                    f"docker logs --tail 100 ov-{plan.project}-{colour}-{ports.index(port)} "
                    "2>&1 || true"
                )
                self._root(
                    f"docker rm -f $(docker ps -aq --filter name=ov-{plan.project}-{colour}-) "
                    ">/dev/null 2>&1 || true"
                )
                return DeployResult(
                    ok=False,
                    detail=(
                        f"{err} — the previous version is still serving, nothing was switched over"
                    ),
                    log=logs.stdout,
                )

        ok, err = self._write_site(plan, static_root="", upstreams=ports)
        if not ok:
            self._root(
                f"docker rm -f $(docker ps -aq --filter name=ov-{plan.project}-{colour}-) "
                ">/dev/null 2>&1 || true"
            )
            return DeployResult(ok=False, detail=err)

        live, url, detail = self._verify_public(plan)
        if not live:
            return DeployResult(ok=False, deployment_ref=image, detail=detail, log=build.tail(2000))

        # Only now is the old colour dead. Images are pruned to the last few so
        # the box does not fill up with tags nobody will roll back to.
        self._root(
            f"docker rm -f $(docker ps -aq --filter name=ov-{plan.project}-{old}-) "
            ">/dev/null 2>&1 || true\n"
            f"docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}} {{{{.CreatedAt}}}}' "
            f"| grep '^ov-{plan.project}:' | sort -k2 -r | tail -n +4 | awk '{{print $1}}' "
            "| xargs -r docker rmi -f >/dev/null 2>&1 || true"
        )
        log.info(
            "vps_deployed",
            project=plan.project,
            url=url,
            replicas=plan.replicas,
            colour=colour,
            env_names=sorted(env.keys()),
        )
        return DeployResult(
            ok=True,
            url=url,
            deployment_ref=image,
            detail=(
                f"Live at {url} — {plan.replicas} replica(s) behind Caddy on your VPS, "
                f"{which}, zero-downtime swap onto the {colour} slot ({detail})"
            ),
            log=build.tail(2000),
        )

    # -- domain ------------------------------------------------------------

    def attach_domain(self, *, project: str, hostname: str) -> DomainResult:
        """Check the domain actually points here; the user owns the DNS, not us."""
        host = normalize_hostname(hostname)
        if not host:
            return DomainResult(ok=False, detail=f"{hostname!r} is not a valid hostname")

        ip = self.server_ip()
        record = {
            "type": "A",
            "name": host,
            "value": ip,
            "note": "Caddy requests the TLS certificate on the first request after this resolves",
        }
        try:
            resolved = {info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET)}
        except OSError as exc:
            return DomainResult(
                ok=False,
                hostname=host,
                detail=f"{host} does not resolve yet ({exc.strerror or exc}) — add the A record",
                required_records=[record],
            )

        if ip and ip in resolved:
            return DomainResult(
                ok=True,
                hostname=host,
                detail=f"{host} resolves to {ip}, which is this server",
            )
        return DomainResult(
            ok=False,
            hostname=host,
            detail=(
                f"{host} resolves to {', '.join(sorted(resolved))}, not this server ({ip}) — "
                "update the A record, then retry"
            ),
            required_records=[record],
        )

    # -- read-only status --------------------------------------------------

    def status(self, project: str) -> dict[str, Any]:
        """What is actually running for this project, straight from the box."""
        name = normalize_project_name(project)
        res = self._sh(
            f"docker ps --format '{{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Image}}}}' "
            f"--filter name=ov-{shlex.quote(name)}- 2>/dev/null || true"
        )
        replicas = [
            dict(zip(("name", "status", "image"), line.split("\t"), strict=False))
            for line in res.stdout.splitlines()
            if line.strip()
        ]
        return {"project": name, "server": self.target.label(), "replicas": replicas}


def from_vault(
    get_secret_for_provider,
    *,
    host: str | None = None,
    user: str | None = None,
    port: int | None = None,
    stack: Any | None = None,
    replicas: int | None = None,
) -> VpsSshAdapter:
    """Build from env, with the vault supplying the key *path*.

    Deliberately a path and not key material: writing a private key out of the
    vault to disk on every deploy would put plaintext custody back where
    OpenVault spent an epic removing it. The vault row for provider ``vps_ssh``
    holds where the key lives; OpenSSH reads it.
    """
    vaulted = (get_secret_for_provider("vps_ssh") or "").strip()
    key_path = os.environ.get("OPENVAULT_VPS_KEY", "")
    if not key_path and vaulted and not vaulted.startswith("-----BEGIN"):
        key_path = vaulted
    return VpsSshAdapter(
        host=host or os.environ.get("OPENVAULT_VPS_HOST"),
        user=user or os.environ.get("OPENVAULT_VPS_USER", "root"),
        port=int(port or os.environ.get("OPENVAULT_VPS_PORT", "22") or 22),
        key_path=key_path,
        stack=stack,
        replicas=int(replicas or os.environ.get("OPENVAULT_VPS_REPLICAS", "2") or 2),
    )


__all__ = [
    "CADDY_SITE_DIR",
    "REMOTE_ROOT",
    "RunResult",
    "SitePlan",
    "SshRunner",
    "SshTarget",
    "VpsConfigError",
    "VpsSshAdapter",
    "from_vault",
    "normalize_hostname",
    "normalize_project_name",
    "other_colour",
    "plan_from_stack",
    "port_base",
    "render_caddy_site",
    "render_dockerfile",
    "render_env_file",
    "tar_directory",
]

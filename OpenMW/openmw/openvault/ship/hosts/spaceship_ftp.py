"""Spaceship FTP - the host this estate already pays for.

Not a second host. Not Cloudflare invented for netie.ai. The user's machine
builds; we push static files over the existing FTP account; we return a public
URL only when (1) publish was explicitly allowed, (2) upload ran, and (3) a
probe of a *configured* URL succeeded. We never construct ``https://netie.ai``.

Live overwrite is gated: ``OPENVAULT_SPACESHIP_ALLOW_PUBLISH=1`` must be set.
Without it, deploy refuses with an empty URL (HT1 stays human).
"""

from __future__ import annotations

import ftplib
import os
from collections.abc import Callable, Mapping
from io import BytesIO
from pathlib import Path

import httpx
import structlog

from openmw.openvault.ship.hosts.base import DeployResult, DomainResult, Preflight
from openmw.openvault.ship.stacks import is_upload_ignored_path

log = structlog.get_logger()

_FTP_TIMEOUT_S = 30.0
_PROBE_TIMEOUT_S = 10.0

ProbeFn = Callable[[str], bool]
UploadFn = Callable[[str, str, str, str, Path], int]
EnvWriter = Callable[[str, str, str, str, Mapping[str, str]], None]


def public_ftp_env_refused(
    env: Mapping[str, str] | None,
    env_dir: str,
    remote_dir: str = "",
) -> str:
    """Why vault env was not written. Empty when writing is allowed.

    Shared-host FTP often lands in public_html. Writing ``.env`` there is a
    leak, not Vercel-style inject. A distinct ``SPACESHIP_FTP_ENV_DIR`` is the
    only path we will STOR secrets onto, and it must not equal the upload dir.
    """
    if not env:
        return ""
    dest = (env_dir or "").strip().rstrip("/")
    public = (remote_dir or "").strip().rstrip("/")
    n = len(env)
    if not dest:
        return (
            f"{n} vault env name{'s' if n != 1 else ''} selected but not written "
            "(public FTP dir would leak secrets). Set SPACESHIP_FTP_ENV_DIR to a "
            "non-public path."
        )
    if dest == public:
        return (
            f"{n} vault env name{'s' if n != 1 else ''} selected but not written: "
            "SPACESHIP_FTP_ENV_DIR must differ from SPACESHIP_FTP_DIR."
        )
    return ""


def normalize_public_url(raw: str) -> str:
    """Accept only an explicit http(s) URL. Never invent a hostname."""
    url = (raw or "").strip().rstrip("/")
    if url.startswith("https://") or url.startswith("http://"):
        return url
    return ""


def host_configured(
    *,
    host: str | None = None,
    user: str | None = None,
) -> bool:
    """True when the existing Spaceship FTP account is named in env/args."""
    ftp_host = (host if host is not None else os.environ.get("SPACESHIP_FTP_HOST", "")).strip()
    ftp_user = (user if user is not None else os.environ.get("SPACESHIP_FTP_USER", "")).strip()
    return bool(ftp_host and ftp_user)


def publish_allowed(raw: str | None = None) -> bool:
    value = (raw if raw is not None else os.environ.get("OPENVAULT_SPACESHIP_ALLOW_PUBLISH", "")).strip()
    return value in {"1", "true", "TRUE", "yes", "YES"}


def iter_upload_relpaths(artifact_dir: Path) -> list[str]:
    """Relative POSIX paths that would be uploaded (.env files excluded)."""
    if not artifact_dir.is_dir():
        return []
    out: list[str] = []
    for path in sorted(artifact_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(artifact_dir).as_posix()
        name = path.name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            continue
        if is_upload_ignored_path(rel):
            continue
        out.append(rel)
    return out


def probe_url(url: str, *, client: httpx.Client | None = None) -> bool:
    """True when the configured URL answers 2xx. Empty/non-http never observed."""
    target = normalize_public_url(url)
    if not target:
        return False
    own = client is None
    http = client or httpx.Client(timeout=_PROBE_TIMEOUT_S, follow_redirects=True)
    try:
        resp = http.head(target)
        if resp.status_code == 405:
            resp = http.get(target)
        return 200 <= resp.status_code < 300
    except httpx.HTTPError:
        return False
    finally:
        if own:
            http.close()


def _ftp_upload(
    host: str,
    user: str,
    password: str,
    remote_dir: str,
    artifact_dir: Path,
) -> int:
    """Upload non-secret files. Returns count. Caller must have allowed publish."""
    rels = iter_upload_relpaths(artifact_dir)
    ftp = ftplib.FTP()
    ftp.connect(host, timeout=_FTP_TIMEOUT_S)
    try:
        ftp.login(user, password)
        if remote_dir:
            _ensure_remote_dir(ftp, remote_dir)
            ftp.cwd(remote_dir)
        root = ftp.pwd()
        uploaded = 0
        for rel in rels:
            local = artifact_dir / rel
            parent = str(Path(rel).parent).replace("\\", "/")
            name = Path(rel).name
            ftp.cwd(root)
            if parent not in {".", ""}:
                _ensure_remote_dir(ftp, parent)
                ftp.cwd(parent)
            with local.open("rb") as handle:
                ftp.storbinary(f"STOR {name}", handle)
            uploaded += 1
        return uploaded
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def _ftp_write_env(
    host: str,
    user: str,
    password: str,
    env_dir: str,
    env: Mapping[str, str],
) -> None:
    """STOR a dotenv at env_dir/.env. Caller already checked dest != public dir."""
    lines: list[str] = []
    for name, value in env.items():
        if "\n" in value or "\r" in value:
            raise OSError(f"{name}: newline cannot travel in a dotenv on FTP")
        lines.append(f"{name}={value}")
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    ftp = ftplib.FTP()
    ftp.connect(host, timeout=_FTP_TIMEOUT_S)
    try:
        ftp.login(user, password)
        if env_dir:
            _ensure_remote_dir(ftp, env_dir)
            ftp.cwd(env_dir)
        ftp.storbinary("STOR .env", BytesIO(payload))
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def _ensure_remote_dir(ftp: ftplib.FTP, rel: str) -> None:
    if not rel or rel in {".", "./"}:
        return
    parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return
    here = ftp.pwd()
    try:
        for part in parts:
            try:
                ftp.cwd(part)
            except ftplib.error_perm:
                ftp.mkd(part)
                ftp.cwd(part)
    finally:
        ftp.cwd(here)


class SpaceshipFtpAdapter:
    """Publish a built directory over the existing Spaceship FTP account."""

    id = "spaceship_ftp"
    name = "Spaceship FTP (existing host)"
    credential_provider = "spaceship"
    needs_local_build = True

    def __init__(
        self,
        *,
        host: str | None,
        user: str | None,
        password: str | None,
        remote_dir: str | None = None,
        public_url: str | None = None,
        env_dir: str | None = None,
        allow_publish: bool | None = None,
        probe: ProbeFn | None = None,
        uploader: UploadFn | None = None,
        env_writer: EnvWriter | None = None,
    ) -> None:
        self._host = (host or "").strip()
        self._user = (user or "").strip()
        self._password = (password or "").strip()
        self._remote_dir = (remote_dir or "").strip()
        self._public_url = normalize_public_url(public_url or "")
        self._env_dir = (env_dir if env_dir is not None else os.environ.get("SPACESHIP_FTP_ENV_DIR", "")).strip()
        if allow_publish is None:
            self._allow_publish = publish_allowed()
        else:
            self._allow_publish = bool(allow_publish)
        self._probe = probe
        self._uploader = uploader
        self._env_writer = env_writer

    def preflight(self) -> Preflight:
        if not self._host:
            return Preflight(
                ready=False,
                blocker=(
                    "Set SPACESHIP_FTP_HOST to the existing Spaceship FTP hostname. "
                    "Do not buy New hosting."
                ),
            )
        if not self._user:
            return Preflight(
                ready=False,
                blocker="Set SPACESHIP_FTP_USER (existing FTP account, e.g. ship@your.domain).",
            )
        if not self._password:
            return Preflight(
                ready=False,
                blocker=(
                    "Vault a Spaceship FTP password (provider `spaceship`) or set "
                    "SPACESHIP_FTP_PASS. Never paste it in chat."
                ),
            )
        facts = {
            "host": self._host,
            "user": self._user,
            "allow_publish": "yes" if self._allow_publish else "no",
        }
        if self._public_url:
            facts["configured_public_url"] = self._public_url
        if self._env_dir:
            facts["env_dir"] = self._env_dir
        else:
            facts["env"] = "vault-inject-refused-public-ftp"
        if not self._allow_publish:
            facts["note"] = (
                "Preflight can be ready; live overwrite needs "
                "OPENVAULT_SPACESHIP_ALLOW_PUBLISH=1"
            )
        return Preflight(ready=True, facts=facts)

    def deploy(
        self,
        artifact_dir: Path,
        *,
        project: str,
        env: Mapping[str, str] | None = None,
    ) -> DeployResult:
        del project  # FTP dest is the existing account, not a new project name.
        pre = self.preflight()
        if not pre.ready:
            return DeployResult(ok=False, detail=pre.blocker)

        if not artifact_dir.is_dir():
            return DeployResult(
                ok=False,
                detail="nothing was built - there is no artifact directory to upload",
            )

        files = iter_upload_relpaths(artifact_dir)
        if not files:
            return DeployResult(
                ok=False,
                detail="artifact has no uploadable files after excluding .env secrets",
            )

        if not self._allow_publish:
            log.info(
                "spaceship_ftp_refused_overwrite",
                host=self._host,
                files=len(files),
            )
            return DeployResult(
                ok=False,
                url="",
                detail=(
                    "Refusing to overwrite the live Spaceship site without "
                    "OPENVAULT_SPACESHIP_ALLOW_PUBLISH=1. Simulate never invents a URL. "
                    "HT1 stays human."
                ),
            )

        env_note = public_ftp_env_refused(env, self._env_dir, self._remote_dir)
        try:
            uploader = self._uploader or _ftp_upload
            count = uploader(
                self._host,
                self._user,
                self._password,
                self._remote_dir,
                artifact_dir,
            )
            if env and not env_note:
                writer = self._env_writer or _ftp_write_env
                writer(
                    self._host,
                    self._user,
                    self._password,
                    self._env_dir,
                    dict(env),
                )
        except (OSError, ftplib.all_errors) as exc:
            log.warning("spaceship_ftp_upload_failed", error=str(exc))
            return DeployResult(
                ok=False,
                url="",
                detail=f"FTP upload failed: {exc}",
            )

        observed = ""
        if self._public_url:
            probe = self._probe if self._probe is not None else probe_url
            if probe(self._public_url):
                observed = self._public_url
        if not observed:
            return DeployResult(
                ok=False,
                url="",
                detail=(
                    f"Uploaded {count} files to existing Spaceship FTP but no observed "
                    "public URL (set SPACESHIP_PUBLIC_URL and only return it after probe). "
                    "Refusing to invent one."
                ),
            )
        detail = f"Uploaded {count} files to existing Spaceship host; observed {observed}"
        if env and not env_note:
            detail += f"; wrote {len(env)} vault env names to non-public FTP dir"
        elif env_note:
            detail += f"; {env_note}"
        return DeployResult(
            ok=True,
            url=observed,
            deployment_ref=self._user,
            detail=detail,
        )

    def attach_domain(self, *, project: str, hostname: str) -> DomainResult:
        del project
        host = (hostname or "").strip()
        if not host:
            return DomainResult(ok=False, detail="no hostname given")
        configured = normalize_public_url(self._public_url)
        wanted = normalize_public_url(
            hostname if hostname.startswith("http") else f"https://{host}"
        )
        if configured and wanted and configured.rstrip("/") == wanted.rstrip("/"):
            return DomainResult(
                ok=True,
                hostname=host,
                detail="hostname already matches the configured Spaceship public URL",
            )
        return DomainResult(
            ok=False,
            hostname=host,
            detail=(
                "Spaceship DNS stays in Hosting Manager. We do not create records "
                "or invent a URL. Point the existing domain at this account yourself."
            ),
        )


def from_vault(get_secret_for_provider: Callable[[str], str | None]) -> SpaceshipFtpAdapter:
    """Build from vault provider `spaceship` plus env (host/user/url)."""
    password = get_secret_for_provider("spaceship") or os.environ.get("SPACESHIP_FTP_PASS")
    return SpaceshipFtpAdapter(
        host=os.environ.get("SPACESHIP_FTP_HOST"),
        user=os.environ.get("SPACESHIP_FTP_USER"),
        password=password,
        remote_dir=os.environ.get("SPACESHIP_FTP_DIR"),
        public_url=os.environ.get("SPACESHIP_PUBLIC_URL"),
        env_dir=os.environ.get("SPACESHIP_FTP_ENV_DIR"),
    )


__all__ = [
    "SpaceshipFtpAdapter",
    "from_vault",
    "host_configured",
    "iter_upload_relpaths",
    "normalize_public_url",
    "probe_url",
    "public_ftp_env_refused",
    "publish_allowed",
]

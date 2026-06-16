"""nvme-cli subprocess fallback per implementation_plan.md §7.2."""

from __future__ import annotations

import re
import shutil
import subprocess

import structlog

from nvme_sentinel.hal.exceptions import AdminCommandError, CapabilityError

_LOG = structlog.get_logger()
_VERSION_RE = re.compile(r"(\d+)\.")


class NvmeCliSupport:
    """Capability-detected nvme-cli availability for LinuxNvmeAdapter fallback."""

    def __init__(self) -> None:
        self._nvme_cli_path: str | None = shutil.which("nvme")
        self._nvme_cli_major: int | None = None
        if self._nvme_cli_path is not None:
            self._nvme_cli_major = self._detect_major_version()
            _LOG.debug(
                "nvme_cli_detected",
                path=self._nvme_cli_path,
                major=self._nvme_cli_major,
            )

    def available(self) -> bool:
        """Return True when nvme-cli is present and major version >= 1."""
        return self._nvme_cli_path is not None and (
            self._nvme_cli_major is not None and self._nvme_cli_major >= 1
        )

    def capability_token(self) -> str:
        return "nvme-cli"

    def _detect_major_version(self) -> int:
        if self._nvme_cli_path is None:
            raise CapabilityError("nvme-cli not found on PATH")
        proc = subprocess.run(
            [self._nvme_cli_path, "version"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise CapabilityError(f"nvme version failed: {proc.stderr.decode(errors='replace')}")
        text = proc.stdout.decode(errors="replace")
        match = _VERSION_RE.search(text)
        if not match:
            raise CapabilityError(f"cannot parse nvme version from: {text!r}")
        major = int(match.group(1))
        if major < 1:
            raise CapabilityError(f"nvme-cli major version {major} < 1")
        return major

    def id_ctrl_raw(self, path: str) -> bytes:
        """Return raw Identify Controller bytes via nvme-cli."""
        return _run_raw(
            self._nvme_cli_path,
            ["id-ctrl", path, "--raw-binary"],
            opcode=0x06,
            max_len=4096,
        )

    def id_ns_raw(self, path: str, nsid: int) -> bytes:
        """Return raw Identify Namespace bytes via nvme-cli."""
        return _run_raw(
            self._nvme_cli_path,
            ["id-ns", path, f"--namespace-id={nsid}", "--raw-binary"],
            opcode=0x06,
            max_len=4096,
        )

    def get_smart_raw(self, path: str) -> bytes:
        """Return raw SMART / Health Information log bytes via nvme-cli."""
        return _run_raw(
            self._nvme_cli_path,
            ["smart-log", path, "--raw-binary"],
            opcode=0x02,
            max_len=512,
        )


def _run_raw(
    nvme_bin: str | None,
    args: list[str],
    *,
    opcode: int,
    max_len: int,
) -> bytes:
    if nvme_bin is None:
        raise CapabilityError("nvme-cli not found on PATH")
    proc = subprocess.run(
        [nvme_bin, *args],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise AdminCommandError(
            status_code=proc.returncode,
            opcode=opcode,
            message=proc.stderr.decode(errors="replace"),
        )
    return proc.stdout[:max_len]


def id_ctrl_raw(path: str) -> bytes:
    """Module-level helper using PATH nvme binary."""
    support = NvmeCliSupport()
    if not support.available():
        raise CapabilityError("nvme-cli not available")
    return support.id_ctrl_raw(path)


def id_ns_raw(path: str, nsid: int) -> bytes:
    """Module-level helper using PATH nvme binary."""
    support = NvmeCliSupport()
    if not support.available():
        raise CapabilityError("nvme-cli not available")
    return support.id_ns_raw(path, nsid)


def get_smart_raw(path: str) -> bytes:
    """Module-level helper using PATH nvme binary."""
    support = NvmeCliSupport()
    if not support.available():
        raise CapabilityError("nvme-cli not available")
    return support.get_smart_raw(path)

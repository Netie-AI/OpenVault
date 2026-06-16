"""HAL interface contracts for NVMe-capable storage adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import TracebackType


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Immutable device identity and topology information."""

    path: str
    model: str
    serial: str
    firmware_rev: str
    namespace_count: int
    is_nvme: bool


@dataclass(frozen=True, slots=True)
class AdminCommand:
    """NVMe admin command fields required by passthrough adapters."""

    opcode: int
    nsid: int = 0
    cdw10: int = 0
    cdw11: int = 0
    cdw12: int = 0
    cdw13: int = 0
    cdw14: int = 0
    cdw15: int = 0
    data_len: int = 0
    timeout_ms: int = 60_000


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of a submitted NVMe command and any returned data payload."""

    status: int
    result_dw0: int
    data: bytes


class StorageInterface(ABC):
    """Hardware abstraction for storage devices supporting NVMe admin passthrough."""

    @abstractmethod
    def open(self) -> None:
        """Open underlying OS device resources."""

    @abstractmethod
    def close(self) -> None:
        """Release underlying OS device resources."""

    @abstractmethod
    def admin_passthru(self, cmd: AdminCommand) -> CommandResult:
        """Submit one NVMe admin command and return its completion payload."""

    @abstractmethod
    def get_device_info(self) -> DeviceInfo:
        """Fetch static identity information for the current device."""

    @abstractmethod
    def list_namespaces(self) -> list[int]:
        """List active namespace identifiers for the current controller."""

    @abstractmethod
    def is_nvme(self) -> bool:
        """Return whether the opened device supports NVMe semantics."""

    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """Return adapter capabilities such as ioctl or device-io-control."""

    def __enter__(self) -> StorageInterface:
        """Enter context by opening the adapter."""
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit context by closing the adapter."""
        self.close()

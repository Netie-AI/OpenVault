"""Unit tests for base HAL abstractions."""

from __future__ import annotations

from nvme_sentinel.hal.base import BaseAdapter
from nvme_sentinel.hal.exceptions import AdminCommandError
from nvme_sentinel.hal.interface import AdminCommand, CommandResult, DeviceInfo


class DummyAdapter(BaseAdapter):
    """Minimal concrete adapter for BaseAdapter behavior tests."""

    def __init__(self) -> None:
        super().__init__()
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def admin_passthru(self, cmd: AdminCommand) -> CommandResult:
        return CommandResult(status=0, result_dw0=0, data=b"\x00" * cmd.data_len)

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            path="/dev/mock",
            model="model",
            serial="serial",
            firmware_rev="fw",
            namespace_count=1,
            is_nvme=True,
        )

    def list_namespaces(self) -> list[int]:
        return [1]

    def is_nvme(self) -> bool:
        return True

    def capabilities(self) -> frozenset[str]:
        return frozenset({"mock"})


def test_context_manager_opens_and_closes() -> None:
    """StorageInterface context methods open and close resources."""
    adapter = DummyAdapter()
    assert adapter.opened is False
    with adapter:
        assert adapter.opened is True
    assert adapter.opened is False


def test_timed_context_runs() -> None:
    """Timing context manager emits telemetry without raising."""
    adapter = DummyAdapter()
    with adapter._timed(AdminCommand(opcode=0x06)):
        pass


def test_retry_retries_transient_errors() -> None:
    """Retry helper retries generic failures up to success."""
    adapter = DummyAdapter()
    attempts = {"count": 0}

    def flaky() -> int:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("transient")
        return 7

    wrapped = adapter._retry(flaky, retries=2, backoff=0.0)
    assert wrapped() == 7
    assert attempts["count"] == 2


def test_retry_does_not_retry_admin_command_error() -> None:
    """Admin command protocol errors are re-raised immediately."""
    adapter = DummyAdapter()

    def protocol_failure() -> int:
        raise AdminCommandError(status_code=1, opcode=0x06, message="bad status")

    wrapped = adapter._retry(protocol_failure, retries=2, backoff=0.0)
    try:
        wrapped()
    except AdminCommandError as exc:
        assert "opcode=0x06" in str(exc)
    else:
        msg = "expected AdminCommandError"
        raise AssertionError(msg)

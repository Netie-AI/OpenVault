"""Abstract HAL base class with shared telemetry and retry helpers."""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter, sleep
from typing import ParamSpec, TypedDict, TypeVar

import structlog

from .exceptions import AdminCommandError
from .interface import AdminCommand, StorageInterface

P = ParamSpec("P")
R = TypeVar("R")


class TelemetryRecord(TypedDict, total=False):
    """Structured telemetry payload for adapter command logging."""

    opcode: int
    duration_ms: int
    status: int
    nsid: int
    data_len: int
    adapter: str


class BaseAdapter(StorageInterface, ABC):
    """Abstract adapter with shared timing, logging, and transient retry helpers."""

    def __init__(self) -> None:
        """Initialize adapter-scoped structured logger context."""
        self.log = structlog.get_logger().bind(adapter=self.__class__.__name__)

    @contextmanager
    def _timed(self, cmd: AdminCommand) -> Iterator[TelemetryRecord]:
        """Yields a mutable record dict; caller fills 'status' after completion."""
        record: TelemetryRecord = {
            "adapter": self.__class__.__name__,
            "opcode": cmd.opcode,
            "nsid": cmd.nsid,
            "data_len": cmd.data_len,
        }
        start = perf_counter()
        try:
            yield record
        finally:
            record["duration_ms"] = int((perf_counter() - start) * 1000.0)
            self.log.info("admin_command_timing", **record)

    def _retry(self, fn: Callable[P, R], retries: int = 2, backoff: float = 0.1) -> Callable[P, R]:
        """Wrap a callable with retry semantics for transient non-protocol failures."""

        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            attempt = 0
            while True:
                try:
                    return fn(*args, **kwargs)
                except AdminCommandError:
                    raise
                except Exception:
                    if attempt >= retries:
                        raise
                    attempt += 1
                    sleep(backoff * attempt)

        return wrapped

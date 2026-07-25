"""Per-model concurrency semaphore with bounded FIFO queue."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

SEMAPHORE_QUEUE_FULL = "SEMAPHORE_QUEUE_FULL"
SEMAPHORE_TIMEOUT = "SEMAPHORE_TIMEOUT"


@dataclass
class _QueueItem:
    event: threading.Event = field(default_factory=threading.Event)
    granted: bool = False
    release_fn: Callable[[], None] | None = None
    error: Exception | None = None


@dataclass
class _ModelGate:
    running: int = 0
    max_concurrency: int = 3
    queue: list[_QueueItem] = field(default_factory=list)
    rate_limited_until: float | None = None


_gates: dict[str, _ModelGate] = {}
_gate_lock = threading.Lock()


class SemaphoreError(Exception):
    """Base for semaphore acquire failures."""

    code: str

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class SemaphoreQueueFullError(SemaphoreError):
    def __init__(self, model_str: str, max_queue_size: int) -> None:
        super().__init__(
            f"Semaphore queue full ({max_queue_size}) for {model_str}",
            SEMAPHORE_QUEUE_FULL,
        )


class SemaphoreTimeoutError(SemaphoreError):
    def __init__(self, model_str: str, timeout_ms: int) -> None:
        super().__init__(
            f"Semaphore timeout after {timeout_ms}ms for {model_str}",
            SEMAPHORE_TIMEOUT,
        )


def _get_gate(model_str: str, max_concurrency: int) -> _ModelGate:
    if model_str not in _gates:
        _gates[model_str] = _ModelGate(max_concurrency=max_concurrency)
    gate = _gates[model_str]
    gate.max_concurrency = max_concurrency
    return gate


def _is_rate_limited(gate: _ModelGate, now: float) -> bool:
    if gate.rate_limited_until is None:
        return False
    if now >= gate.rate_limited_until:
        gate.rate_limited_until = None
        return False
    return True


def _create_release(model_str: str) -> Callable[[], None]:
    released = False

    def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        with _gate_lock:
            gate = _gates.get(model_str)
            if gate is None:
                return
            if gate.running > 0:
                gate.running -= 1
            _drain_queue(model_str, gate)

    return release


def _drain_queue(model_str: str, gate: _ModelGate) -> None:
    now = time.time()
    while gate.queue and gate.running < gate.max_concurrency and not _is_rate_limited(gate, now):
        item = gate.queue.pop(0)
        gate.running += 1
        item.granted = True
        item.release_fn = _create_release(model_str)
        item.event.set()

    if gate.running == 0 and not gate.queue:
        _gates.pop(model_str, None)


def acquire(
    model_str: str,
    *,
    max_concurrency: int = 3,
    timeout_ms: int = 30_000,
    max_queue_size: int | None = None,
) -> Callable[[], None]:
    """Acquire a concurrency slot; returns a release callable."""
    now = time.time()
    with _gate_lock:
        gate = _get_gate(model_str, max_concurrency)
        if gate.running < gate.max_concurrency and not _is_rate_limited(gate, now):
            gate.running += 1
            return _create_release(model_str)

        if max_queue_size is not None and max_queue_size >= 0 and len(gate.queue) >= max_queue_size:
            if gate.running == 0 and not gate.queue:
                _gates.pop(model_str, None)
            raise SemaphoreQueueFullError(model_str, max_queue_size)

        item = _QueueItem()
        gate.queue.append(item)

    if not item.event.wait(timeout_ms / 1000.0):
        with _gate_lock:
            gate = _gates.get(model_str)
            if gate is not None:
                try:
                    gate.queue.remove(item)
                except ValueError:
                    pass
                if gate.running == 0 and not gate.queue:
                    _gates.pop(model_str, None)
        raise SemaphoreTimeoutError(model_str, timeout_ms)

    if item.error is not None:
        raise item.error
    if item.release_fn is None:
        raise SemaphoreError("Semaphore acquire failed", SEMAPHORE_TIMEOUT)
    return item.release_fn


def mark_rate_limited(model_str: str, cooldown_ms: int) -> None:
    with _gate_lock:
        gate = _get_gate(model_str, 3)
        gate.rate_limited_until = time.time() + cooldown_ms / 1000.0


def get_stats() -> dict[str, dict[str, int | str | None]]:
    with _gate_lock:
        stats: dict[str, dict[str, int | str | None]] = {}
        for model, gate in _gates.items():
            stats[model] = {
                "running": gate.running,
                "queued": len(gate.queue),
                "max": gate.max_concurrency,
                "rateLimitedUntil": (
                    None
                    if gate.rate_limited_until is None
                    else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(gate.rate_limited_until))
                ),
            }
        return stats


def reset_all() -> None:
    with _gate_lock:
        for gate in _gates.values():
            for item in gate.queue:
                item.error = SemaphoreError("Semaphore reset", "RESET")
                item.event.set()
        _gates.clear()

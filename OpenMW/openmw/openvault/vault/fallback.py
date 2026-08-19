"""Fallback chain + circuit breaker for OpenVault proxy hops."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openmw.openvault.paths import fallback_path
from openmw.openvault.vault.store import KeyRecord, KeyVault

CircuitState = Literal["closed", "open", "half_open"]
ROLE_ORDER = ("primary", "backup", "cheap", "free")


@dataclass
class HopCircuit:
    key_id: str
    state: CircuitState = "closed"
    failures: int = 0
    opened_at: float | None = None
    last_error: str | None = None
    park_until: float | None = None
    park_reason: str | None = None


@dataclass
class FallbackConfig:
    """Ordered roles and breaker thresholds."""

    role_order: list[str] = field(default_factory=lambda: list(ROLE_ORDER))
    failure_threshold: int = 3
    open_seconds: float = 60.0


@dataclass
class FallbackStatus:
    hops: list[dict[str, object]]
    config: dict[str, object]


def rendezvous_score(affinity_key: str, key_id: str) -> int:
    """Highest-random-weight score for one (prompt prefix, key) pair.

    Rendezvous hashing rather than modulo: adding or removing a key remaps only
    that key's share of traffic instead of reshuffling everything, so growing
    the pool does not cold-start every conversation at once.
    """
    digest = hashlib.sha256(f"{affinity_key}\x00{key_id}".encode()).hexdigest()
    return int(digest[:16], 16)


def _rank_band(records: list[KeyRecord], affinity_key: str) -> list[KeyRecord]:
    """Sort one priority band, using affinity only to break exact-priority ties."""
    if not affinity_key:
        return sorted(records, key=lambda r: r.priority)
    return sorted(records, key=lambda r: (r.priority, -rendezvous_score(affinity_key, r.id), r.id))


class FallbackManager:
    """Select next healthy hop; open circuit on repeated failures."""

    def __init__(
        self,
        vault: KeyVault,
        *,
        config_path: Path | None = None,
        config: FallbackConfig | None = None,
    ) -> None:
        self._vault = vault
        self._config_path = config_path if config_path is not None else fallback_path()
        self._config = config if config is not None else self._load_config()
        self._circuits: dict[str, HopCircuit] = {}

    def _load_config(self) -> FallbackConfig:
        if self._config_path.is_file():
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
            return FallbackConfig(
                role_order=list(raw.get("role_order", ROLE_ORDER)),
                failure_threshold=int(raw.get("failure_threshold", 3)),
                open_seconds=float(raw.get("open_seconds", 60.0)),
            )
        return FallbackConfig()

    def save_config(self, config: FallbackConfig) -> None:
        self._config = config
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(
                {
                    "role_order": config.role_order,
                    "failure_threshold": config.failure_threshold,
                    "open_seconds": config.open_seconds,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def get_config(self) -> FallbackConfig:
        return self._config

    def _circuit(self, key_id: str) -> HopCircuit:
        if key_id not in self._circuits:
            self._circuits[key_id] = HopCircuit(key_id=key_id)
        return self._circuits[key_id]

    def _is_available(self, record: KeyRecord, now: float) -> bool:
        # Custody first, before health: a tenant's key is not ours to spend no
        # matter how healthy it looks. This is the chokepoint every selection
        # path runs through, so a future caller that forgets to source from
        # pooled_ordered() still cannot reach a tenant key (DR-0009, #36).
        if record.custody != "pooled":
            return False
        if not record.enabled:
            return False
        if record.precheck_status in ("auth_fail",):
            return False
        circ = self._circuit(record.id)
        if circ.park_until is not None and now < circ.park_until:
            return False
        if circ.state == "open":
            if circ.opened_at is not None and now - circ.opened_at >= self._config.open_seconds:
                circ.state = "half_open"
                return True
            return False
        return True

    def ordered_candidates(self, *, affinity_key: str = "") -> list[KeyRecord]:
        """Healthy hops, best first.

        ``affinity_key`` makes the order deterministic for a repeated prompt
        prefix. Without it, a pool of several keys scatters the same
        conversation across accounts and every upstream prompt cache stays cold
        — the caller pays full input price on every turn. The re-order happens
        strictly *within* a priority band, so health, park windows, role order
        and the operator's own priorities all still win; affinity only breaks
        ties that were previously broken by insertion order.
        """
        now = time.time()
        by_role: dict[str, list[KeyRecord]] = {r: [] for r in self._config.role_order}
        extras: list[KeyRecord] = []
        for record in self._vault.pooled_ordered():
            if record.role in by_role:
                by_role[record.role].append(record)
            else:
                extras.append(record)
        ordered: list[KeyRecord] = []
        for role in self._config.role_order:
            available = [r for r in by_role.get(role, []) if self._is_available(r, now)]
            ordered.extend(_rank_band(available, affinity_key))
        ordered.extend(_rank_band([r for r in extras if self._is_available(r, now)], affinity_key))
        return ordered

    def record_success(self, key_id: str) -> None:
        circ = self._circuit(key_id)
        circ.failures = 0
        circ.state = "closed"
        circ.opened_at = None
        circ.last_error = None
        circ.park_until = None
        circ.park_reason = None

    def record_failure(self, key_id: str, error: str) -> None:
        circ = self._circuit(key_id)
        circ.failures += 1
        circ.last_error = error
        if circ.state == "half_open" or circ.failures >= self._config.failure_threshold:
            circ.state = "open"
            circ.opened_at = time.time()

    def record_park(self, key_id: str, cooldown_ms: int, reason: str) -> None:
        """Temporarily hide a key without counting a circuit failure.

        Rate limits and stale OAuth must not open the hop circuit — that is the
        live bug fixed by DESIGN_TIERED_QUEUE_LB §1.1 / §4.2.
        """
        circ = self._circuit(key_id)
        wait_s = max(0.0, float(cooldown_ms) / 1000.0)
        circ.park_until = time.time() + wait_s
        circ.park_reason = reason
        circ.last_error = reason

    def status(self) -> FallbackStatus:
        hops: list[dict[str, object]] = []
        for record in self._vault.pooled_ordered():
            circ = self._circuit(record.id)
            hops.append(
                {
                    "key_id": record.id,
                    "label": record.label,
                    "provider": record.provider,
                    "role": record.role,
                    "priority": record.priority,
                    "precheck_status": record.precheck_status,
                    "circuit": circ.state,
                    "failures": circ.failures,
                    "last_error": circ.last_error or record.last_error,
                    "last_latency_ms": record.last_latency_ms,
                    "park_until": circ.park_until,
                    "park_reason": circ.park_reason,
                }
            )
        return FallbackStatus(
            hops=hops,
            config={
                "role_order": self._config.role_order,
                "failure_threshold": self._config.failure_threshold,
                "open_seconds": self._config.open_seconds,
            },
        )

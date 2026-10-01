"""Fallback chain + circuit breaker for OpenVault proxy hops."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openmw.openvault.paths import fallback_path
from openmw.openvault.vault.parks import (
    clip_error_text,
    delete_park,
    ensure_park_schema,
    load_parks,
    save_park,
)
from openmw.openvault.vault.quota import (
    park_wait_s,
    quota_blocks,
    quota_view,
    quota_window,
    tokens_used_by_key,
)
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
    # Per (key, model) rate-limit parks. A 429 on one catalog id must not hide
    # the rest of the key. Cleared only when that model's window expires.
    model_park_until: dict[str, float] = field(default_factory=dict)
    model_park_reason: dict[str, str] = field(default_factory=dict)
    model_error_text: dict[str, str] = field(default_factory=dict)
    error_text: str = ""


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


# In-memory single-turn recency. Not a ledger write: the event loop must not
# gain a per-request database insert just to remember which key went last.
_spread_lock = threading.Lock()
_last_used: dict[str, int] = {}
_use_seq = 0


def note_key_used(key_id: str) -> None:
    """Record that this vault key was sent. Memory only."""
    global _use_seq
    if not key_id:
        return
    with _spread_lock:
        _use_seq += 1
        _last_used[key_id] = _use_seq


def reset_key_spread() -> None:
    """Drop in-memory spread state. Used by tests."""
    global _use_seq
    with _spread_lock:
        _last_used.clear()
        _use_seq = 0


def _priority_bands(ranked: list[KeyRecord]) -> list[list[KeyRecord]]:
    bands: list[list[KeyRecord]] = []
    current: list[KeyRecord] = []
    priority: int | None = None
    for record in ranked:
        if priority is None or record.priority != priority:
            if current:
                bands.append(current)
            current = [record]
            priority = record.priority
        else:
            current.append(record)
    if current:
        bands.append(current)
    return bands


def _sibling_providers(bands: list[list[KeyRecord]]) -> set[str]:
    found: set[str] = set()
    for band in bands:
        counts: dict[str, int] = {}
        for record in band:
            counts[record.provider] = counts.get(record.provider, 0) + 1
        for provider, count in counts.items():
            if count >= 2:
                found.add(provider)
    return found


def _quota_weights(records: list[KeyRecord], db_path: Path, providers: set[str]) -> dict[str, int]:
    """Catalog daily limit minus this key's ledger tokens. Missing limit => weight 1."""
    weights: dict[str, int] = {}
    now = time.time()
    for provider in providers:
        start, _nxt, limit = quota_window(provider, now)
        if limit is None:
            continue
        used = tokens_used_by_key(db_path, provider=provider, since=start)
        for record in records:
            if record.provider == provider:
                weights[record.id] = max(0, limit - used.get(record.id, 0))
    return weights


def _spread_key(
    record: KeyRecord,
    weights: dict[str, int],
    last: dict[str, int],
    max_seq: int,
) -> tuple[int, str]:
    """Least-recently-used, weighted by quota remaining. Higher score is tried first."""
    seq = last.get(record.id, 0)
    idle = (max_seq + 1) - seq
    weight = weights.get(record.id, 1)
    score = 0 if weight <= 0 else idle * weight
    return (-score, record.id)


def _fill_provider_slots(
    band: list[KeyRecord],
    weights: dict[str, int],
    last: dict[str, int],
    max_seq: int,
) -> list[KeyRecord]:
    """Reorder same-provider keys into the slots that provider already occupies."""
    slots: dict[str, list[int]] = {}
    for index, record in enumerate(band):
        slots.setdefault(record.provider, []).append(index)
    arranged = list(band)
    for indexes in slots.values():
        if len(indexes) < 2:
            continue
        chosen = sorted(
            (band[index] for index in indexes),
            key=lambda record: _spread_key(record, weights, last, max_seq),
        )
        for index, record in zip(indexes, chosen, strict=True):
            arranged[index] = record
    return arranged


def _spread_single_turn(ranked: list[KeyRecord], db_path: Path) -> list[KeyRecord]:
    """Spread keys inside a priority band. Provider order and band order stay."""
    if len(ranked) < 2:
        return ranked
    bands = _priority_bands(ranked)
    providers = _sibling_providers(bands)
    if not providers:
        return ranked
    weights = _quota_weights(ranked, db_path, providers)
    with _spread_lock:
        last = dict(_last_used)
    max_seq = max(last.values(), default=0)
    out: list[KeyRecord] = []
    for band in bands:
        out.extend(_fill_provider_slots(band, weights, last, max_seq))
    return out


def _rank_band(records: list[KeyRecord], affinity_key: str, db_path: Path) -> list[KeyRecord]:
    """Sort one role group.

    An affinity key (multi-turn head or ``prompt_cache_key``) keeps rendezvous
    order inside a priority. A single-turn call has no affinity key: same-provider
    keys inside a priority band are least-recently-used, weighted by quota
    remaining. Different providers keep the slots they already had.
    """
    if affinity_key:
        return sorted(
            records,
            key=lambda record: (
                record.priority,
                -rendezvous_score(affinity_key, record.id),
                record.id,
            ),
        )
    ranked = sorted(records, key=lambda record: record.priority)
    return _spread_single_turn(ranked, db_path)


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
        ensure_park_schema(self._vault.db_path)
        self._load_parks()

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

    def _load_parks(self) -> None:
        for row in load_parks(self._vault.db_path):
            circ = self._circuit(row.key_id)
            if row.model:
                circ.model_park_until[row.model] = row.park_until
                circ.model_park_reason[row.model] = row.reason
                if row.error_text:
                    circ.model_error_text[row.model] = row.error_text
                continue
            circ.park_until = row.park_until
            circ.park_reason = row.reason or None
            circ.error_text = row.error_text
            circ.last_error = row.reason or None

    def _wait_s(self, key_id: str, cooldown_ms: int, reason: str) -> float:
        wait_s = max(0.0, float(cooldown_ms) / 1000.0)
        record = self._vault.get(key_id)
        provider = record.provider if record is not None else ""
        return park_wait_s(provider, wait_s, reason)

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
        if quota_blocks(record.provider, self._vault.db_path):
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
        conversation across accounts and every upstream prompt cache stays cold,
        so the caller pays full input price on every turn. The re-order happens
        strictly within a priority band, so health, park windows, role order
        and the operator's own priorities all still win.

        With an affinity key, rendezvous hashing breaks ties inside the band.
        With none (a single-turn call), same-provider keys in that band are
        least-recently-used, weighted by quota remaining in the usage ledger.
        The order of providers, and the order of bands, does not change.
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
            ordered.extend(_rank_band(available, affinity_key, self._vault.db_path))
        extras_available = [r for r in extras if self._is_available(r, now)]
        ordered.extend(_rank_band(extras_available, affinity_key, self._vault.db_path))
        return ordered

    def record_success(self, key_id: str) -> None:
        circ = self._circuit(key_id)
        circ.failures = 0
        circ.state = "closed"
        circ.opened_at = None
        circ.last_error = None
        circ.park_until = None
        circ.park_reason = None
        circ.error_text = ""
        delete_park(self._vault.db_path, key_id)
        # Leave model_park_until alone: a later model answering does not
        # un-park a sibling that just returned 429.

    def model_is_parked(self, key_id: str, model: str) -> bool:
        """True while this (key, model) is inside its 429 window."""
        circ = self._circuit(key_id)
        until = circ.model_park_until.get(model)
        if until is None:
            return False
        if time.time() >= until:
            circ.model_park_until.pop(model, None)
            circ.model_park_reason.pop(model, None)
            circ.model_error_text.pop(model, None)
            return False
        return True

    def record_model_park(
        self,
        key_id: str,
        model: str,
        cooldown_ms: int,
        reason: str,
        *,
        error_text: str = "",
    ) -> None:
        """Hide one model on this key. Does not park the key itself."""
        circ = self._circuit(key_id)
        wait_s = self._wait_s(key_id, cooldown_ms, reason)
        until = time.time() + wait_s
        stored = clip_error_text(error_text) if error_text else ""
        circ.model_park_until[model] = until
        circ.model_park_reason[model] = reason
        if stored:
            circ.model_error_text[model] = stored
        save_park(
            self._vault.db_path,
            key_id=key_id,
            model=model,
            park_until=until,
            reason=reason,
            error_text=stored,
        )

    def record_failure(self, key_id: str, error: str) -> None:
        circ = self._circuit(key_id)
        circ.failures += 1
        circ.last_error = error
        if circ.state == "half_open" or circ.failures >= self._config.failure_threshold:
            circ.state = "open"
            circ.opened_at = time.time()

    def record_park(
        self,
        key_id: str,
        cooldown_ms: int,
        reason: str,
        *,
        error_text: str = "",
    ) -> None:
        """Temporarily hide a key without counting a circuit failure.

        Rate limits and stale OAuth must not open the hop circuit — that is the
        live bug fixed by DESIGN_TIERED_QUEUE_LB §1.1 / §4.2.
        The park is written to keys.db so a new process sees the same window.
        """
        circ = self._circuit(key_id)
        wait_s = self._wait_s(key_id, cooldown_ms, reason)
        until = time.time() + wait_s
        stored = clip_error_text(error_text) if error_text else ""
        circ.park_until = until
        circ.park_reason = reason
        circ.last_error = reason
        circ.error_text = stored
        save_park(
            self._vault.db_path,
            key_id=key_id,
            model="",
            park_until=until,
            reason=reason,
            error_text=stored,
        )

    def key_is_parked(self, key_id: str) -> bool:
        """True while this key is inside a park window."""
        circ = self._circuit(key_id)
        return circ.park_until is not None and time.time() < circ.park_until

    def key_park_reason(self, key_id: str) -> str | None:
        """Park reason while the key is parked, else None."""
        if not self.key_is_parked(key_id):
            return None
        return self._circuit(key_id).park_reason

    def soonest_key_park_until(self, key_ids: list[str]) -> float | None:
        """Earliest still-active key park, or None when none of them are parked."""
        now = time.time()
        untils: list[float] = []
        for key_id in key_ids:
            circ = self._circuit(key_id)
            if circ.park_until is not None and now < circ.park_until:
                untils.append(circ.park_until)
        if not untils:
            return None
        return min(untils)

    def usable_provider_count(self) -> int:
        """Pooled providers that are not parked, chat-unusable, or out of quota."""
        if self._vault.seal.is_sealed:
            return 0
        # Local import: chat_probe imports FallbackManager.
        from openmw.openvault.vault.chat_probe import chat_unusable_ids

        blocked = chat_unusable_ids(self._vault.db_path)
        found: set[str] = set()
        for record in self._vault.pooled_ordered():
            if self.key_is_parked(record.id):
                continue
            if record.id in blocked:
                continue
            if quota_blocks(record.provider, self._vault.db_path):
                continue
            found.add(record.provider)
        return len(found)

    def hop_circuit_is_open(self, key_id: str) -> bool:
        """True while this hop's circuit is open and the cool-down has not elapsed."""
        circ = self._circuit(key_id)
        if circ.state != "open":
            return False
        if circ.opened_at is None:
            return True
        return (time.time() - circ.opened_at) < self._config.open_seconds

    def park_retry_after_s(self, key_id: str, model: str | None = None) -> int | None:
        """Seconds until the soonest key or model park lifts. None when neither is parked."""
        now = time.time()
        circ = self._circuit(key_id)
        untils: list[float] = []
        if circ.park_until is not None and now < circ.park_until:
            untils.append(circ.park_until)
        if model is not None:
            model_until = circ.model_park_until.get(model)
            if model_until is not None and now < model_until:
                untils.append(model_until)
        if not untils:
            return None
        remaining = min(untils) - now
        if remaining <= 0:
            return None
        return max(1, math.ceil(remaining))

    def status(self) -> FallbackStatus:
        hops: list[dict[str, object]] = []
        now = time.time()
        for record in self._vault.pooled_ordered():
            circ = self._circuit(record.id)
            if circ.park_until is None:
                park_state = ""
            elif now < circ.park_until:
                park_state = "parked"
            else:
                park_state = "expired"
            view = quota_view(record.provider, self._vault.db_path, now=now)
            health = record.precheck_status
            if view.status == "quota_exhausted" and health in ("ok", "unknown"):
                health = "quota_exhausted"
            hop: dict[str, object] = {
                "key_id": record.id,
                "label": record.label,
                "provider": record.provider,
                "role": record.role,
                "priority": record.priority,
                "precheck_status": record.precheck_status,
                "health": health,
                "circuit": circ.state,
                "failures": circ.failures,
                "last_error": circ.last_error or record.last_error,
                "last_latency_ms": record.last_latency_ms,
                "park_until": circ.park_until,
                "park_reason": circ.park_reason,
                "park_state": park_state,
                "error_text": circ.error_text,
                "served_local": False,
            }
            if view.status == "quota_exhausted" and view.reset_at:
                hop["quota_reset_at"] = view.reset_at
            hops.append(hop)
        return FallbackStatus(
            hops=hops,
            config={
                "role_order": self._config.role_order,
                "failure_threshold": self._config.failure_threshold,
                "open_seconds": self._config.open_seconds,
            },
        )

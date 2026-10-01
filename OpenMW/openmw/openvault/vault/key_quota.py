"""Admin per-key quota snapshot.

Reads ``usage_events``, ``hop_parks``, and ``openrouter_probe``. Does not
ALTER ``keys`` or ``usage_events``. The masked id is the only key identity
in the payload. Provider secrets are not selected.
"""

from __future__ import annotations

import time

from openmw.openvault.vault.key_add import mask_key_id
from openmw.openvault.vault.openrouter_probe import load_openrouter_probes
from openmw.openvault.vault.parks import ParkRow, clip_error_text, ensure_park_schema, load_parks
from openmw.openvault.vault.quota import iso_utc, quota_window, tokens_used_for_key
from openmw.openvault.vault.store import KeyVault


def _park_fields(rows: list[ParkRow], key_id: str, now: float) -> tuple[str, str | None, str]:
    mine = [row for row in rows if row.key_id == key_id]
    if not mine:
        return "", None, ""
    active = [row for row in mine if row.park_until > now]
    if active:
        chosen = min(active, key=lambda row: row.park_until)
        state = "active"
    else:
        chosen = max(mine, key=lambda row: row.park_until)
        state = "expired"
    return state, iso_utc(chosen.park_until), clip_error_text(chosen.error_text)


def quota_snapshot(vault: KeyVault, *, now: float | None = None) -> dict[str, object]:
    """One row per vault key. No secret, no full key id."""
    current = time.time() if now is None else now
    ensure_park_schema(vault.db_path)
    parks = load_parks(vault.db_path)
    probes = load_openrouter_probes(vault.db_path)
    rows: list[dict[str, object]] = []
    for record in vault.list_keys():
        start, nxt, limit = quota_window(record.provider, current)
        used = tokens_used_for_key(
            vault.db_path,
            provider=record.provider,
            vault_key_id=record.id,
            since=start,
        )
        state, until, error_text = _park_fields(parks, record.id, current)
        probe = probes.get(record.id)
        rows.append(
            {
                "masked_id": mask_key_id(record.id),
                "provider": record.provider,
                "tokens_used": used,
                "daily_limit": limit,
                "reset_at": iso_utc(nxt),
                "park_state": state,
                "park_until": until,
                "error_text": error_text,
                "precheck_status": record.precheck_status,
                "limit_remaining": None if probe is None else probe.limit_remaining,
                "is_free_tier": None if probe is None else probe.is_free_tier,
            }
        )
    return {"ok": True, "keys": rows}

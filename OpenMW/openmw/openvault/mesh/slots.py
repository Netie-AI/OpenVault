"""Unified model-slot registry — local engines + Cortex catalog + selection."""

from __future__ import annotations

from typing import Any

from openmw.openvault.mesh.cortex_client import CortexClient
from openmw.openvault.mesh.orchestration import OrchestrationSelection, load_selection


def _slot_from_model(model: dict[str, Any], *, origin: str) -> dict[str, Any]:
    mid = str(model.get("id") or model.get("name") or "")
    return {
        "id": mid,
        "name": str(model.get("name") or mid),
        "origin": origin,
        "source": str(model.get("source") or origin),
        "tier": model.get("tier"),
        "cortex_tier_hint": model.get("cortex_tier_hint"),
        "online": bool(model.get("online", origin != "offline")),
        "acknowledged": True,
    }


async def list_slots(cortex: CortexClient) -> dict[str, Any]:
    """Merge local OpenMW registry + Cortex models; mark selection."""
    selection = load_selection()
    models_payload = await cortex.models()
    engines_payload = await cortex.engines()

    slots: list[dict[str, Any]] = []
    seen: set[str] = set()

    for model in models_payload.get("models", []):
        if not isinstance(model, dict):
            continue
        slot = _slot_from_model(
            model,
            origin="cortex" if models_payload.get("online") else "local",
        )
        if slot["id"] and slot["id"] not in seen:
            seen.add(slot["id"])
            slots.append(slot)

    for model in cortex.local_models():
        slot = _slot_from_model(model, origin="local")
        if slot["id"] and slot["id"] not in seen:
            seen.add(slot["id"])
            slots.append(slot)

    # Engine backends as slots (ollama etc.) even without model ids.
    engines_raw = engines_payload.get("engines")
    engine_list: list[Any]
    if isinstance(engines_raw, dict):
        engine_list = list(engines_raw.values()) if engines_raw else []
        if not engine_list and engines_raw:
            engine_list = [engines_raw]
    elif isinstance(engines_raw, list):
        engine_list = engines_raw
    else:
        engine_list = []

    for eng in engine_list:
        if not isinstance(eng, dict):
            continue
        eid = str(eng.get("id") or eng.get("name") or eng.get("engine_id") or "")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        slots.append(
            {
                "id": eid,
                "name": str(eng.get("name") or eid),
                "origin": "engine",
                "source": str(engines_payload.get("source") or "engine"),
                "tier": eng.get("tier"),
                "cortex_tier_hint": None,
                "online": bool(engines_payload.get("online")),
                "acknowledged": True,
            }
        )

    primary = selection.primary_model
    for slot in slots:
        slot["selected"] = bool(primary) and slot["id"] == primary
        slot["fallback"] = slot["id"] in selection.fallback_models

    return {
        "acknowledged": True,
        "count": len(slots),
        "slots": slots,
        "selection": {
            "primary_model": selection.primary_model,
            "fallback_models": list(selection.fallback_models),
            "cortex_tier": selection.cortex_tier,
            "engine_id": selection.engine_id,
            "notes": selection.notes,
        },
        "cortex_online": bool(models_payload.get("online") or engines_payload.get("online")),
    }


def slots_summary_sync(selection: OrchestrationSelection | None = None) -> dict[str, Any]:
    """Sync fallback for tests without Cortex HTTP."""
    sel = selection if selection is not None else load_selection()
    client = CortexClient()
    local = [_slot_from_model(m, origin="local") for m in client.local_models()]
    for slot in local:
        slot["selected"] = bool(sel.primary_model) and slot["id"] == sel.primary_model
        slot["fallback"] = slot["id"] in sel.fallback_models
    return {
        "acknowledged": True,
        "count": len(local),
        "slots": local,
        "selection": {
            "primary_model": sel.primary_model,
            "fallback_models": list(sel.fallback_models),
            "cortex_tier": sel.cortex_tier,
            "engine_id": sel.engine_id,
            "notes": sel.notes,
        },
        "cortex_online": False,
    }

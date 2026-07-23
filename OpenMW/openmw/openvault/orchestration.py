"""Persist user's chosen orchestration models / Cortex tiers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from openmw.openvault.paths import selection_path


@dataclass
class OrchestrationSelection:
    """Models assigned to assist Cortex / OpenVault routing."""

    primary_model: str = ""
    fallback_models: list[str] = field(default_factory=list)
    cortex_tier: str = "T1"
    engine_id: str = "ollama"
    notes: str = ""


def load_selection(path: Path | None = None) -> OrchestrationSelection:
    target = path if path is not None else selection_path()
    if not target.is_file():
        return OrchestrationSelection()
    raw = json.loads(target.read_text(encoding="utf-8"))
    return OrchestrationSelection(
        primary_model=str(raw.get("primary_model", "")),
        fallback_models=list(raw.get("fallback_models", [])),
        cortex_tier=str(raw.get("cortex_tier", "T1")),
        engine_id=str(raw.get("engine_id", "ollama")),
        notes=str(raw.get("notes", "")),
    )


def save_selection(
    selection: OrchestrationSelection,
    path: Path | None = None,
) -> OrchestrationSelection:
    target = path if path is not None else selection_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asdict(selection), indent=2), encoding="utf-8")
    return selection

"""Cheap help-route heuristics. Not NVIDIA llm-router. Not an LLM-as-judge.

When the user is trying to get a free key or a cheap hop, walk ``free`` then
``cheap`` before primary. Fallback and breakers still fail closed.
"""

from __future__ import annotations

from typing import Any

from openmw.openvault.vault.store import KeyRecord

HELP_MARKERS: tuple[str, ...] = (
    "register",
    "free key",
    "free tier",
    "get a key",
    "sign up",
    "signup",
    "no budget",
    "cheapest",
    "help me route",
    "free model",
    "free token",
)

_ROLE_RANK = {"free": 0, "cheap": 1, "backup": 2, "primary": 3}


def _message_blob(body: dict[str, Any] | None) -> str:
    if not body:
        return ""
    chunks: list[str] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            chunks.append(content)
    return " ".join(chunks).lower()


def wants_free_help(body: dict[str, Any] | None) -> bool:
    blob = _message_blob(body)
    if not blob:
        return False
    return any(marker in blob for marker in HELP_MARKERS)


def prefer_free_then_cheap(records: list[KeyRecord]) -> list[KeyRecord]:
    """Stable sort: free, cheap, backup, primary. Does not drop hops."""
    return sorted(
        records,
        key=lambda rec: (_ROLE_RANK.get(str(rec.role), 4), rec.priority, rec.id),
    )

"""Deck-shuffle round-robin — draw without replacement before reshuffle."""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass

MAX_RR_COUNTERS = 500


@dataclass
class _Deck:
    order: list[str]
    index: int
    ids_key: str


_decks: dict[str, _Deck] = {}
_deck_lock = threading.Lock()


def fisher_yates_shuffle(items: list[str]) -> list[str]:
    """Return a new shuffled list (does not mutate the input)."""
    result = list(items)
    for i in range(len(result) - 1, 0, -1):
        j = random.randrange(i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def _ids_key(item_ids: list[str]) -> str:
    return ",".join(sorted(item_ids))


def get_next_from_deck(namespace: str, item_ids: list[str]) -> str:
    """Draw the next id from a namespace deck; reshuffle when exhausted."""
    if not item_ids:
        return ""
    if len(item_ids) == 1:
        return item_ids[0]

    with _deck_lock:
        key = _ids_key(item_ids)
        existing = _decks.get(namespace)

        if existing and existing.ids_key == key and existing.index < len(existing.order):
            chosen = existing.order[existing.index]
            _decks[namespace] = _Deck(existing.order, existing.index + 1, key)
            return chosen

        last_used: str | None = None
        if existing and existing.ids_key == key and existing.order:
            last_used = existing.order[-1]

        new_order = fisher_yates_shuffle(item_ids)
        if last_used is not None and new_order[0] == last_used and len(new_order) > 1:
            swap_idx = 1 + random.randrange(len(new_order) - 1)
            new_order[0], new_order[swap_idx] = new_order[swap_idx], new_order[0]

        _decks[namespace] = _Deck(new_order, 1, key)
        return new_order[0]


def reset_decks() -> None:
    """Clear all decks — for tests."""
    with _deck_lock:
        _decks.clear()

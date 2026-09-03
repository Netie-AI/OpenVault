"""Per-connection vault-key rotator — skip invalid, round-robin one-shots."""

from __future__ import annotations

from types import SimpleNamespace

from openmw.openvault.route.key_rotator import (
    FAILURE_THRESHOLD,
    key_is_invalid,
    record_key_failure,
    record_key_success,
    reset_all,
    rotate_same_provider_bands,
)


def setup_function() -> None:
    reset_all()


def test_two_auth_failures_mark_key_invalid() -> None:
    assert key_is_invalid("openai", "k1") is False
    record_key_failure("openai", "k1")
    assert key_is_invalid("openai", "k1") is False
    for _ in range(FAILURE_THRESHOLD - 1):
        record_key_failure("openai", "k1")
    assert key_is_invalid("openai", "k1") is True
    record_key_success("openai", "k1")
    assert key_is_invalid("openai", "k1") is False


def test_rotate_spreads_one_shots_and_pins_affinity() -> None:
    a = SimpleNamespace(id="a", provider="openai", role="primary")
    b = SimpleNamespace(id="b", provider="openai", role="primary")
    first = rotate_same_provider_bands([a, b], affinity_key="")
    second = rotate_same_provider_bands([a, b], affinity_key="")
    assert [r.id for r in first] == ["a", "b"]
    assert [r.id for r in second] == ["b", "a"]

    pinned = rotate_same_provider_bands([a, b], affinity_key="conv-1")
    assert [r.id for r in pinned] == ["a", "b"]

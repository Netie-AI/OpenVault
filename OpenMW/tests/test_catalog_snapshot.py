"""Offline snapshot of the seven FreeRoute catalogs this refresh owns.

No network. A future edit to one of these pools shows up as a reviewed diff
in this file. The seven are the catalogs named by OpenVault #74: openrouter,
groq, google, mistral, nvidia, cerebras, and the local hop.
"""

from __future__ import annotations

import pytest

from openmw.openvault.vault.providers import (
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_LOCAL_MODEL,
    LOCAL_QWEN_ID,
    PROVIDER_CATALOG,
    ProviderSpec,
    resolve_model,
)

# Order is part of the contract: resolve_model(..., "auto") returns index 0.
SEVEN_CATALOGS: dict[str, dict[str, object]] = {
    "openrouter": {
        "free_notes": "Pinned :free ids only (prompt and completion price 0)",
        "chat_models": (
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "thinkingmachines/inkling:free",
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
        ),
        "vision_models": (
            "thinkingmachines/inkling:free",
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-31b-it:free",
        ),
        "reasoning_models": (),
    },
    "groq": {
        "free_notes": "Fast free tier RPM; great fallback hop",
        "chat_models": (
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-20b",
        ),
        "vision_models": ("qwen/qwen3.8-27b",),
        "reasoning_models": (
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
        ),
    },
    "google": {
        "free_notes": "Gemini free tier via AI Studio (OpenAI-compat endpoint)",
        "chat_models": (
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
            "gemini-3.1-flash-lite",
        ),
        "vision_models": (
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
        ),
        "reasoning_models": (
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
            "gemini-3.1-flash-lite",
        ),
    },
    "mistral": {
        "free_notes": "Experiment / free credits on signup",
        "chat_models": (
            "mistral-small-latest",
            "ministral-8b-latest",
        ),
        "vision_models": ("mistral-small-latest",),
        "reasoning_models": (),
    },
    "nvidia": {
        "free_notes": "build.nvidia.com / NIM OpenAI-compatible; keys typically nvapi-…",
        "chat_models": ("nvidia/llama-3.1-nemotron-70b-instruct",),
        "vision_models": (),
        "reasoning_models": ("nvidia/llama-3.1-nemotron-70b-instruct",),
    },
    "cerebras": {
        "free_notes": "Trial ($5 credits, 30 days, 5 RPM), not a free tier",
        "chat_models": (
            "gpt-oss-120b",
            "qwen-3.8-27b",
        ),
        "vision_models": ("qwen-3.8-27b",),
        "reasoning_models": ("gpt-oss-120b",),
    },
    "local_qwen": {
        "name": "Local Qwen (loopback)",
        "base_url": "http://127.0.0.1:8080/v1",
        "tier": "local",
        "local_hop": True,
        "placeholder_secret": "local",
        "free_notes": (
            "No cloud key. OpenAI-compat llama.cpp or Ollama on loopback. "
            "Set OPENVAULT_LOCAL_BASE_URL + OPENVAULT_LOCAL_MODEL. "
            "OpenVault does not start the server or pull a model."
        ),
        "chat_models": ("qwen2.5:0.5b",),
        "vision_models": (),
        "reasoning_models": (),
    },
}

REMOVED_MODEL_IDS: tuple[str, ...] = (
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "qwen/qwen3.6-27b",
    "llama-3.3-70b",
    "llama3.1-8b",
    "open-mistral-nemo",
    "meta/llama-3.1-8b-instruct",
    "meta/llama-3.1-70b-instruct",
    "mistralai/mistral-nemotron",
)


def _spec(provider_id: str) -> ProviderSpec:
    for spec in PROVIDER_CATALOG:
        if spec.id == provider_id:
            return spec
    raise AssertionError(provider_id)


def _served_ids(spec: ProviderSpec) -> set[str]:
    return set(spec.chat_models) | set(spec.vision_models) | set(spec.reasoning_models)


def test_seven_catalogs_match_offline_snapshot() -> None:
    assert len(SEVEN_CATALOGS) == 7
    for provider_id, expected in SEVEN_CATALOGS.items():
        spec = _spec(provider_id)
        for field, want in expected.items():
            assert getattr(spec, field) == want, provider_id + "." + field


def test_openrouter_auto_and_every_id_are_free_suffix() -> None:
    spec = _spec("openrouter")
    auto = resolve_model("openrouter", "auto")
    assert auto is not None
    assert auto.endswith(":free")
    vision = resolve_model("openrouter", "auto", multimodal=True)
    assert vision is not None
    assert vision.endswith(":free")
    assert vision in spec.vision_models
    ids = _served_ids(spec)
    assert ids
    assert all(mid.endswith(":free") for mid in ids)


@pytest.mark.parametrize("model_id", REMOVED_MODEL_IDS)
def test_removed_model_id_is_absent(model_id: str) -> None:
    for spec in PROVIDER_CATALOG:
        assert model_id not in _served_ids(spec), spec.id


def test_local_qwen_unchanged() -> None:
    assert LOCAL_QWEN_ID == "local_qwen"
    assert DEFAULT_LOCAL_MODEL == "qwen2.5:0.5b"
    assert DEFAULT_LOCAL_BASE_URL == "http://127.0.0.1:8080/v1"
    spec = _spec(LOCAL_QWEN_ID)
    pinned = SEVEN_CATALOGS["local_qwen"]
    assert spec.id == "local_qwen"
    assert spec.local_hop is True
    assert spec.chat_models == (DEFAULT_LOCAL_MODEL,)
    assert spec.base_url == DEFAULT_LOCAL_BASE_URL
    assert spec.free_notes == pinned["free_notes"]
    assert spec.vision_models == ()
    assert spec.reasoning_models == ()


def test_cerebras_notes_say_trial_not_free_tier() -> None:
    notes = _spec("cerebras").free_notes
    assert notes == "Trial ($5 credits, 30 days, 5 RPM), not a free tier"
    assert "free tier for" not in notes

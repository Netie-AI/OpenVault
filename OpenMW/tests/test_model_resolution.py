"""Model-id resolution for the /v1 proxy.

Regression cover for a fault that presented three separate times as "this provider
is down": an id the upstream does not serve answers 404, which is indistinguishable
from an outage. The proxy forwarded the caller's `model` verbatim to every hop, so
`{"model": "auto"}` reached Groq as a model literally named "auto" and 404'd on a key
whose precheck said `ok`.
"""

from __future__ import annotations

import pytest

from openmw.openvault.vault.providers import (
    PROVIDER_CATALOG,
    get_provider,
    models_for,
    resolve_model,
)


@pytest.mark.parametrize("alias", ["auto", "", "default", "AUTO", "any", "free"])
def test_aliases_become_the_provider_first_choice(alias: str) -> None:
    assert resolve_model("groq", alias) == "openai/gpt-oss-120b"


def test_none_is_treated_as_auto() -> None:
    assert resolve_model("groq", None) == "openai/gpt-oss-120b"


def test_known_id_is_honoured_unchanged() -> None:
    assert resolve_model("groq", "llama-3.1-8b-instant") == "llama-3.1-8b-instant"
    assert resolve_model("google", "gemini-3.5-flash") == "gemini-3.5-flash"


def test_other_providers_id_falls_back_rather_than_404ing() -> None:
    """`gpt-4o` arriving at Groq is a preference we cannot meet, not a fatal error.

    Routing across heterogeneous providers is the whole point of the proxy, so an
    unmeetable preference downgrades to this provider's best model.
    """
    assert resolve_model("groq", "gpt-4o") == "openai/gpt-oss-120b"
    assert resolve_model("google", "openai/gpt-oss-120b") == "gemini-3.5-flash"


def test_multimodal_uses_vision_pool() -> None:
    assert resolve_model("groq", "auto", multimodal=True) == "qwen/qwen3.6-27b"
    assert resolve_model("google", "auto", multimodal=True) == "gemini-3.5-flash"


def test_text_only_provider_refuses_multimodal_instead_of_dropping_the_image() -> None:
    """Returning a text model here would silently discard the image."""
    assert get_provider("deepseek") is not None
    assert models_for("deepseek", multimodal=True) == ()
    assert resolve_model("deepseek", "auto", multimodal=True) is None


def test_uncatalogued_provider_passes_a_concrete_id_through() -> None:
    """Providers without a catalogued pool still honour a caller-supplied id.

    Skipping those outright would make the proxy unusable for anthropic (Messages
    API, out of scope), speech-only entries, and every custom endpoint, so a
    concrete id goes upstream untouched.
    """
    assert resolve_model("openai", "gpt-4o-mini") == "gpt-4o-mini"
    assert resolve_model("deepseek", "deepseek-v4-flash") == "deepseek-v4-flash"
    assert resolve_model("deepseek", "any-model", multimodal=True) == "any-model"
    assert resolve_model("anthropic", "claude-opus-4") == "claude-opus-4"


def test_core_byok_auto_resolves_concrete_model() -> None:
    """FreeRoute buyer path uses model=auto; empty chat_models used to skip the hop."""
    for provider in ("openai", "ollama", "deepseek", "litellm", "cortex"):
        resolved = resolve_model(provider, "auto")
        assert resolved is not None, f"{provider} auto must not skip"
        assert resolved == models_for(provider)[0]
        assert resolve_model(provider, "") == resolved
        assert resolve_model(provider, None) == resolved


def test_alias_without_catalogued_pool_is_skipped_not_guessed() -> None:
    """ "auto" with nothing catalogued is exactly the case that 404'd upstream."""
    assert resolve_model("anthropic", "auto") is None
    assert resolve_model("together", "") is None
    assert resolve_model("mistral", "auto") == "mistral-small-latest"


def test_unknown_provider_keeps_a_concrete_id_but_never_invents_one() -> None:
    assert resolve_model("nope", "some-model") == "some-model"
    assert resolve_model("nope", "auto") is None


def test_every_catalogued_model_id_is_plausible() -> None:
    """Cheap shape check - a stray blank or whitespace id would 404 at runtime."""
    for spec in PROVIDER_CATALOG:
        for mid in spec.chat_models + spec.vision_models:
            assert mid and mid == mid.strip(), f"{spec.id} has a malformed model id {mid!r}"


def test_vision_models_are_a_subset_of_chat_models() -> None:
    """A vision id the provider cannot serve for chat would be unreachable."""
    for spec in PROVIDER_CATALOG:
        if not spec.vision_models:
            continue
        extra = set(spec.vision_models) - set(spec.chat_models)
        assert not extra, f"{spec.id} lists vision-only ids not in chat_models: {sorted(extra)}"


def test_catalog_is_exposed_over_the_wire() -> None:
    """The UI needs these to offer a model picker per key."""
    spec = get_provider("groq")
    assert spec is not None
    d = spec.to_dict()
    assert d["chat_models"][0] == "openai/gpt-oss-120b"
    assert isinstance(d["chat_models"], list)
    assert isinstance(d["vision_models"], list)


# --- reasoning budget floor -------------------------------------------------------
# gpt-oss models write their chain of thought from the same completion budget as the
# answer. Measured: max_tokens=32 -> 30 reasoning tokens, content "", finish "length".
# AirGPT treats empty content as a dead provider, so without a floor a healthy key
# would be cascaded away from on every short-budget call.

from openmw.openvault.vault.providers import (  # noqa: E402
    MIN_REASONING_BUDGET,
    budget_for,
    is_reasoning_model,
)


def test_reasoning_models_are_flagged() -> None:
    assert is_reasoning_model("groq", "openai/gpt-oss-120b")
    assert is_reasoning_model("groq", "openai/gpt-oss-20b")
    assert not is_reasoning_model("groq", "llama-3.1-8b-instant")
    assert not is_reasoning_model("google", "gemini-2.5-flash")


def test_small_budget_is_raised_to_the_floor() -> None:
    assert budget_for("groq", "openai/gpt-oss-120b", 32) == MIN_REASONING_BUDGET
    assert budget_for("groq", "openai/gpt-oss-120b", 8) == MIN_REASONING_BUDGET


def test_adequate_budget_is_left_alone() -> None:
    assert budget_for("groq", "openai/gpt-oss-120b", MIN_REASONING_BUDGET) is None
    assert budget_for("groq", "openai/gpt-oss-120b", 4096) is None


def test_budget_is_never_lowered() -> None:
    """A large budget may be deliberate; the floor only ever raises."""
    for req in (MIN_REASONING_BUDGET, 2048, 100_000):
        assert budget_for("groq", "openai/gpt-oss-120b", req) is None


def test_non_reasoning_and_unset_budgets_untouched() -> None:
    assert budget_for("groq", "llama-3.1-8b-instant", 32) is None
    assert budget_for("groq", "openai/gpt-oss-120b", None) is None
    assert budget_for("nope", "whatever", 8) is None


def test_reasoning_models_are_a_subset_of_chat_models() -> None:
    for spec in PROVIDER_CATALOG:
        extra = set(spec.reasoning_models) - set(spec.chat_models)
        assert not extra, f"{spec.id} flags unreachable reasoning ids: {sorted(extra)}"

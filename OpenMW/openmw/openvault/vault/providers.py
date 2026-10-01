"""Provider catalog absorbed from OmniRoute / OpenRouter / LiteLLM / Ollama patterns.

Not a 250-provider clone — a curated, honest set with free tiers, register links,
downtime probes, and Cortex/AirGPT essential coverage.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

ProviderTier = Literal["free", "freemium", "paid", "local"]


@dataclass(frozen=True)
class ProviderSpec:
    """One upstream that OpenVault can vault + precheck + route."""

    id: str
    name: str
    base_url: str
    default_role: str  # primary|backup|cheap|free
    tier: ProviderTier
    register_url: str
    docs_url: str
    health_path: str  # relative to base_url
    openai_compatible: bool = True
    free_notes: str = ""
    needed_by: tuple[str, ...] = ()
    status_page: str = ""
    placeholder_secret: str = ""  # e.g. ollama ignores key
    # Model ids this provider actually serves, strongest first. The proxy used to
    # forward the caller's `model` verbatim to every hop, so a request for "auto"
    # reached Groq as a model named "auto" and came back 404 - a healthy key that
    # looked like a dead provider. Resolution needs a per-provider list.
    chat_models: tuple[str, ...] = ()
    # Subset that accepts images. Empty means text-only, and a multimodal request
    # must skip this provider rather than silently drop the image.
    vision_models: tuple[str, ...] = ()
    # Largest input this provider accepts, in tokens. 0 means UNKNOWN, and every
    # reader treats unknown as "do not refuse" — an assumed window would reject
    # work that would have succeeded (R-0005). Populate only from a cited source,
    # the same discipline chat_models follows; operators can supply a table via
    # OPENVAULT_CONTEXT_WINDOWS until one is verified here.
    context_window: int = 0
    # Models that spend completion budget on reasoning tokens BEFORE writing content.
    # Measured on gpt-oss-120b: max_tokens=32 produced 30 reasoning tokens and an
    # empty string with finish_reason=length; the same prompt at 512 answered fine
    # after 159 reasoning tokens. Callers that treat empty content as a dead provider
    # (AirGPT does) would cascade away from a perfectly healthy key, so these need a
    # budget floor rather than a blanket "empty means broken".
    reasoning_models: tuple[str, ...] = ()
    # LOCAL-1: no-cloud-key FreeRoute hop. Distinct from vaulted ollama/litellm.
    local_hop: bool = False
    # Free-tier tokens per reset window. None means OpenVault does not track one.
    # The router reads this; it does not hard-code a provider's allowance.
    daily_token_limit: int | None = None
    # IANA zone whose local midnight ends the daily window. Empty means none.
    quota_reset_tz: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("local_hop", None)
        d["needed_by"] = list(self.needed_by)
        d["chat_models"] = list(self.chat_models)
        d["vision_models"] = list(self.vision_models)
        d["reasoning_models"] = list(self.reasoning_models)
        d["spendable"] = self.openai_compatible and bool(self.chat_models)
        # Cortex#274 hop_reported_local / count_local_spendable: JSON true only.
        d["served_local"] = bool(self.local_hop)
        return d


LOCAL_QWEN_ID = "local_qwen"
LOCAL_HOP_KEY_ID = "local:local_qwen"
LOCAL_BASE_URL_ENV = "OPENVAULT_LOCAL_BASE_URL"
LOCAL_MODEL_ENV = "OPENVAULT_LOCAL_MODEL"
DEFAULT_LOCAL_MODEL = "qwen2.5:0.5b"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"


# Curated catalog — OmniRoute-inspired free/paid + OpenRouter marketplace + Ollama local.
PROVIDER_CATALOG: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_role="primary",
        tier="paid",
        register_url="https://platform.openai.com/api-keys",
        docs_url="https://platform.openai.com/docs",
        health_path="/models",
        needed_by=("cortex", "airgpt", "openvault"),
        status_page="https://status.openai.com/",
        # Pinned from https://developers.openai.com/api/docs/models (2026-08).
        # gpt-5.6 is an alias for gpt-5.6-sol. gpt-4o* remain in API for callers
        # that still name them; auto prefers the current frontier line.
        chat_models=(
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.6",
            "gpt-4o",
            "gpt-4o-mini",
        ),
        vision_models=(
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.6",
            "gpt-4o",
            "gpt-4o-mini",
        ),
        reasoning_models=(
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
            "gpt-5.6",
        ),
    ),
    ProviderSpec(
        id="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com",
        default_role="primary",
        tier="paid",
        register_url="https://console.anthropic.com/settings/keys",
        docs_url="https://docs.anthropic.com/",
        health_path="/v1/models",
        openai_compatible=False,
        needed_by=("cortex", "airgpt", "openvault"),
        status_page="https://status.anthropic.com/",
    ),
    ProviderSpec(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_role="cheap",
        tier="freemium",
        register_url="https://openrouter.ai/keys",
        docs_url="https://openrouter.ai/docs",
        health_path="/models",
        free_notes="Pinned :free ids only (prompt and completion price 0)",
        needed_by=("cortex", "airgpt", "openvault"),
        status_page="https://status.openrouter.ai/",
        # Pinned 2026-10-01T08:00:04Z from https://openrouter.ai/api/v1/models.
        # Every id had pricing.prompt == 0 and pricing.completion == 0 and ends
        # with :free. Vision ids include "image" in architecture.input_modalities.
        # Paid ids were parking a credit-less key as credits_exhausted.
        chat_models=(
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "thinkingmachines/inkling:free",
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
        ),
        vision_models=(
            "thinkingmachines/inkling:free",
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-31b-it:free",
        ),
    ),
    ProviderSpec(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_role="free",
        tier="freemium",
        register_url="https://console.groq.com/keys",
        docs_url="https://console.groq.com/docs",
        health_path="/models",
        free_notes="Fast free tier RPM; great fallback hop",
        needed_by=("cortex", "airgpt"),
        # Verified 2026-10-01 against https://console.groq.com/docs/models and
        # https://console.groq.com/docs/deprecations. Shutdown for free/developer:
        # llama-3.1-8b-instant and llama-3.3-70b-versatile (2026-08-16),
        # qwen/qwen3.6-27b (2026-09-14, successor qwen/qwen3.8-27b).
        # qwen/qwen3.8-27b is the preview vision model (image input, 20 MB).
        chat_models=(
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-20b",
        ),
        vision_models=("qwen/qwen3.8-27b",),
        reasoning_models=(
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
        ),
        # gpt-oss-120b free tier is about 200K tokens/day. Health sums usage_events.
        daily_token_limit=200_000,
        quota_reset_tz="UTC",
    ),
    ProviderSpec(
        id="google",
        name="Google AI Studio",
        # OpenAI-compat base so FreeRoute proxy can keep Bearer + /chat/completions.
        # Native /v1beta rejects Bearer (expects x-goog-api-key) and has no
        # /chat/completions path — that mismatch was the false 401 on healthy keys.
        # Docs: https://ai.google.dev/gemini-api/docs/openai
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_role="free",
        tier="freemium",
        register_url="https://aistudio.google.com/apikey",
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
        health_path="/models",
        free_notes="Gemini free tier via AI Studio (OpenAI-compat endpoint)",
        needed_by=("cortex", "airgpt"),
        # Ordered by live probe 2026-08-03 against OpenAI-compat chat.
        # gemini-2.5-flash / -lite return 404 "no longer available to new users".
        chat_models=(
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
            "gemini-3.1-flash-lite",
        ),
        vision_models=(
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
        ),
        # Gemini 3.x often spends the completion budget on thought signatures before
        # content (observed: max_tokens=32 -> empty message, finish_reason=length).
        reasoning_models=(
            "gemini-3.5-flash",
            "gemini-3.6-flash",
            "gemini-3-flash-preview",
            "gemini-flash-latest",
            "gemini-3.1-flash-lite",
        ),
        # AI Studio RPD resets at midnight Pacific. A park lasts until then.
        quota_reset_tz="America/Los_Angeles",
    ),
    ProviderSpec(
        id="mistral",
        name="Mistral",
        base_url="https://api.mistral.ai/v1",
        default_role="cheap",
        tier="freemium",
        register_url="https://console.mistral.ai/api-keys/",
        docs_url="https://docs.mistral.ai/",
        health_path="/models",
        free_notes="Experiment / free credits on signup",
        needed_by=("cortex",),
        # open-mistral-nemo retired 2026-07-31 (overview row open-mistral-nemo-2407):
        # https://docs.mistral.ai/getting-started/models/models_overview/
        chat_models=(
            "mistral-small-latest",
            "ministral-8b-latest",
        ),
        vision_models=("mistral-small-latest",),
    ),
    ProviderSpec(
        id="nvidia",
        name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        default_role="cheap",
        tier="freemium",
        # Keys page: https://build.nvidia.com/settings/api-keys
        # (quickstart: https://docs.api.nvidia.com/nim/docs/api-quickstart)
        register_url="https://build.nvidia.com/settings/api-keys",
        docs_url="https://docs.api.nvidia.com/",
        health_path="/models",
        free_notes="build.nvidia.com / NIM OpenAI-compatible; keys typically nvapi-…",
        needed_by=("airgpt", "cortex"),
        # Refreshed 2026-10-01 from public GET
        # https://integrate.api.nvidia.com/v1/models (no key).
        # meta/llama-3.1-405b-instruct was not listed.
        # Ids containing "deepseek" on that list are omitted.
        # nvidia/llama-3.1-nemotron-70b-instruct is still listed and kept.
        # Main large chat models only, not the full list.
        # nemotron-3-super reasoning_effort defaults to high:
        # https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer
        chat_models=(
            "nvidia/nemotron-3-ultra-550b-a55b",
            "nvidia/nemotron-4-340b-instruct",
            "nvidia/llama-3.1-nemotron-ultra-253b-v1",
            "nvidia/nemotron-3-super-120b-a12b",
            "writer/palmyra-creative-122b",
            "meta/llama-3.2-90b-vision-instruct",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "mistralai/mistral-large-2-instruct",
            "moonshotai/kimi-k3",
            "openai/gpt-oss-20b",
            "google/gemma-4-31b-it",
            "z-ai/glm-5.3",
        ),
        vision_models=("meta/llama-3.2-90b-vision-instruct",),
        reasoning_models=(
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "openai/gpt-oss-20b",
        ),
    ),
    ProviderSpec(
        id="sambanova",
        name="SambaNova Cloud",
        base_url="https://api.sambanova.ai/v1",
        default_role="free",
        tier="freemium",
        # Keys: https://docs.sambanova.ai/docs/en/get-started/api-keys-urls
        register_url="https://cloud.sambanova.ai/apis",
        docs_url="https://docs.sambanova.ai/docs/en/models/sambacloud-models",
        health_path="/models",
        free_notes="Free tier with no payment method: 20 RPM, 20 RPD, 200K TPD",
        needed_by=("cortex", "airgpt"),
        # Live GET https://api.sambanova.ai/v1/models on 2026-10-01 (no key).
        # Same ids as https://docs.sambanova.ai/docs/en/models/sambacloud-models
        # Free tier table: https://docs.sambanova.ai/docs/en/models/rate-limits
        # DeepSeek-V3.1 and DeepSeek-V3.2 were listed and are omitted.
        # Meta-Llama-3.1-405B-Instruct left SambaCloud on 2025-06-25 and was
        # not in the live list.
        # gemma-4-31B-it accepts image input (models page).
        chat_models=(
            "gpt-oss-120b",
            "MiniMax-M2.7",
            "Meta-Llama-3.3-70B-Instruct",
            "MiniMax-M3",
            "gemma-4-31B-it",
        ),
        vision_models=("gemma-4-31B-it",),
        reasoning_models=("gpt-oss-120b",),
    ),
    ProviderSpec(
        id="sea_lion",
        name="AI Singapore SEA-LION",
        base_url="https://api.sea-lion.ai/v1",
        default_role="free",
        tier="freemium",
        register_url="https://playground.sea-lion.ai/key-manager",
        docs_url="https://docs.sea-lion.ai/guides/inferencing/api",
        health_path="/models",
        free_notes="Trial API key; 10 requests per minute (docs, 04 Jun 2026)",
        needed_by=("cortex", "airgpt"),
        # Chat ids from https://docs.sea-lion.ai/guides/inferencing/api
        # and https://docs.sea-lion.ai/guides/tool_calling
        # aisingapore/SEA-Guard classifies safe/unsafe and is not a chat hop.
        # aisingapore/SEA-LION-ModernBERT-Embedding-600M is POST /v1/embeddings.
        # Llama-SEA-LION-v3.5-70B-R defaults to thinking_mode on.
        chat_models=(
            "aisingapore/Llama-SEA-LION-v3.5-70B-R",
            "aisingapore/Qwen-SEA-LION-v4.5-27B-IT",
            "aisingapore/Gemma-SEA-LION-v4-27B-IT",
        ),
        reasoning_models=("aisingapore/Llama-SEA-LION-v3.5-70B-R",),
    ),
    ProviderSpec(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_role="cheap",
        tier="freemium",
        register_url="https://platform.deepseek.com/api_keys",
        docs_url="https://api-docs.deepseek.com/",
        health_path="/models",
        free_notes="Low-cost coding models; often used as cheap hop",
        needed_by=("cortex", "airgpt"),
        # Legacy deepseek-chat / deepseek-reasoner retired 2026-07-24.
        # Current ids: https://api-docs.deepseek.com/quick_start/pricing
        chat_models=(
            "deepseek-v4-pro",
            "deepseek-v4-flash",
        ),
        reasoning_models=(
            "deepseek-v4-pro",
            "deepseek-v4-flash",
        ),
    ),
    ProviderSpec(
        id="together",
        name="Together AI",
        base_url="https://api.together.xyz/v1",
        default_role="cheap",
        tier="freemium",
        # Project keys: https://docs.together.ai/docs/quickstart
        register_url="https://api.together.ai/settings/projects/~current/api-keys",
        docs_url="https://docs.together.ai/",
        health_path="/models",
        free_notes="Signup credits; OpenAI-compatible",
        needed_by=("cortex",),
        # Pinned from https://docs.together.ai/docs/inference-models (2026-09).
        # Prism-ML/Ternary-Bonsai-27B is listed Free; the rest are cheap hops
        # so model=auto actually spends a pooled Together key instead of skipping.
        chat_models=(
            "Prism-ML/Ternary-Bonsai-27B",
            "Qwen/Qwen3.5-9B",
            "openai/gpt-oss-120b",
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        ),
        vision_models=("Qwen/Qwen3.5-9B",),
        reasoning_models=("openai/gpt-oss-120b",),
    ),
    ProviderSpec(
        id="fireworks",
        name="Fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        default_role="cheap",
        tier="paid",
        # Dashboard keys: https://docs.fireworks.ai/getting-started/quickstart
        register_url="https://app.fireworks.ai/settings/users/api-keys",
        docs_url="https://docs.fireworks.ai/",
        health_path="/models",
        needed_by=("cortex",),
    ),
    ProviderSpec(
        id="cerebras",
        name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        default_role="free",
        tier="freemium",
        register_url="https://cloud.cerebras.ai/",
        docs_url="https://inference-docs.cerebras.ai/",
        health_path="/models",
        free_notes="Trial ($5 credits, 30 days, 5 RPM), not a free tier",
        needed_by=("airgpt",),
        # Models: https://inference-docs.cerebras.ai/models/overview
        # Trial ($5 / 30 days / 5 RPM, not a renewing free tier):
        # https://inference-docs.cerebras.ai/support/rate-limits
        # qwen-3.8-27b accepts images (rate-limit footnote: image limits).
        # llama-3.3-70b and llama3.1-8b are not in the shared catalog.
        chat_models=(
            "gpt-oss-120b",
            "qwen-3.8-27b",
        ),
        vision_models=("qwen-3.8-27b",),
        reasoning_models=("gpt-oss-120b",),
    ),
    ProviderSpec(
        id="huggingface",
        name="Hugging Face",
        # Confirmed for #60: Hub URL + /api/whoami-v2. Not an OpenAI-compat hop
        # (openai_compatible=False). Keyless hops are parked — do not invent
        # router.huggingface.co here.
        base_url="https://huggingface.co",
        default_role="backup",
        tier="freemium",
        register_url="https://huggingface.co/settings/tokens",
        docs_url="https://huggingface.co/docs/api-inference",
        health_path="/api/whoami-v2",
        openai_compatible=False,
        free_notes="HF token for gated models + inference",
        needed_by=("cortex", "openvault", "airgpt"),
    ),
    ProviderSpec(
        id=LOCAL_QWEN_ID,
        name="Local Qwen (loopback)",
        base_url=DEFAULT_LOCAL_BASE_URL,
        default_role="free",
        tier="local",
        register_url="https://github.com/ggml-org/llama.cpp",
        docs_url="https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
        health_path="/models",
        free_notes=(
            "No cloud key. OpenAI-compat llama.cpp or Ollama on loopback. "
            "Set OPENVAULT_LOCAL_BASE_URL + OPENVAULT_LOCAL_MODEL. "
            "OpenVault does not start the server or pull a model."
        ),
        needed_by=("cortex", "airgpt", "openvault"),
        placeholder_secret="local",
        chat_models=(DEFAULT_LOCAL_MODEL,),
        local_hop=True,
    ),
    ProviderSpec(
        id="ollama",
        name="Ollama (local)",
        base_url="http://127.0.0.1:11434/v1",
        default_role="free",
        tier="local",
        register_url="https://ollama.com/download",
        docs_url="https://docs.ollama.com/api/openai-compatibility",
        health_path="/models",
        free_notes="100% free local; api_key can be 'ollama'",
        needed_by=("cortex", "airgpt", "openvault"),
        placeholder_secret="ollama",
        # Tags commonly served by a stock Ollama install. Missing pull -> real
        # upstream 404, not a dishonest "no catalogued model" skip.
        chat_models=(
            "llama3.2",
            "qwen2.5",
            "llama3.1",
            "mistral",
            "llama3.2-vision",
        ),
        vision_models=("llama3.2-vision",),
    ),
    ProviderSpec(
        id="cortex",
        name="Netie Cortex",
        base_url="http://127.0.0.1:8010",
        default_role="primary",
        tier="local",
        register_url="https://github.com/Netie-AI/Cortex",
        docs_url="https://github.com/Netie-AI/Cortex/blob/main/docs/PLUG_AND_PLAY.md",
        health_path="/health",
        openai_compatible=False,
        free_notes="Local Netie Engine — BYOK via OpenVault",
        needed_by=("airgpt", "openvault"),
        placeholder_secret="cortex-local",
        # Align with OpenMW model_manager tier defaults / models.json ids.
        chat_models=(
            "qwen3.5-9b",
            "qwen2.5-14b",
            "phi-4-mini",
        ),
    ),
    ProviderSpec(
        id="litellm",
        name="LiteLLM Proxy",
        base_url="http://127.0.0.1:4000/v1",
        default_role="backup",
        tier="local",
        register_url="https://docs.litellm.ai/docs/",
        docs_url="https://docs.litellm.ai/docs/proxy/quick_start",
        health_path="/models",
        free_notes="Self-hosted OpenAI-compatible multi-provider proxy",
        needed_by=("cortex", "openvault"),
        placeholder_secret="sk-litellm",
        # Common LiteLLM proxy aliases; operator config may remap. auto needs
        # a concrete id so the hop is attempted rather than skipped.
        chat_models=(
            "gpt-4o-mini",
            "gpt-4o",
            "claude-3-5-sonnet",
        ),
        vision_models=(
            "gpt-4o-mini",
            "gpt-4o",
        ),
    ),
    ProviderSpec(
        id="github_models",
        name="GitHub Models",
        base_url="https://models.inference.ai.azure.com",
        default_role="free",
        tier="freemium",
        # marketplace/models is 404. Retirement notice:
        # https://docs.github.com/en/github-models
        register_url="https://docs.github.com/en/github-models",
        docs_url="https://docs.github.com/en/github-models",
        health_path="/models",
        free_notes="Free tier via GitHub token -- inference API retired 2026-07-30",
        needed_by=("airgpt",),
        # GitHub Models inference retired 30 Jul 2026
        # (https://docs.github.com/en/rest/models/inference). Empty chat_models
        # so model=auto skips rather than 404ing a dead hop.
    ),
    ProviderSpec(
        id="deepgram",
        name="Deepgram",
        base_url="https://api.deepgram.com/v1",
        default_role="primary",
        tier="paid",
        register_url="https://console.deepgram.com/",
        docs_url="https://developers.deepgram.com/",
        health_path="/projects",
        openai_compatible=False,
        free_notes="Speech-to-text (Nova-3 streaming). Not a chat provider - no chat_models.",
        needed_by=("openwillow",),
        status_page="https://status.deepgram.com/",
    ),
    ProviderSpec(
        id="siliconflow",
        name="SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        default_role="free",
        tier="freemium",
        # API keys page: https://docs.siliconflow.com/en/userguide/quickstart
        register_url="https://cloud.siliconflow.com/account/ak",
        docs_url="https://docs.siliconflow.cn/",
        health_path="/models",
        free_notes="OmniRoute lists as permanently-free pool (region dependent)",
        needed_by=("cortex",),
        # Pinned from SiliconFlow chat docs:
        # https://docs.siliconflow.cn/en/userguide/guides/fine-tune
        # https://docs.siliconflow.cn/en/userguide/capabilities/text-generation
        chat_models=(
            "Qwen/Qwen2.5-7B-Instruct",
            "Qwen/Qwen3.6-27B",
            "Qwen/Qwen3.5-9B",
        ),
        vision_models=("Qwen/Qwen3.5-9B",),
    ),
)


def _with_runtime_local(spec: ProviderSpec) -> ProviderSpec:
    """Apply operator env to the LOCAL-1 spec without mutating the catalog tuple."""
    model = (os.environ.get(LOCAL_MODEL_ENV) or "").strip() or spec.chat_models[0]
    base = (os.environ.get(LOCAL_BASE_URL_ENV) or "").strip() or spec.base_url
    return replace(spec, chat_models=(model,), base_url=base)


def get_provider(provider_id: str) -> ProviderSpec | None:
    for spec in PROVIDER_CATALOG:
        if spec.id == provider_id:
            return _with_runtime_local(spec) if spec.local_hop else spec
    return None


def list_catalog(
    *,
    free_only: bool = False,
    needed_by: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in PROVIDER_CATALOG:
        if free_only and spec.tier not in ("free", "freemium", "local"):
            continue
        if needed_by and needed_by not in spec.needed_by:
            continue
        live = _with_runtime_local(spec) if spec.local_hop else spec
        rows.append(live.to_dict())
    return rows


def spendable_for_freeroute() -> list[dict[str, Any]]:
    """Providers the pooled /v1 spend path can actually hop with model=auto.

    OmniRoute/9router list hundreds of names. We only list ids that are
    OpenAI-compatible *and* have a cited chat_models pool, so a vaulted key
    is not skipped as 'no catalogued model'.
    """
    return [
        (_with_runtime_local(spec) if spec.local_hop else spec).to_dict()
        for spec in PROVIDER_CATALOG
        if spec.openai_compatible and spec.chat_models
    ]


def essentials_for(*consumers: str) -> list[dict[str, Any]]:
    """Providers Cortex / AirGPT / OpenVault should have keys for."""
    wanted = set(consumers) if consumers else {"cortex", "airgpt", "openvault"}
    out: list[dict[str, Any]] = []
    for spec in PROVIDER_CATALOG:
        if wanted.intersection(spec.needed_by):
            out.append(spec.to_dict())
    return out


@dataclass
class DowntimeResult:
    provider_id: str
    online: bool
    latency_ms: float | None
    detail: str
    register_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def check_provider_downtime(
    spec: ProviderSpec,
    *,
    timeout_s: float = 8.0,
) -> DowntimeResult:
    """Probe provider availability (models/health) — OmniRoute-style uptime chip."""
    import time

    import httpx

    url = f"{spec.base_url.rstrip('/')}{spec.health_path}"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url)
        latency = (time.perf_counter() - started) * 1000.0
        # 401/403 still means the endpoint is up (auth required)
        online = resp.status_code < 500
        detail = f"HTTP {resp.status_code}"
        if not online:
            detail += " — upstream down or degraded"
        return DowntimeResult(spec.id, online, latency, detail, spec.register_url)
    except httpx.TimeoutException:
        return DowntimeResult(spec.id, False, None, "timeout", spec.register_url)
    except httpx.HTTPError as exc:
        return DowntimeResult(spec.id, False, None, str(exc), spec.register_url)


def catalog_coverage_report(vault_provider_ids: set[str] | frozenset[str]) -> dict[str, Any]:
    """What Cortex/AirGPT still need vs what the vault already has."""
    missing: dict[str, list[dict[str, str]]] = {
        "cortex": [],
        "airgpt": [],
        "openvault": [],
    }
    for consumer in missing:
        for spec in PROVIDER_CATALOG:
            if consumer not in spec.needed_by:
                continue
            if spec.id == LOCAL_QWEN_ID:
                # Listed on FreeRoute without a vault row. Not a coverage gap.
                continue
            if spec.id not in vault_provider_ids and spec.tier != "local":
                # local always "available" as installable; still list if not vaulted
                missing[consumer].append(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "register_url": spec.register_url,
                        "tier": spec.tier,
                    }
                )
            elif spec.id not in vault_provider_ids and spec.tier == "local":
                missing[consumer].append(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "register_url": spec.register_url,
                        "tier": spec.tier,
                    }
                )
    present = sorted(vault_provider_ids)
    free = [s.to_dict() for s in PROVIDER_CATALOG if s.tier in ("free", "freemium", "local")]
    return {
        "vault_providers": present,
        "missing_by_consumer": missing,
        "free_or_local_catalog": free,
        "catalog_size": len(PROVIDER_CATALOG),
        "omniroute_absorb_notes": (
            "FreeRoute (AirGPT product name) absorbs OmniRoute patterns: 4-tier fallback, "
            "free-tier surface, register links, downtime probes, circuit breaker. "
            "Not cloned: 250-provider matrix, compression engines, quota-share DRR. "
            "Custody + routing SoT stays OpenVault; AirGPT only enables the sidecar."
        ),
        "similar_routers": [
            {"id": "openrouter", "why": "hosted marketplace + free models"},
            {"id": "litellm", "why": "self-hosted OpenAI-compatible proxy"},
            {"id": "ollama", "why": "local free forever OpenAI-compatible"},
            {"id": "portkey", "why": "gateway + guardrails (pattern only)"},
            {
                "id": "omniroute",
                "why": "inspiration for FreeRoute auto-fallback + free-tier aggregation",
            },
            {
                "id": "9router",
                "why": "inspiration for free-provider register deep-links + auto-fallback bar",
            },
            {"id": "openfree", "why": "our gateway brand — enable in AirGPT, route via OpenVault"},
        ],
    }


# Keep for type checkers / seed helpers
ESSENTIAL_PROVIDER_IDS: frozenset[str] = frozenset(s.id for s in PROVIDER_CATALOG if s.needed_by)


# Aliases callers use when they do not care which model answers. The proxy used to
# forward these straight through, so an upstream saw a model literally named "auto".
_AUTO_ALIASES: frozenset[str] = frozenset({"", "auto", "default", "openvault/auto", "free", "any"})


def resolve_model(provider: str, requested: str | None, *, multimodal: bool = False) -> str | None:
    """Pick a model id `provider` will actually accept.

    Returns ``None`` when the provider cannot serve the request at all, so the caller
    skips the hop instead of sending something that 404s. Rules, in order:

    1. An id this provider really serves is honoured as-is.
    2. An alias ("auto", "", "default", ...) becomes the provider's first choice.
    3. A concrete id belonging to some *other* provider - `gpt-4o` arriving at Groq -
       is treated as "caller had a preference we cannot meet" and falls back to this
       provider's first choice rather than 404ing. Routing across heterogeneous
       providers is the entire point of the proxy.
    4. `multimodal=True` restricts to `vision_models`; a text-only provider returns
       None rather than quietly discarding the image.
    """
    spec = get_provider(provider)
    if spec is None:
        # Unknown provider: only a caller-supplied concrete id can work.
        want = (requested or "").strip()
        return None if want.lower() in _AUTO_ALIASES else want or None

    pool = spec.vision_models if multimodal else spec.chat_models
    if not pool:
        # Nothing catalogued for this modality yet. Remaining empty entries
        # (anthropic Messages API, speech-only, etc.) still need concrete caller
        # ids to stay usable; only an alias is unresolvable here, and guessing
        # is what sent "auto" upstream as a model name in the first place.
        want = (requested or "").strip()
        if want and want.lower() not in _AUTO_ALIASES:
            return want
        return None

    want = (requested or "").strip()
    if want and want.lower() not in _AUTO_ALIASES and want in pool:
        return want
    return pool[0]


def models_for(provider: str, *, multimodal: bool = False) -> tuple[str, ...]:
    """Every id this provider can serve, strongest first. Empty if unsupported."""
    spec = get_provider(provider)
    if spec is None:
        return ()
    return spec.vision_models if multimodal else spec.chat_models


def catalog_contains_model(model: str, *, multimodal: bool = False) -> bool:
    """True when ``model`` is an exact id in some provider catalog pool.

    Aliases (``auto``, empty, ``default``) are not catalog ids. A different
    spelling is not a match: ``gpt-oss-120b`` is not ``openai/gpt-oss-120b``.
    """
    want = (model or "").strip()
    if not want or want.lower() in _AUTO_ALIASES:
        return False
    for spec in PROVIDER_CATALOG:
        live = _with_runtime_local(spec) if spec.local_hop else spec
        pool = live.vision_models if multimodal else live.chat_models
        if want in pool:
            return True
    return False


# A reasoning model emits its chain of thought from the same completion budget as the
# answer, so a small max_tokens is consumed entirely before any content is written.
# Measured on gpt-oss-120b: 30 reasoning tokens at max_tokens=32 -> content "",
# finish_reason "length". 159 reasoning tokens at 512 -> a normal answer.
MIN_REASONING_BUDGET = 512


def is_reasoning_model(provider: str, model: str) -> bool:
    spec = get_provider(provider)
    return bool(spec and model in spec.reasoning_models)


def budget_for(provider: str, model: str, requested: int | None) -> int | None:
    """Raise a too-small completion budget to the floor a reasoning model needs.

    Returns the budget to send, or None to leave the caller's body untouched. Never
    lowers a budget - the caller may have a cost reason for a large one.
    """
    if requested is None or not is_reasoning_model(provider, model):
        return None
    if requested >= MIN_REASONING_BUDGET:
        return None
    return MIN_REASONING_BUDGET

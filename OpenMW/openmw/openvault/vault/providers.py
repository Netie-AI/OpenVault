"""Provider catalog absorbed from OmniRoute / OpenRouter / LiteLLM / Ollama patterns.

Not a 250-provider clone — a curated, honest set with free tiers, register links,
downtime probes, and Cortex/AirGPT essential coverage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["needed_by"] = list(self.needed_by)
        d["chat_models"] = list(self.chat_models)
        d["vision_models"] = list(self.vision_models)
        d["reasoning_models"] = list(self.reasoning_models)
        return d


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
        free_notes="20+ free models via :free suffix; single key marketplace",
        needed_by=("cortex", "airgpt", "openvault"),
        status_page="https://status.openrouter.ai/",
        # Verified against https://openrouter.ai/api/v1/models. The previously pinned
        # google/gemini-2.0-flash-001 had been retired and answered 404, which read as
        # "OpenRouter is down" for weeks.
        chat_models=(
            "google/gemini-2.5-flash",
            "meta-llama/llama-3.3-70b-instruct",
        ),
        vision_models=("google/gemini-2.5-flash",),
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
        # Verified against https://api.groq.com/openai/v1/models. Limits are per
        # model per day (1K RPD, 8K TPM each), so listing several multiplies the
        # usable budget instead of dying on the first 429.
        chat_models=(
            "openai/gpt-oss-120b",
            "qwen/qwen3.6-27b",
            "openai/gpt-oss-20b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ),
        vision_models=("qwen/qwen3.6-27b",),
        reasoning_models=(
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.6-27b",
        ),
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
        chat_models=(
            "mistral-small-latest",
            "ministral-8b-latest",
            "open-mistral-nemo",
        ),
        vision_models=("mistral-small-latest",),
    ),
    ProviderSpec(
        id="nvidia",
        name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        default_role="cheap",
        tier="freemium",
        register_url="https://build.nvidia.com/",
        docs_url="https://docs.api.nvidia.com/",
        health_path="/models",
        free_notes="build.nvidia.com / NIM OpenAI-compatible; keys typically nvapi-…",
        needed_by=("airgpt", "cortex"),
        chat_models=(
            "meta/llama-3.1-8b-instruct",
            "meta/llama-3.1-70b-instruct",
            "mistralai/mistral-nemotron",
            "nvidia/llama-3.1-nemotron-70b-instruct",
        ),
        reasoning_models=(
            "nvidia/llama-3.1-nemotron-70b-instruct",
        ),
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
        register_url="https://api.together.xyz/settings/api-keys",
        docs_url="https://docs.together.ai/",
        health_path="/models",
        free_notes="Signup credits; OpenAI-compatible",
        needed_by=("cortex",),
    ),
    ProviderSpec(
        id="fireworks",
        name="Fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
        default_role="cheap",
        tier="paid",
        register_url="https://fireworks.ai/account/api-keys",
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
        free_notes="High-speed free tier for Llama/Qwen",
        needed_by=("airgpt",),
        # From D:\Netie\Free APIs for OpenVault Free\Free API.txt (2026-08).
        chat_models=(
            "gpt-oss-120b",
            "llama-3.3-70b",
            "llama3.1-8b",
        ),
        reasoning_models=("gpt-oss-120b",),
    ),
    ProviderSpec(
        id="huggingface",
        name="Hugging Face",
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
        register_url="https://github.com/marketplace/models",
        docs_url="https://docs.github.com/en/github-models",
        health_path="/models",
        free_notes="Free tier via GitHub token for many models",
        needed_by=("airgpt",),
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
        register_url="https://cloud.siliconflow.cn/account/ak",
        docs_url="https://docs.siliconflow.cn/",
        health_path="/models",
        free_notes="OmniRoute lists as permanently-free pool (region dependent)",
        needed_by=("cortex",),
    ),
)


def get_provider(provider_id: str) -> ProviderSpec | None:
    for spec in PROVIDER_CATALOG:
        if spec.id == provider_id:
            return spec
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
        rows.append(spec.to_dict())
    return rows


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
            {"id": "omniroute", "why": "inspiration for FreeRoute auto-fallback + free-tier aggregation"},
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

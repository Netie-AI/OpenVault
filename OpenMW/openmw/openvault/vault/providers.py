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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["needed_by"] = list(self.needed_by)
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
    ),
    ProviderSpec(
        id="google",
        name="Google AI Studio",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        default_role="free",
        tier="freemium",
        register_url="https://aistudio.google.com/apikey",
        docs_url="https://ai.google.dev/gemini-api/docs",
        health_path="/models",
        free_notes="Gemini free tier via AI Studio",
        needed_by=("cortex", "airgpt"),
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
    ),
    ProviderSpec(
        id="cortex",
        name="Netie Cortex",
        base_url="http://127.0.0.1:8000",
        default_role="primary",
        tier="local",
        register_url="https://github.com/Netie-AI/Cortex",
        docs_url="https://github.com/Netie-AI/Cortex/blob/main/docs/PLUG_AND_PLAY.md",
        health_path="/health",
        openai_compatible=False,
        free_notes="Local Netie Engine — BYOK via OpenVault",
        needed_by=("airgpt", "openvault"),
        placeholder_secret="cortex-local",
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
            "OpenFree (AirGPT product name) absorbs OmniRoute patterns: 4-tier fallback, "
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
                "why": "inspiration for OpenFree auto-fallback + free-tier aggregation",
            },
            {"id": "openfree", "why": "our gateway brand — enable in AirGPT, route via OpenVault"},
        ],
    }


# Keep for type checkers / seed helpers
ESSENTIAL_PROVIDER_IDS: frozenset[str] = frozenset(s.id for s in PROVIDER_CATALOG if s.needed_by)

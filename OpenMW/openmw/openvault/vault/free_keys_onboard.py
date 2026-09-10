"""FreeRoute Get-free-keys onboard checklist (#60).

Canonical Groq-first table. Not a second vault, not GitHub Models, not the
Marketing /rates page. Cloudflare Workers AI is stored as ``provider=custom``
with a composed account-id base_url — there is no extra ProviderKind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openmw.openvault.vault.providers import get_provider
from openmw.openvault.vault.store import KeyRecord, ProviderKind

GITHUB_MODELS_ID = "github_models"
RETIRED_ONBOARD_IDS: frozenset[str] = frozenset({GITHUB_MODELS_ID})

CF_WORKERS_AI_BASE_TEMPLATE = "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1"
CF_TOKEN_ENV_KEYS: frozenset[str] = frozenset({"CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"})
CF_ACCOUNT_ID_ENV = "CLOUDFLARE_ACCOUNT_ID"
CF_MODELS_405_WARN = (
    "HTTP 405 GET /models — probe mismatch, not a dead key; "
    "Cloudflare Workers AI /ai/run can still work"
)

_ACCOUNT_ID_RE = re.compile(r"^[A-Fa-f0-9]{32}$")
_PASSWORD_ENV_RE = re.compile(r"(?:^|_)(PASSWORD|PASSWD)$")
_SITE_ENV_RE = re.compile(r"^SITE_")
_PASSWORD_LABEL_RE = re.compile(r"(PASSWORD|PASSWD)|^SITE_", re.I)


def _catalog_base(provider_id: str, fallback: str) -> str:
    spec = get_provider(provider_id)
    return spec.base_url if spec is not None else fallback


@dataclass(frozen=True)
class FreeKeyOnboard:
    """One checklist row. ``add_key_provider`` is what POST /api/keys receives."""

    id: str
    label: str
    register_url: str
    default_base_url: str
    add_key_provider: ProviderKind
    notes: str = ""
    required_first: bool = False
    needs_account_id: bool = False

    def to_dict(self) -> dict[str, Any]:
        spec = get_provider(self.id)
        chat_models = list(spec.chat_models) if spec is not None else []
        openai_compatible = spec.openai_compatible if spec is not None else True
        return {
            "id": self.id,
            "label": self.label,
            "register_url": self.register_url,
            "default_base_url": self.default_base_url,
            "add_key_provider": self.add_key_provider,
            "role": "free",
            "custody": "pooled",
            "notes": self.notes,
            "required_first": self.required_first,
            "needs_account_id": self.needs_account_id,
            "github_models": False,
            "deep_link": f"/tool/register?provider={self.id}",
            "return_path": f"/keys?provider={self.id}#free",
            "tier": spec.tier if spec is not None else "freemium",
            "docs_url": spec.docs_url if spec is not None else self.register_url,
            "spendable": bool(spec is not None and spec.openai_compatible and spec.chat_models),
            "chat_models": chat_models,
            "openai_compatible": openai_compatible,
        }


FREE_KEYS_ONBOARD: tuple[FreeKeyOnboard, ...] = (
    FreeKeyOnboard(
        id="groq",
        label="Groq",
        register_url="https://console.groq.com/keys",
        default_base_url=_catalog_base("groq", "https://api.groq.com/openai/v1"),
        add_key_provider="groq",
        notes="Required first. Fast free-tier hop.",
        required_first=True,
    ),
    FreeKeyOnboard(
        id="google",
        label="Google AI Studio",
        register_url="https://aistudio.google.com/apikey",
        default_base_url=_catalog_base(
            "google", "https://generativelanguage.googleapis.com/v1beta/openai"
        ),
        add_key_provider="google",
        notes="Prefer AQ. auth keys (AI Studio). AIza… still works.",
    ),
    FreeKeyOnboard(
        id="openrouter",
        label="OpenRouter free",
        register_url="https://openrouter.ai/keys",
        default_base_url=_catalog_base("openrouter", "https://openrouter.ai/api/v1"),
        add_key_provider="openrouter",
        notes="Free models at $0 via :free suffix.",
    ),
    FreeKeyOnboard(
        id="cerebras",
        label="Cerebras",
        register_url="https://cloud.cerebras.ai",
        default_base_url=_catalog_base("cerebras", "https://api.cerebras.ai/v1"),
        add_key_provider="cerebras",
        notes="Official trial may need a card.",
    ),
    FreeKeyOnboard(
        id="mistral",
        label="Mistral",
        register_url="https://console.mistral.ai/api-keys",
        default_base_url=_catalog_base("mistral", "https://api.mistral.ai/v1"),
        add_key_provider="mistral",
        notes="Phone/billing common on signup.",
    ),
    FreeKeyOnboard(
        id="huggingface",
        label="Hugging Face",
        register_url="https://huggingface.co/settings/tokens",
        default_base_url=_catalog_base("huggingface", "https://huggingface.co"),
        add_key_provider="huggingface",
        notes=(
            "Catalog base_url is the Hub URL (whoami-v2). Not listed as "
            "OpenAI-compat; keyless hops are parked."
        ),
    ),
    FreeKeyOnboard(
        id="cloudflare",
        label="Cloudflare Workers AI",
        register_url="https://developers.cloudflare.com/workers-ai/get-started/rest-api/",
        default_base_url=CF_WORKERS_AI_BASE_TEMPLATE,
        add_key_provider="custom",
        notes=(
            "Account ID is not a secret. Compose base_url with the API token. "
            "GET /models 405 is a probe mismatch, not a dead key."
        ),
        needs_account_id=True,
    ),
)


def onboard_item(provider_id: str) -> FreeKeyOnboard | None:
    want = provider_id.strip()
    for item in FREE_KEYS_ONBOARD:
        if item.id == want:
            return item
    return None


def onboard_install_defaults(provider: str, base_url: str = "") -> tuple[str, str]:
    """role + custody for Free Keys paste/ingest. Keyless hops stay parked."""
    if is_cloudflare_workers_ai_base(base_url):
        return ("free", "pooled")
    item = onboard_item(provider)
    if item is not None:
        return ("free", "pooled")
    return ("", "pooled")


def compose_cloudflare_workers_ai_base(account_id: str) -> str:
    """Account ID is non-secret. Refuse tokens, URLs, and empty values."""
    aid = account_id.strip()
    if not _ACCOUNT_ID_RE.fullmatch(aid):
        raise ValueError(
            "Cloudflare Account ID must be the dashboard account id (not an API token)"
        )
    return CF_WORKERS_AI_BASE_TEMPLATE.format(ACCOUNT_ID=aid)


def is_cloudflare_workers_ai_base(base_url: str) -> bool:
    url = (base_url or "").strip().lower().rstrip("/")
    return "api.cloudflare.com/client/v4/accounts/" in url and "/ai" in url


def catalog_base_url(provider: str) -> str:
    spec = get_provider(provider)
    if spec is not None and spec.base_url:
        return spec.base_url
    item = onboard_item(provider)
    if item is not None and not item.needs_account_id:
        return item.default_base_url
    return ""


def is_password_env_key(env_key: str) -> bool:
    """SITE_* and *_PASSWORD are site logins — /api/secrets, never /api/keys."""
    key = env_key.strip().upper()
    if not key:
        return False
    if key in CF_TOKEN_ENV_KEYS or key == CF_ACCOUNT_ID_ENV:
        return False
    if _SITE_ENV_RE.match(key):
        return True
    return bool(_PASSWORD_ENV_RE.search(key))


def looks_like_site_password_key(*, label: str, provider: str, base_url: str) -> bool:
    """Empty-base_url custom rows named like site passwords must not hit /api/keys."""
    if (base_url or "").strip():
        return False
    if provider != "custom":
        return False
    return bool(_PASSWORD_LABEL_RE.search((label or "").strip()))


def key_matches_onboard(record: KeyRecord, item: FreeKeyOnboard) -> bool:
    if record.lifecycle != "active" or not record.enabled:
        return False
    if item.needs_account_id or item.add_key_provider == "custom":
        return is_cloudflare_workers_ai_base(record.base_url)
    return record.provider == item.add_key_provider


def sort_groq_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {item.id: i for i, item in enumerate(FREE_KEYS_ONBOARD)}

    def _key(row: dict[str, Any]) -> tuple[int, int, str]:
        pid = str(row.get("id") or row.get("provider") or "")
        if pid in order:
            return (0, order[pid], pid)
        return (1, 50, pid)

    return sorted(rows, key=_key)


def onboard_payload(keys: list[KeyRecord] | None = None) -> dict[str, Any]:
    stored = list(keys or [])
    providers: list[dict[str, Any]] = []
    for item in FREE_KEYS_ONBOARD:
        row = item.to_dict()
        match = next((k for k in stored if key_matches_onboard(k, item)), None)
        row["installed"] = match is not None
        row["installed_key_id"] = match.id if match is not None else None
        row["installed_base_url"] = match.base_url if match is not None else ""
        providers.append(row)
    return {
        "ok": True,
        "surface": "freeroute-onboard",
        "github_models": "retired",
        "order": [item.id for item in FREE_KEYS_ONBOARD],
        "providers": providers,
        "count": len(providers),
        "return_path": "/keys#free",
        "help": (
            "Get free keys: Groq first, then Google AI Studio, OpenRouter, "
            "Cerebras, Mistral, Hugging Face, Cloudflare Workers AI. "
            "Paste-to-save via POST /api/keys (role=free, custody=pooled). "
            "Site passwords are not this wizard — they stay on /api/secrets*. "
            "Prefer openvault app. Not the public /rates page."
        ),
        "custody": "pooled",
        "keyless": "parked",
    }

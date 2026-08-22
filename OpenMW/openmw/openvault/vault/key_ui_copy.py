"""Product-locked key UI copy. Keep in lockstep with apps/web/src/keys/copy.ts."""

from __future__ import annotations

from typing import Final

CORTEX_KEY_LABEL: Final[str] = "Cortex API key"
POWERED_BY: Final[str] = "Powered by top-tier AI"
SAFETY_DISCLOSURE: Final[str] = (
    "Safety: Cortex uses this key. OpenVault keeps it in one vault. "
    "Do not share it. Ship and leave-machine still go through the gate."
)

SUBSCRIBE_TITLE: Final[str] = "Your Cortex API key"
SUBSCRIBE_LEAD: Final[str] = (
    "Get a Cortex API key. Use it with Cortex. OpenVault stores it -- one vault, one key."
)
SUBSCRIBE_BUTTON: Final[str] = "Get Cortex API key"
SUBSCRIBE_ISSUED_HINT: Final[str] = (
    "Copy this Cortex API key now. OpenVault will not show the full value again."
)
SUBSCRIBE_EMPTY_HINT: Final[str] = "No Cortex API key yet. Tap the button to issue one."
SUBSCRIBE_DISCLOSURE: Final[str] = f"{SAFETY_DISCLOSURE} {POWERED_BY}"

FORBIDDEN_SUBSCRIBE_TERMS: Final[tuple[str, ...]] = (
    "DeepSeek",
    "China",
    "Groq",
    "Gemini",
    "OpenRouter",
    "OpenAI",
)

BYOK_TITLE: Final[str] = "Bring your own key"
BYOK_LEAD: Final[str] = (
    "Paste a key you already have. We show the provider name you brought -- "
    "honest labels, not a guessed brand."
)

FREE_TITLE: Final[str] = "Free keys"
FREE_LEAD: Final[str] = "Two steps. Register, then install."
FREE_STEPS: Final[tuple[tuple[str, str], ...]] = (
    ("Register", "Create a free account and copy the key it shows you."),
    ("Install", "Paste that key here. OpenVault encrypts it in the vault."),
)


def subscribe_surface(*extra: str) -> str:
    """All subscribe-facing strings concatenated for lock tests."""
    parts = [
        SUBSCRIBE_TITLE,
        SUBSCRIBE_LEAD,
        SUBSCRIBE_BUTTON,
        CORTEX_KEY_LABEL,
        SUBSCRIBE_ISSUED_HINT,
        SUBSCRIBE_EMPTY_HINT,
        SUBSCRIBE_DISCLOSURE,
        SAFETY_DISCLOSURE,
        POWERED_BY,
        *extra,
    ]
    return "\n".join(parts)

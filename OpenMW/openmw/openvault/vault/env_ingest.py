"""Auto-ingest provider secrets from the process environment into the vault.

OpenVault is the key source of truth (PRODUCT_ROLES.md); the environment is an
*import source* only. This module finds credential-shaped env vars, skips
placeholders, and hands them to :func:`upsert_env_secret` so they land
encrypted in the vault.

Two safety rules hold throughout:

* ``dry_run`` defaults to True — scanning never writes until asked, matching
  the control tier's dry-run-by-default posture.
* Raw secrets are never echoed back. Every reported value is masked.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openmw.openvault.vault.airgpt_keyvault import (
    ENV_KEY_TO_PROVIDER,
    PROVIDER_TO_ENV,
    upsert_env_secret,
)
from openmw.openvault.vault.crypto import mask_secret
from openmw.openvault.vault.store import KeyVault

# Configuration, not credentials — never ingest as a secret.
NON_SECRET_ENV_KEYS = frozenset({"OPENVAULT_URL"})

# Shortest plausible credential; anything shorter reads as a placeholder.
MIN_SECRET_LEN = 8

_PLACEHOLDERS = frozenset(
    {
        "changeme",
        "change-me",
        "placeholder",
        "todo",
        "none",
        "null",
        "unset",
        "your-key",
        "your-key-here",
        "your_api_key",
    }
)

_GENERIC_ENV_RE = re.compile(r"^[A-Z0-9_]+_(API_KEY|TOKEN|SECRET|KEY)$")


def known_env_keys() -> set[str]:
    """Env var names the provider catalog already understands."""
    return set(ENV_KEY_TO_PROVIDER) | set(PROVIDER_TO_ENV.values())


def _is_placeholder(value: str) -> bool:
    candidate = value.strip()
    if len(candidate) < MIN_SECRET_LEN:
        return True
    lowered = candidate.lower()
    if lowered in _PLACEHOLDERS:
        return True
    if lowered.startswith(("your", "<", "${", "changeme", "example")):
        return True
    # Already-masked values must never round-trip back into the vault.
    return "…" in candidate or candidate.startswith("••")


@dataclass
class EnvCandidate:
    """A credential-shaped environment variable worth importing."""

    env_key: str  # normalized, upper-case
    source_name: str  # exact key as found in the environment
    provider: str
    known: bool
    masked: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_key": self.env_key,
            "provider": self.provider,
            "known": self.known,
            "masked": self.masked,
        }


def scan_environment(
    env: Mapping[str, str] | None = None,
    *,
    include_unknown: bool = False,
) -> list[EnvCandidate]:
    """Find importable secrets. Never returns raw values."""
    source: Mapping[str, str] = os.environ if env is None else env
    known = known_env_keys()
    found: list[EnvCandidate] = []
    for name in sorted(source):
        env_key = name.strip().upper()
        if env_key in NON_SECRET_ENV_KEYS:
            continue
        is_known = env_key in known
        if not is_known and not (include_unknown and _GENERIC_ENV_RE.match(env_key)):
            continue
        value = (source[name] or "").strip()
        if _is_placeholder(value):
            continue
        found.append(
            EnvCandidate(
                env_key=env_key,
                source_name=name,
                provider=ENV_KEY_TO_PROVIDER.get(env_key, "custom"),
                known=is_known,
                masked=mask_secret(value),
            )
        )
    return found


def ingest_environment(
    vault: KeyVault,
    *,
    env: Mapping[str, str] | None = None,
    dry_run: bool = True,
    include_unknown: bool = False,
) -> dict[str, Any]:
    """Scan the environment and (unless ``dry_run``) vault what it finds."""
    source: Mapping[str, str] = os.environ if env is None else env
    candidates = scan_environment(source, include_unknown=include_unknown)
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        row: dict[str, Any] = candidate.to_dict()
        if dry_run:
            row["ok"] = True
            row["action"] = "would_import"
            results.append(row)
            continue
        outcome = upsert_env_secret(
            vault,
            env_key=candidate.env_key,
            secret=source[candidate.source_name],
            label=candidate.env_key,
        )
        row["ok"] = bool(outcome.get("ok"))
        row["action"] = str(outcome.get("action") or "failed")
        if not row["ok"]:
            row["error"] = str(outcome.get("error") or "unknown")
        stored = outcome.get("key")
        if isinstance(stored, dict):
            row["key_id"] = stored.get("id")
            row["masked"] = stored.get("masked_secret") or candidate.masked
        results.append(row)
    imported = sum(1 for r in results if r["ok"] and r["action"] in ("created", "updated"))
    return {
        "ok": True,
        "dry_run": dry_run,
        "scanned": len(candidates),
        "imported": imported,
        "results": results,
        "policy": (
            "OpenVault is the key source of truth; the environment is an import "
            "source only. Secrets are stored encrypted and never echoed back."
        ),
    }

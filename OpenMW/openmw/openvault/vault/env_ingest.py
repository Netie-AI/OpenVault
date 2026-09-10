"""Auto-ingest provider secrets from the process environment into the vault.

OpenVault is the key source of truth (PRODUCT_ROLES.md); the environment is an
*import source* only. This module finds credential-shaped env vars, skips
placeholders, and hands them to :func:`upsert_env_secret` so they land
encrypted in the vault.

Two safety rules hold throughout:

* ``dry_run`` defaults to True — scanning never writes until asked, matching
  the control tier's dry-run-by-default posture.
* Raw secrets are never echoed back. Every reported value is masked.

Passwords and SITE_* logins are not API keys. They go to ``/api/secrets*``
(or are skipped with an honest pointer) so they cannot land as empty-base_url
custom keys.
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
from openmw.openvault.vault.free_keys_onboard import (
    CF_ACCOUNT_ID_ENV,
    CF_TOKEN_ENV_KEYS,
    compose_cloudflare_workers_ai_base,
    is_password_env_key,
)
from openmw.openvault.vault.secrets import SecretStore, SecretValidationError, mask_password
from openmw.openvault.vault.store import KeyVault

# Configuration, not credentials — never ingest as a secret.
NON_SECRET_ENV_KEYS = frozenset({"OPENVAULT_URL", CF_ACCOUNT_ID_ENV})

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
    return set(ENV_KEY_TO_PROVIDER) | set(PROVIDER_TO_ENV.values()) | set(CF_TOKEN_ENV_KEYS)


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


def parse_env_text(text: str) -> dict[str, str]:
    """Parse a pasted ``.env`` / multi-key block. Never returns secrets to the caller."""
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_key = key.strip().strip("\ufeff")
        if not env_key:
            continue
        token = value.strip()
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
            token = token[1:-1]
        parsed[env_key] = token
    return parsed


def resolve_ingest_source(
    env: Mapping[str, str] | None = None,
    env_text: str = "",
) -> dict[str, str]:
    """Pasted ``.env`` replaces process env so a wizard import is not a surprise mix."""
    if env_text.strip():
        source = parse_env_text(env_text)
        if CF_ACCOUNT_ID_ENV not in source:
            from_os = os.environ.get(CF_ACCOUNT_ID_ENV, "").strip()
            if from_os:
                source[CF_ACCOUNT_ID_ENV] = from_os
        return source
    if env is not None:
        return dict(env)
    return dict(os.environ)


@dataclass
class EnvCandidate:
    """A credential-shaped environment variable worth importing."""

    env_key: str  # normalized, upper-case
    source_name: str  # exact key as found in the environment
    provider: str
    known: bool
    masked: str
    store: str = "keys"  # keys | secrets

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_key": self.env_key,
            "provider": self.provider,
            "known": self.known,
            "masked": self.masked,
            "store": self.store,
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
        password = is_password_env_key(env_key)
        is_known = env_key in known
        generic = bool(include_unknown and _GENERIC_ENV_RE.match(env_key))
        if not is_known and not password and not generic:
            continue
        value = (source[name] or "").strip()
        if _is_placeholder(value):
            continue
        if password:
            found.append(
                EnvCandidate(
                    env_key=env_key,
                    source_name=name,
                    provider="custom",
                    known=False,
                    masked=mask_password(value),
                    store="secrets",
                )
            )
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


def _cloudflare_base_url(source: Mapping[str, str]) -> str:
    account_id = (source.get(CF_ACCOUNT_ID_ENV) or "").strip()
    if not account_id:
        return ""
    try:
        return compose_cloudflare_workers_ai_base(account_id)
    except ValueError:
        return ""


def ingest_environment(
    vault: KeyVault,
    *,
    env: Mapping[str, str] | None = None,
    env_text: str = "",
    dry_run: bool = True,
    include_unknown: bool = False,
    secrets: SecretStore | None = None,
) -> dict[str, Any]:
    """Scan the environment and (unless ``dry_run``) vault what it finds."""
    source = resolve_ingest_source(env, env_text)
    candidates = scan_environment(source, include_unknown=include_unknown)
    results: list[dict[str, Any]] = []
    cf_base = _cloudflare_base_url(source)
    for candidate in candidates:
        row: dict[str, Any] = candidate.to_dict()
        if candidate.store == "secrets":
            if dry_run:
                row["ok"] = True
                row["action"] = "would_import_password"
                results.append(row)
                continue
            if secrets is None:
                row["ok"] = False
                row["action"] = "skipped_password"
                row["error"] = "site passwords go to POST /api/secrets/passwords, not /api/keys"
                results.append(row)
                continue
            try:
                record = secrets.create_password(
                    label=candidate.env_key,
                    password=source[candidate.source_name],
                )
            except SecretValidationError as exc:
                row["ok"] = False
                row["action"] = "failed"
                row["error"] = str(exc)
                results.append(row)
                continue
            row["ok"] = True
            row["action"] = "created_password"
            row["secret_id"] = record.id
            row["masked"] = record.masked
            results.append(row)
            continue

        extra_base = ""
        if candidate.env_key in CF_TOKEN_ENV_KEYS:
            if not cf_base:
                if dry_run:
                    row["ok"] = True
                    row["action"] = "would_import"
                    row["needs_account_id"] = True
                    results.append(row)
                    continue
                row["ok"] = False
                row["action"] = "needs_account_id"
                row["error"] = (
                    "Cloudflare Workers AI needs CLOUDFLARE_ACCOUNT_ID to compose base_url"
                )
                results.append(row)
                continue
            extra_base = cf_base
            row["needs_account_id"] = False

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
            base_url=extra_base,
            provider_hint="custom" if candidate.env_key in CF_TOKEN_ENV_KEYS else "",
        )
        row["ok"] = bool(outcome.get("ok"))
        row["action"] = str(outcome.get("action") or "failed")
        if not row["ok"]:
            row["error"] = str(outcome.get("error") or "unknown")
        stored = outcome.get("key")
        if isinstance(stored, dict):
            row["key_id"] = stored.get("id")
            row["masked"] = stored.get("masked_secret") or candidate.masked
            row["base_url"] = stored.get("base_url") or extra_base
            row["provider"] = stored.get("provider") or candidate.provider
        results.append(row)
    imported = sum(1 for r in results if r["ok"] and r["action"] in ("created", "updated"))
    passwords_imported = sum(1 for r in results if r["ok"] and r["action"] == "created_password")
    return {
        "ok": True,
        "dry_run": dry_run,
        "scanned": len(candidates),
        "imported": imported,
        "passwords_imported": passwords_imported,
        "results": results,
        "policy": (
            "OpenVault is the key source of truth; the environment is an import "
            "source only. API keys go to /api/keys. Site passwords go to "
            "/api/secrets/passwords. Secrets are stored encrypted and never echoed back."
        ),
    }

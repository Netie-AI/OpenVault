"""Secrets-at-ship inject — OpenVault resolves vaulted secrets into deploy env.

PRODUCT_ROLES lock 1+3: custody + leave-machine gate stay here; Cortex is never
a second secrets SoT. Plaintext leaves the vault only into the host/deploy env
request — never into logs, step details, adapter JSON, or API summaries.

Env quoting lessons stolen from vendor/openship systemd-env tests (reimplemented;
do not import their runtime). FreeBuild HTTP ``envVars`` takes raw strings; the
systemd escaper is for SSH/unit hosts that write ``Environment=`` lines.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from openmw.openvault.vault.crypto import Seal, VaultSealedError
from openmw.openvault.vault.secrets import SecretStore
from openmw.openvault.vault.store import KeyRecord, KeyVault

EnvSource = Literal["key", "secret"]

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InjectError(ValueError):
    """Concrete blocker when selected secrets cannot be injected."""

    def __init__(self, reason: str, *, status_code: int = 400) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True)
class ShipEnvRef:
    """One env binding selected for a ship execute."""

    env_name: str
    key_id: str | None = None
    secret_id: str | None = None


@dataclass
class InjectResult:
    """Resolved env for the deploy call. ``values`` must not be serialized."""

    env: dict[str, str]
    names: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """API-/log-safe view — names and source ids only, never values."""
        return {
            "count": len(self.names),
            "names": list(self.names),
            "sources": [dict(s) for s in self.sources],
        }


def escape_systemd_env_value(value: str) -> str:
    """Escape a value for systemd ``Environment="KEY=…"`` (openship pattern).

    Order matters: backslashes first, then quotes, then percent, then C0.
    """
    out = value.replace("\\", "\\\\")
    out = out.replace('"', '\\"')
    out = out.replace("%", "%%")
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return out


def systemd_environment_line(name: str, value: str) -> str:
    """Render one ``Environment=`` assignment the way openship buildUnitFile does."""
    if not _ENV_NAME_RE.match(name):
        raise InjectError(f"invalid env name for systemd line: {name!r}")
    return f'Environment="{name}={escape_systemd_env_value(value)}"'


def redact_plaintext(text: str, values: Sequence[str]) -> str:
    """Omit known secret values from a string (logs / step detail / API dump)."""
    out = text
    for value in values:
        if value and value in out:
            out = out.replace(value, "[redacted]")
    return out


def scrub_mapping(obj: Any, values: Sequence[str]) -> Any:
    """Deep-copy JSON-ish structures with secret values redacted."""
    if isinstance(obj, str):
        return redact_plaintext(obj, values)
    if isinstance(obj, Mapping):
        # Drop envVars entirely — values belong only on the wire to the host.
        return {
            k: ("[omitted]" if k in ("envVars", "env_vars") else scrub_mapping(v, values))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [scrub_mapping(v, values) for v in obj]
    return obj


def _normalize_refs(refs: Sequence[ShipEnvRef | Mapping[str, Any]]) -> list[ShipEnvRef]:
    out: list[ShipEnvRef] = []
    seen: set[str] = set()
    for raw in refs:
        if isinstance(raw, ShipEnvRef):
            ref = raw
        else:
            ref = ShipEnvRef(
                env_name=str(raw.get("env_name", "")).strip(),
                key_id=(str(raw["key_id"]).strip() or None) if raw.get("key_id") else None,
                secret_id=(str(raw["secret_id"]).strip() or None) if raw.get("secret_id") else None,
            )
        name = ref.env_name.strip()
        if not name:
            raise InjectError("ship secret binding missing env_name")
        if not _ENV_NAME_RE.match(name):
            raise InjectError(f"invalid env_name {name!r}; use [A-Za-z_][A-Za-z0-9_]*")
        if name in seen:
            raise InjectError(f"duplicate env_name {name!r} in ship secrets")
        seen.add(name)
        has_key = bool(ref.key_id)
        has_secret = bool(ref.secret_id)
        if has_key == has_secret:
            raise InjectError(f"{name}: provide exactly one of key_id or secret_id")
        out.append(ShipEnvRef(env_name=name, key_id=ref.key_id, secret_id=ref.secret_id))
    return out


def resolve_ship_env(
    refs: Sequence[ShipEnvRef | Mapping[str, Any]],
    *,
    vault: KeyVault,
    secrets: SecretStore | None = None,
    seal: Seal | None = None,
) -> InjectResult:
    """Decrypt selected vault rows into a deploy env map.

    Raises :class:`InjectError` with a concrete blocker when the vault is sealed,
    a ref is missing/revoked, or a payment card is selected (cards never ship).
    """
    normalized = _normalize_refs(refs)
    if not normalized:
        return InjectResult(env={}, names=[], sources=[])

    active_seal = seal if seal is not None else vault.seal
    if active_seal.is_sealed:
        raise InjectError(
            "vault is sealed; unseal before secrets-at-ship inject",
            status_code=403,
        )

    env: dict[str, str] = {}
    sources: list[dict[str, str]] = []
    for ref in normalized:
        try:
            if ref.key_id:
                record = vault.get(ref.key_id)
                if record is None:
                    raise InjectError(f"{ref.env_name}: key_id {ref.key_id} not found in vault")
                if record.lifecycle != "active":
                    raise InjectError(
                        f"{ref.env_name}: key_id {ref.key_id} is {record.lifecycle}, not active"
                    )
                plaintext = vault.get_secret(ref.key_id)
                if plaintext is None:
                    raise InjectError(
                        f"{ref.env_name}: key_id {ref.key_id} has no decryptable secret"
                    )
                env[ref.env_name] = plaintext
                sources.append({"env_name": ref.env_name, "source": "key", "id": ref.key_id})
                continue

            assert ref.secret_id is not None
            if secrets is None:
                raise InjectError(
                    f"{ref.env_name}: secret_id requires SecretStore (password custody)"
                )
            record = secrets.get(ref.secret_id)
            if record is None:
                raise InjectError(f"{ref.env_name}: secret_id {ref.secret_id} not found in vault")
            if record.kind == "payment_card":
                raise InjectError(
                    f"{ref.env_name}: payment cards cannot be injected at ship "
                    "(PCI — cards never leave OpenVault onto a host)"
                )
            if record.lifecycle != "active":
                raise InjectError(
                    f"{ref.env_name}: secret_id {ref.secret_id} is {record.lifecycle}, not active"
                )
            plaintext = secrets.reveal(ref.secret_id)
            if plaintext is None:
                raise InjectError(
                    f"{ref.env_name}: secret_id {ref.secret_id} has no decryptable secret"
                )
            env[ref.env_name] = plaintext
            sources.append({"env_name": ref.env_name, "source": "secret", "id": ref.secret_id})
        except VaultSealedError as exc:
            raise InjectError(str(exc), status_code=403) from exc

    return InjectResult(env=env, names=list(env.keys()), sources=sources)


# Labels that are also the env names the host adapters read. A matching vault
# row is the credential. Process env remains the fallback inside the engine.
HOST_ENV_LABELS = frozenset(
    {
        "CLOUDFLARE_API_TOKEN",
        "CF_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "NETLIFY_AUTH_TOKEN",
        "NETLIFY_SITE_ID",
        "COOLIFY_URL",
        "COOLIFY_TOKEN",
        "COOLIFY_APP_UUID",
        "SPACESHIP_FTP_HOST",
        "SPACESHIP_FTP_USER",
        "SPACESHIP_FTP_PASS",
        "SPACESHIP_FTP_DIR",
        "SPACESHIP_PUBLIC_URL",
        "SPACESHIP_FTP_ENV_DIR",
        "OPENVAULT_VPS_USER",
        "OPENVAULT_VPS_PORT",
        "OPENVAULT_VPS_KEY",
        "OPENVAULT_VPS_REPLICAS",
    }
)


def _is_host_credential_row(key: KeyRecord) -> bool:
    """Whether a row may supply a ship host credential.

    pooled + no account + enabled + active, all four. DR-0009 keeps tenant
    custody out of anything OpenVault spends, and a host token is spent: it
    authenticates a deploy on the operator's own Cloudflare, Netlify, Coolify,
    FTP or VPS account. A ``custody == "tenant"`` row (somebody else's
    credential) and an ``account_id``-bound row (a provider-account key, not a
    host credential) are never eligible, so neither can shadow or replace the
    operator's pooled row of the same label.
    """
    return (
        key.label in HOST_ENV_LABELS
        and key.enabled
        and key.lifecycle == "active"
        and key.custody == "pooled"
        and key.account_id is None
    )


def vault_host_env(
    vault: KeyVault,
    *,
    on_decrypt: Callable[[str, str], None] | None = None,
) -> dict[str, str]:
    """Copy host credentials out of the vault by label. Empty when sealed.

    Eligible rows are pooled, unbound to a provider account, enabled and
    active (see ``_is_host_credential_row``). When several eligible rows share
    a label the most recently created one wins; a created_at tie falls back to
    the larger row id so the answer never depends on list order. The choice is
    made before any decrypt, so exactly one row per label is decrypted and
    superseded or ineligible rows are never opened.

    ``on_decrypt(label, key_ref)`` runs once per decrypted credential with the
    label and the first 8 characters of the row id, never the value. The
    caller owns the secret-audit writer (it lives in app.py), so this module
    takes a callback rather than importing it.

    Values are plaintext for the deploy call only. Callers must scrub them
    from any response and must not call this for a target that never leaves
    the machine. This function does not log them.
    """
    if vault.seal.is_sealed:
        return {}
    chosen: dict[str, KeyRecord] = {}
    for key in vault.list_keys():
        if not _is_host_credential_row(key):
            continue
        best = chosen.get(key.label)
        if best is None or (key.created_at, key.id) > (best.created_at, best.id):
            chosen[key.label] = key
    found: dict[str, str] = {}
    for label in sorted(chosen):
        key = chosen[label]
        try:
            secret = vault.get_secret(key.id)
        except VaultSealedError:
            return {}
        if secret:
            if on_decrypt is not None:
                on_decrypt(label, key.id[:8])
            found[label] = secret
    if "CLOUDFLARE_API_TOKEN" not in found and found.get("CF_API_TOKEN"):
        found["CLOUDFLARE_API_TOKEN"] = found["CF_API_TOKEN"]
    return found

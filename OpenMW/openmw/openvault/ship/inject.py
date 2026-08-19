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
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from openmw.openvault.vault.crypto import Seal, VaultSealedError
from openmw.openvault.vault.secrets import SecretStore
from openmw.openvault.vault.store import KeyVault

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
                secret_id=(
                    str(raw["secret_id"]).strip() or None
                )
                if raw.get("secret_id")
                else None,
            )
        name = ref.env_name.strip()
        if not name:
            raise InjectError("ship secret binding missing env_name")
        if not _ENV_NAME_RE.match(name):
            raise InjectError(
                f"invalid env_name {name!r}; use [A-Za-z_][A-Za-z0-9_]*"
            )
        if name in seen:
            raise InjectError(f"duplicate env_name {name!r} in ship secrets")
        seen.add(name)
        has_key = bool(ref.key_id)
        has_secret = bool(ref.secret_id)
        if has_key == has_secret:
            raise InjectError(
                f"{name}: provide exactly one of key_id or secret_id"
            )
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
                    raise InjectError(
                        f"{ref.env_name}: key_id {ref.key_id} not found in vault"
                    )
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
                sources.append(
                    {"env_name": ref.env_name, "source": "key", "id": ref.key_id}
                )
                continue

            assert ref.secret_id is not None
            if secrets is None:
                raise InjectError(
                    f"{ref.env_name}: secret_id requires SecretStore (password custody)"
                )
            record = secrets.get(ref.secret_id)
            if record is None:
                raise InjectError(
                    f"{ref.env_name}: secret_id {ref.secret_id} not found in vault"
                )
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
            sources.append(
                {"env_name": ref.env_name, "source": "secret", "id": ref.secret_id}
            )
        except VaultSealedError as exc:
            raise InjectError(str(exc), status_code=403) from exc

    return InjectResult(env=env, names=list(env.keys()), sources=sources)

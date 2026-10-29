"""Move password-shaped KeyVault rows into SecretStore.

SITE_* / *_PASSWORD rows that already landed as ``provider=custom`` with an
empty base_url are not LLM hops. New ingest refuses them on ``POST /api/keys``;
this module repairs the rows that got in before that gate. Real custom LLM hops
(empty base_url but not password-shaped) stay keys.
"""

from __future__ import annotations

from typing import Any

import structlog

from openmw.openvault.vault.crypto import VaultCryptoError, VaultSealedError
from openmw.openvault.vault.free_keys_onboard import looks_like_site_password_key
from openmw.openvault.vault.secrets import SecretStore, SecretValidationError
from openmw.openvault.vault.store import KeyVault

log = structlog.get_logger()


def _key_ref(key_id: str) -> str:
    return key_id[:8] if key_id else ""


def migrate_site_password_keys(vault: KeyVault, secrets: SecretStore) -> dict[str, Any]:
    """Copy matching passwords into secrets, then delete those key rows.

    Fail closed when sealed: no decrypt, no silent skip that leaves hops.
    Duplicate secret labels skip a second write and still delete the hop.

    Only ``lifecycle == "active"`` hops migrate. A revoked, rotated or
    compromised hop is retained encrypted for audit and may hold a dead or
    leaked password; copying it would mint an ACTIVE secret under its label and
    deleting it would destroy the audit row. Those hops are never decrypted,
    never written, never deleted, and are counted as ``skipped_inactive``.

    ``remaining_site_keys`` still counts every password-shaped row left in
    KeyVault, so it now includes the ``skipped_inactive`` hops: ``ok`` can be
    True with ``remaining_site_keys > 0``. ``ok`` means "no failures", not "no
    rows left".
    """
    if vault.seal.is_sealed:
        raise VaultSealedError(
            "vault is sealed; POST /api/vault/unseal with the passphrase first"
        )

    existing = {
        row.label
        for row in secrets.list_secrets(kind="password")
        if row.lifecycle == "active"
    }
    migrated = 0
    already = 0
    failed = 0
    skipped_inactive = 0
    for record in list(vault.list_keys()):
        if not looks_like_site_password_key(
            label=record.label,
            provider=record.provider,
            base_url=record.base_url,
        ):
            continue
        ref = _key_ref(record.id)
        if record.lifecycle != "active":
            skipped_inactive += 1
            log.info(
                "openvault_site_password_migrate_skipped_inactive",
                key_ref=ref,
                lifecycle=record.lifecycle,
            )
            continue
        try:
            password = vault.get_secret(record.id)
        except VaultSealedError:
            raise
        except VaultCryptoError:
            failed += 1
            log.warning("openvault_site_password_migrate_decrypt_failed", key_ref=ref)
            continue
        if not password:
            failed += 1
            log.warning("openvault_site_password_migrate_empty", key_ref=ref)
            continue
        if record.label not in existing:
            try:
                secrets.create_password(
                    label=record.label,
                    password=password,
                    account_id=record.account_id,
                )
            except SecretValidationError:
                failed += 1
                log.warning("openvault_site_password_migrate_write_failed", key_ref=ref)
                continue
            existing.add(record.label)
            migrated += 1
        else:
            already += 1
        if not vault.delete(record.id):
            failed += 1
            log.warning("openvault_site_password_migrate_delete_failed", key_ref=ref)

    remaining = sum(
        1
        for record in vault.list_keys()
        if looks_like_site_password_key(
            label=record.label,
            provider=record.provider,
            base_url=record.base_url,
        )
    )
    log.info(
        "openvault_site_password_migrate",
        migrated=migrated,
        already_in_secrets=already,
        failed=failed,
        skipped_inactive=skipped_inactive,
        remaining_site_keys=remaining,
    )
    return {
        "ok": failed == 0,
        "migrated": migrated,
        "already_in_secrets": already,
        "failed": failed,
        "skipped_inactive": skipped_inactive,
        "remaining_site_keys": remaining,
    }

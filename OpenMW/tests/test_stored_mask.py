"""Stored-mask column: list_keys must not decrypt every secret."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.vault.crypto import Seal, mask_secret
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def test_list_keys_uses_stored_mask_without_decrypt(vault: KeyVault) -> None:
    secret = "sk-test-secret-value-123456"
    created = vault.create(
        label="work",
        provider="openai",
        secret=secret,
        role="primary",
    )
    assert created.masked_secret == mask_secret(secret)

    with patch.object(vault._seal, "decrypt", wraps=vault._seal.decrypt) as dec:
        listed = vault.list_keys()
        assert len(listed) == 1
        assert listed[0].masked_secret == mask_secret(secret)
        # Public list path must not touch plaintext.
        dec.assert_not_called()


def test_update_secret_refreshes_stored_mask(vault: KeyVault) -> None:
    rec = vault.create(
        label="work",
        provider="openai",
        secret="sk-old-secret-aaaaaaaa",
        role="primary",
    )
    new_secret = "sk-new-secret-bbbbbbbb"
    updated = vault.update(rec.id, secret=new_secret)
    assert updated is not None
    assert updated.masked_secret == mask_secret(new_secret)
    assert vault.get_secret(rec.id) == new_secret


def test_masked_column_backfill_on_open(tmp_path: Path) -> None:
    """Rows created before the column still get a mask on next KeyVault open."""
    seal = Seal(Fernet.generate_key())
    db = tmp_path / "keys.db"
    v1 = KeyVault(db_path=db, seal=seal)
    rec = v1.create(
        label="legacy",
        provider="openai",
        secret="sk-legacy-secret-zzzzzz",
        role="backup",
    )
    # Simulate pre-migration row: wipe stored mask.
    with v1._connect() as conn:
        conn.execute("UPDATE keys SET masked = '' WHERE id = ?", (rec.id,))
        conn.commit()

    v2 = KeyVault(db_path=db, seal=seal)
    listed = v2.list_keys()
    assert listed[0].masked_secret == mask_secret("sk-legacy-secret-zzzzzz")
    with v2._connect() as conn:
        row = conn.execute("SELECT masked FROM keys WHERE id = ?", (rec.id,)).fetchone()
    assert row is not None
    assert row["masked"] == mask_secret("sk-legacy-secret-zzzzzz")

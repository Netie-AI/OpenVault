"""Precheck structured logs must not expose full vault key UUIDs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.precheck import PrecheckResult, precheck_one
from openmw.openvault.vault.store import KeyVault


@pytest.fixture()
def vault(tmp_path: Path) -> KeyVault:
    seal = Seal(Fernet.generate_key())
    return KeyVault(db_path=tmp_path / "keys.db", seal=seal)


def test_precheck_log_uses_key_ref_not_full_id(vault: KeyVault) -> None:
    record = vault.create(
        label="work-openai",
        provider="openai",
        secret="sk-test-secret-value-123456",
        role="primary",
        base_url="https://example.com/v1",
    )
    assert len(record.id) == 32

    fake = PrecheckResult(
        key_id=record.id,
        status="auth_fail",
        latency_ms=12.0,
        error="HTTP 401",
    )

    async def _run() -> None:
        with (
            patch(
                "openmw.openvault.vault.precheck.probe_key",
                new=AsyncMock(return_value=fake),
            ),
            patch("openmw.openvault.vault.precheck.log") as mock_log,
        ):
            result = await precheck_one(vault, record.id)
            assert result.status == "auth_fail"
            mock_log.info.assert_called_once()
            args, kwargs = mock_log.info.call_args
            assert args[0] == "openvault_precheck"
            assert "key_id" not in kwargs
            assert kwargs["key_ref"] == record.id[:8]
            assert len(kwargs["key_ref"]) == 8
            assert kwargs["label"] == "work-openai"
            assert kwargs["provider"] == "openai"
            assert kwargs["role"] == "primary"
            assert kwargs["status"] == "auth_fail"
            assert kwargs["error"] == "HTTP 401"
            assert kwargs["latency_ms"] == 12.0
            for key, value in kwargs.items():
                assert record.id not in str(value), f"full key id leaked in {key}={value!r}"

    asyncio.run(_run())

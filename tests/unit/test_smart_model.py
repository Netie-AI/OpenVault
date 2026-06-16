"""Unit tests for SMART model parsing."""

from __future__ import annotations

import pytest

from nvme_sentinel.hal.enums import CriticalWarning
from nvme_sentinel.hal.exceptions import ParseError
from nvme_sentinel.models.smart import SmartHealthLog


def test_smart_from_bytes_parses_celsius() -> None:
    """SMART parser returns Celsius conversion from Kelvin source bytes."""
    buf = bytearray(512)
    buf[0] = int(CriticalWarning.AVAILABLE_SPARE_LOW.value)
    buf[1:3] = (300).to_bytes(2, "little")
    log = SmartHealthLog.from_bytes(bytes(buf))
    assert log.composite_temperature_kelvin == 300
    assert log.composite_temperature_celsius == 27


def test_smart_from_bytes_rejects_short_buffer() -> None:
    """SMART parser rejects undersized payloads with ParseError."""
    with pytest.raises(ParseError):
        SmartHealthLog.from_bytes(b"\x00" * 511)

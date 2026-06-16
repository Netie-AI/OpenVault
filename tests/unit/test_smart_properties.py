"""Hypothesis property tests for SMART parser."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nvme_sentinel.hal.exceptions import ParseError
from nvme_sentinel.models.smart import SmartHealthLog


@given(st.binary(min_size=512, max_size=512))
@settings(max_examples=200, deadline=None)
def test_smart_parser_never_crashes_on_arbitrary_512_bytes(buf: bytes) -> None:
    """Parser either raises ParseError or returns a SmartHealthLog."""
    try:
        log = SmartHealthLog.from_bytes(buf)
    except ParseError:
        return
    assert log.composite_temperature_kelvin >= 0


@given(st.binary().filter(lambda b: len(b) != 512))
def test_smart_parser_rejects_wrong_length(buf: bytes) -> None:
    """Undersized or oversized buffers raise ParseError."""
    with pytest.raises(ParseError):
        SmartHealthLog.from_bytes(buf)


@given(st.integers(min_value=0, max_value=63))
def test_critical_warning_flag_roundtrip(val: int) -> None:
    """Critical warning byte round-trips through parser."""
    buf = bytearray(512)
    buf[0] = val & 0x3F
    buf[1:3] = (273 + 25).to_bytes(2, "little")
    log = SmartHealthLog.from_bytes(bytes(buf))
    assert log.critical_warning.value == (val & 0x3F)

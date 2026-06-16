"""Generate deterministic NVMe binary fixtures.

Run with:
    uv run python scripts/build_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

FIXTURES_DIR = Path("tests/fixtures")


def _set_le(buf: bytearray, offset: int, width: int, value: int) -> None:
    buf[offset : offset + width] = value.to_bytes(width, "little", signed=False)


def _set_ascii(buf: bytearray, offset: int, width: int, value: str) -> None:
    encoded = value.encode("ascii", errors="strict")
    if len(encoded) > width:
        msg = f"value '{value}' too long for field width {width}"
        raise ValueError(msg)
    buf[offset : offset + width] = encoded.ljust(width, b" ")


def build_identify_controller() -> bytes:
    """Build a 4096-byte Identify Controller fixture."""
    buf = bytearray(4096)
    # NVMe Identify Controller: VID/SSVID at offsets 0..3.
    _set_le(buf, 0, 2, 0x144D)
    _set_le(buf, 2, 2, 0x144D)
    # Serial Number bytes 4..23 (20 bytes).
    _set_ascii(buf, 4, 20, "NVMESENTINEL0001")
    # Model Number bytes 24..63 (40 bytes).
    _set_ascii(buf, 24, 40, "Generic NVMe SSD Reference")
    # Firmware Revision bytes 64..71 (8 bytes).
    _set_ascii(buf, 64, 8, "GS01GR00")
    # Controller ID bytes 78..79.
    _set_le(buf, 78, 2, 0x0001)
    # Number of Namespaces bytes 516..519.
    _set_le(buf, 516, 4, 1)
    return bytes(buf)


def build_identify_namespace() -> bytes:
    """Build a 4096-byte Identify Namespace fixture for NSID 1."""
    buf = bytearray(4096)
    # NSZE / NCAP / NUSE at offsets 0..23.
    _set_le(buf, 0, 8, 0x200000)
    _set_le(buf, 8, 8, 0x200000)
    _set_le(buf, 16, 8, 0x100000)
    # NLBAF / FLBAS at offsets 25 / 26.
    _set_le(buf, 25, 1, 0)
    _set_le(buf, 26, 1, 0)
    # LBAF[0] starts at offset 128 (4 bytes):
    # metadata size (2 bytes), LBA data size log2 (1 byte), relative performance (2 bits in byte).
    _set_le(buf, 128, 2, 0)
    _set_le(buf, 130, 1, 12)
    _set_le(buf, 131, 1, 0)
    return bytes(buf)


def _set_u128(buf: bytearray, offset: int, value: int) -> None:
    _set_le(buf, offset, 16, value)


def build_smart_healthy() -> bytes:
    """Build a 512-byte SMART log fixture with healthy metrics."""
    # §4.4 offsets 192-199: warning/critical composite temp time (min); unset (0) in this fixture.
    buf = bytearray(512)
    _set_le(buf, 0, 1, 0)
    _set_le(buf, 1, 2, 313)
    _set_le(buf, 3, 1, 99)
    _set_le(buf, 4, 1, 10)
    _set_le(buf, 5, 1, 3)
    _set_u128(buf, 32, 1_000_000)
    _set_u128(buf, 48, 500_000)
    _set_u128(buf, 64, 10_000_000)
    _set_u128(buf, 80, 5_000_000)
    _set_u128(buf, 112, 42)
    _set_u128(buf, 128, 8_760)
    _set_u128(buf, 144, 2)
    _set_u128(buf, 160, 0)
    return bytes(buf)


def build_smart_degraded() -> bytes:
    """Build a 512-byte SMART log fixture with degraded metrics."""
    # §4.4 offsets 192-199: warning/critical composite temp time (min); unset (0) in this fixture.
    buf = bytearray(512)
    _set_le(buf, 0, 1, 0b00000101)
    _set_le(buf, 1, 2, 358)
    _set_le(buf, 3, 1, 8)
    _set_le(buf, 4, 1, 10)
    _set_le(buf, 5, 1, 97)
    _set_u128(buf, 144, 120)
    _set_u128(buf, 160, 1234)
    return bytes(buf)


def main() -> None:
    """Write all fixture binaries to tests/fixtures."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURES_DIR / "identify_ctrl_generic.bin").write_bytes(build_identify_controller())
    (FIXTURES_DIR / "identify_ns.bin").write_bytes(build_identify_namespace())
    (FIXTURES_DIR / "smart_healthy.bin").write_bytes(build_smart_healthy())
    (FIXTURES_DIR / "smart_degraded.bin").write_bytes(build_smart_degraded())


if __name__ == "__main__":
    main()

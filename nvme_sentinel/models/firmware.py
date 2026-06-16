"""Firmware Slot Information log model parser subset."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nvme_sentinel.hal.exceptions import ParseError


def _decode_slot(raw: bytes) -> str:
    return raw.decode("ascii", errors="ignore").strip(" \x00")


class FirmwareSlotInfo(BaseModel):
    """Parsed subset of 512-byte Firmware Slot Information log."""

    model_config = ConfigDict(frozen=True)

    afi: int
    frs: list[str]

    @classmethod
    def from_bytes(cls, buf: bytes) -> FirmwareSlotInfo:
        """Parse AFI and seven FRS fields from a 512-byte payload."""
        if len(buf) != 512:
            raise ParseError(f"Firmware Slot log requires 512 bytes, got {len(buf)}")

        # NVMe Firmware Slot Information log page: AFI at offset 0, FRS1..FRS7 at 8-byte strides.
        slots = [_decode_slot(buf[offset : offset + 8]) for offset in range(8, 64, 8)]
        return cls(afi=buf[0], frs=slots)

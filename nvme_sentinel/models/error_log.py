"""Error Information log model parser subset."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nvme_sentinel.hal.exceptions import ParseError


class ErrorLogEntry(BaseModel):
    """Parsed subset of one 64-byte NVMe Error Information log entry."""

    model_config = ConfigDict(frozen=True)

    error_count: int
    status_field: int

    @classmethod
    def from_bytes(cls, buf: bytes) -> ErrorLogEntry:
        """Parse one 64-byte Error Information entry."""
        if len(buf) != 64:
            raise ParseError(f"Error Information entry requires 64 bytes, got {len(buf)}")

        # NVMe Error Information entry subset:
        # error_count at bytes 0..7, status_field at bytes 10..11.
        return cls(
            error_count=int.from_bytes(buf[0:8], "little"),
            status_field=int.from_bytes(buf[10:12], "little"),
        )

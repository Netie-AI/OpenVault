"""Identify Controller/Namespace model subset parsers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nvme_sentinel.hal.exceptions import ParseError


def _decode_ascii(raw: bytes) -> str:
    return raw.decode("ascii", errors="ignore").strip(" \x00")


class ControllerIdentify(BaseModel):
    """Parsed subset of Identify Controller data (4096-byte payload)."""

    model_config = ConfigDict(frozen=True)

    vid: int
    ssvid: int
    sn: str
    mn: str
    fr: str
    rab: int
    ieee: str
    cntlid: int
    ver: int
    nn: int
    fguid: str
    sqes: int
    cqes: int

    @classmethod
    def from_bytes(cls, buf: bytes) -> ControllerIdentify:
        """Parse selected Identify Controller fields from a 4096-byte payload."""
        if len(buf) != 4096:
            raise ParseError(f"Identify Controller requires 4096 bytes, got {len(buf)}")

        # NVMe Base Spec 2.0c §5.17.2.1 offsets.
        return cls(
            vid=int.from_bytes(buf[0:2], "little"),
            ssvid=int.from_bytes(buf[2:4], "little"),
            sn=_decode_ascii(buf[4:24]),  # bytes 4-23
            mn=_decode_ascii(buf[24:64]),  # bytes 24-63
            fr=_decode_ascii(buf[64:72]),  # bytes 64-71
            rab=buf[72],
            ieee=buf[73:76].hex(),
            cntlid=int.from_bytes(buf[78:80], "little"),
            ver=int.from_bytes(buf[80:84], "little"),
            sqes=buf[512],
            cqes=buf[513],
            nn=int.from_bytes(buf[516:520], "little"),  # bytes 516-519
            fguid=buf[112:128].hex(),
        )


class NamespaceIdentify(BaseModel):
    """Parsed subset of Identify Namespace data (4096-byte payload)."""

    model_config = ConfigDict(frozen=True)

    nsid: int
    nsze: int
    ncap: int
    nuse: int
    nsfeat: int
    nlbaf: int
    flbas: int
    lbaf: list[tuple[int, int, int]]
    nguid: str
    eui64: str

    @classmethod
    def from_bytes(cls, buf: bytes, nsid: int) -> NamespaceIdentify:
        """Parse selected Identify Namespace fields from a 4096-byte payload."""
        if len(buf) != 4096:
            raise ParseError(f"Identify Namespace requires 4096 bytes, got {len(buf)}")
        if nsid <= 0:
            raise ParseError(f"Namespace identifier must be > 0, got {nsid}")

        # NVMe Base Spec 2.0c §5.17.2.2 offsets.
        nlbaf = buf[25]
        lbaf_entries: list[tuple[int, int, int]] = []
        for index in range(min(nlbaf + 1, 16)):
            base = 128 + (index * 4)
            ms = int.from_bytes(buf[base : base + 2], "little")
            lbads = buf[base + 2]
            rp = buf[base + 3] & 0b11
            lbaf_entries.append((ms, lbads, rp))

        return cls(
            nsid=nsid,
            nsze=int.from_bytes(buf[0:8], "little"),
            ncap=int.from_bytes(buf[8:16], "little"),
            nuse=int.from_bytes(buf[16:24], "little"),
            nsfeat=buf[24],
            nlbaf=nlbaf,
            flbas=buf[26],
            lbaf=lbaf_entries,
            nguid=buf[104:120].hex(),
            eui64=buf[120:128].hex(),
        )

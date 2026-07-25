"""Mock-adapter fixture resolution.

``MockNvmeAdapter`` defaults its fixture directory to the cwd-relative
``tests/fixtures`` (its own TODO T9.1), so it only constructs when the server
happens to be started from the repo root. The console cannot depend on cwd, so
we resolve the shipped fixtures explicitly and synthesize spec-shaped payloads
when they are absent.
"""

from __future__ import annotations

import os
from pathlib import Path

from nvme_sentinel.adapters.mock import MockNvmeAdapter

_CTRL_NAME = "identify_ctrl_generic.bin"
_NS_NAME = "identify_ns.bin"
_SMART_NAME = "smart_healthy.bin"

MOCK_DEVICE_PATH = "/dev/mock-nvme0"


def _shipped_fixture_dir() -> Path | None:
    """Locate the fixture directory that ships next to the nvme_sentinel package."""
    override = os.environ.get("OPENVAULT_SENTINEL_FIXTURES")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_dir() else None

    import nvme_sentinel

    pkg_init = getattr(nvme_sentinel, "__file__", None)
    if not pkg_init:
        return None
    candidate = Path(pkg_init).resolve().parent.parent / "tests" / "fixtures"
    return candidate if candidate.is_dir() else None


def _synthesized_fixture_dir() -> Path:
    """Write deterministic, spec-shaped NVMe payloads under OPENVAULT_HOME."""
    from openmw.openvault.paths import ensure_home

    out = ensure_home() / "sentinel-fixtures"
    out.mkdir(parents=True, exist_ok=True)
    ctrl = out / _CTRL_NAME
    ns = out / _NS_NAME
    smart = out / _SMART_NAME
    if not ctrl.is_file():
        ctrl.write_bytes(_synth_identify_controller())
    if not ns.is_file():
        ns.write_bytes(_synth_identify_namespace())
    if not smart.is_file():
        smart.write_bytes(_synth_smart_health())
    return out


def resolve_fixture_dir() -> tuple[Path, bool]:
    """Return (directory, is_synthesized) for the mock adapter's fixtures."""
    shipped = _shipped_fixture_dir()
    if shipped is not None and all(
        (shipped / name).is_file() for name in (_CTRL_NAME, _NS_NAME, _SMART_NAME)
    ):
        return shipped, False
    return _synthesized_fixture_dir(), True


def mock_adapter(device_path: str = MOCK_DEVICE_PATH) -> MockNvmeAdapter:
    """Build a MockNvmeAdapter bound to fixtures resolved independently of cwd."""
    fixtures, _ = resolve_fixture_dir()
    return MockNvmeAdapter(
        device_path,
        identify_ctrl_path=fixtures / _CTRL_NAME,
        identify_ns_path=fixtures / _NS_NAME,
        smart_path=fixtures / _SMART_NAME,
    )


def _ascii_field(text: str, width: int) -> bytes:
    """NVMe ASCII fields are space-padded, not NUL-padded (Base Spec 2.0c §1.5)."""
    return text.encode("ascii", "ignore")[:width].ljust(width, b" ")


def _synth_identify_controller() -> bytes:
    """4096-byte Identify Controller payload at the offsets ControllerIdentify parses."""
    buf = bytearray(4096)
    buf[0:2] = (0x144D).to_bytes(2, "little")  # vid
    buf[2:4] = (0x144D).to_bytes(2, "little")  # ssvid
    buf[4:24] = _ascii_field("OPENVAULT-MOCK-0001", 20)  # sn
    buf[24:64] = _ascii_field("OpenVault Mock NVMe Controller", 40)  # mn
    buf[64:72] = _ascii_field("MOCK1.00", 8)  # fr
    buf[72] = 6  # rab
    buf[73:76] = bytes((0x00, 0x25, 0x38))  # ieee
    buf[78:80] = (1).to_bytes(2, "little")  # cntlid
    buf[80:84] = (0x00020000).to_bytes(4, "little")  # ver = 2.0.0
    buf[512] = 0x66  # sqes
    buf[513] = 0x44  # cqes
    buf[516:520] = (1).to_bytes(4, "little")  # nn
    return bytes(buf)


def _synth_identify_namespace() -> bytes:
    """4096-byte Identify Namespace payload for a single 512 GB, 512 B-LBA namespace."""
    buf = bytearray(4096)
    lba_count = 1_000_215_216
    buf[0:8] = lba_count.to_bytes(8, "little")  # nsze
    buf[8:16] = lba_count.to_bytes(8, "little")  # ncap
    buf[16:24] = lba_count.to_bytes(8, "little")  # nuse
    buf[24] = 0x02  # nsfeat
    buf[25] = 0  # nlbaf: one format
    buf[26] = 0  # flbas -> format 0
    buf[128:132] = bytes((0, 0, 9, 0))  # lbaf0: ms=0, lbads=9 (512 B), rp=0
    return bytes(buf)


def _synth_smart_health() -> bytes:
    """512-byte SMART/Health log page at the offsets SmartHealthLog parses."""
    buf = bytearray(512)
    buf[0] = 0x00  # critical_warning: none
    buf[1:3] = (273 + 41).to_bytes(2, "little")  # composite temp: 41 C
    buf[3] = 100  # available_spare
    buf[4] = 10  # available_spare_threshold
    buf[5] = 3  # percentage_used
    buf[6] = 0  # endurance group critical warning summary
    buf[32:48] = (48_112_233).to_bytes(16, "little")  # data_units_read
    buf[48:64] = (31_004_871).to_bytes(16, "little")  # data_units_written
    buf[64:80] = (612_004_112).to_bytes(16, "little")  # host_read_commands
    buf[80:96] = (401_882_310).to_bytes(16, "little")  # host_write_commands
    buf[96:112] = (4_211).to_bytes(16, "little")  # controller_busy_time_minutes
    buf[112:128] = (1_284).to_bytes(16, "little")  # power_cycles
    buf[128:144] = (5_902).to_bytes(16, "little")  # power_on_hours
    buf[144:160] = (37).to_bytes(16, "little")  # unsafe_shutdowns
    buf[160:176] = (0).to_bytes(16, "little")  # media_and_data_integrity_errors
    buf[176:192] = (0).to_bytes(16, "little")  # error info log entry count
    buf[192:196] = (0).to_bytes(4, "little")  # warning composite temp time
    buf[196:200] = (0).to_bytes(4, "little")  # critical composite temp time
    return bytes(buf)

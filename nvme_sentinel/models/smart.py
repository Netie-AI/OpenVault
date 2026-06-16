"""SMART / Health log model and parser (implementation_plan.md §4.4)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nvme_sentinel.hal.enums import CriticalWarning
from nvme_sentinel.hal.exceptions import ParseError


class SmartHealthLog(BaseModel):
    """Typed SMART/Health snapshot decoded from NVMe Log Page 0x02."""

    model_config = ConfigDict(frozen=True)

    critical_warning: CriticalWarning
    composite_temperature_kelvin: int
    available_spare: int
    available_spare_threshold: int
    percentage_used: int
    endurance_group_critical_warning_summary: int
    data_units_read: int
    data_units_written: int
    host_read_commands: int
    host_write_commands: int
    controller_busy_time_minutes: int
    power_cycles: int
    power_on_hours: int
    unsafe_shutdowns: int
    media_and_data_integrity_errors: int
    number_of_error_information_log_entries: int
    warning_composite_temp_time_minutes: int
    critical_composite_temp_time_minutes: int

    @property
    def composite_temperature_celsius(self) -> int:
        """Return composite temperature in Celsius (K - 273)."""
        return self.composite_temperature_kelvin - 273

    @classmethod
    def from_bytes(cls, buf: bytes) -> SmartHealthLog:
        """Parse a 512-byte SMART payload into a typed model."""
        if len(buf) != 512:
            raise ParseError(f"SMART log must be exactly 512 bytes, got {len(buf)}")

        # implementation_plan.md §4.4: offset 0, 1 byte; mask reserved bits 6-7.
        critical_warning = CriticalWarning(buf[0] & 0x3F)
        # implementation_plan.md §4.4: offsets 1-2, u16 LE, unit Kelvin.
        composite_temperature_kelvin = int.from_bytes(buf[1:3], "little")
        # implementation_plan.md §4.4: offsets 3, 4, 5, 6.
        available_spare = buf[3]
        available_spare_threshold = buf[4]
        percentage_used = buf[5]
        endurance_group_critical_warning_summary = buf[6]

        # implementation_plan.md §4.4: u128 LE counters.
        # Python int is arbitrary precision, so 128-bit values are safe.
        data_units_read = int.from_bytes(buf[32:48], "little")
        data_units_written = int.from_bytes(buf[48:64], "little")
        host_read_commands = int.from_bytes(buf[64:80], "little")
        host_write_commands = int.from_bytes(buf[80:96], "little")
        controller_busy_time_minutes = int.from_bytes(buf[96:112], "little")
        power_cycles = int.from_bytes(buf[112:128], "little")
        power_on_hours = int.from_bytes(buf[128:144], "little")
        unsafe_shutdowns = int.from_bytes(buf[144:160], "little")
        media_and_data_integrity_errors = int.from_bytes(buf[160:176], "little")
        number_of_error_information_log_entries = int.from_bytes(buf[176:192], "little")

        # implementation_plan.md §4.4: 192-195 Warning Composite Temperature Time (min), u32 LE;
        # 196-199 Critical Composite Temperature Time (min), u32 LE.
        warning_composite_temp_time_minutes = int.from_bytes(buf[192:196], "little")
        critical_composite_temp_time_minutes = int.from_bytes(buf[196:200], "little")

        return cls(
            critical_warning=critical_warning,
            composite_temperature_kelvin=composite_temperature_kelvin,
            available_spare=available_spare,
            available_spare_threshold=available_spare_threshold,
            percentage_used=percentage_used,
            endurance_group_critical_warning_summary=endurance_group_critical_warning_summary,
            data_units_read=data_units_read,
            data_units_written=data_units_written,
            host_read_commands=host_read_commands,
            host_write_commands=host_write_commands,
            controller_busy_time_minutes=controller_busy_time_minutes,
            power_cycles=power_cycles,
            power_on_hours=power_on_hours,
            unsafe_shutdowns=unsafe_shutdowns,
            media_and_data_integrity_errors=media_and_data_integrity_errors,
            number_of_error_information_log_entries=number_of_error_information_log_entries,
            warning_composite_temp_time_minutes=warning_composite_temp_time_minutes,
            critical_composite_temp_time_minutes=critical_composite_temp_time_minutes,
        )

    def to_dict(self) -> dict[str, int | str]:
        """Return a report-friendly dictionary with Celsius exposure."""
        return {
            "critical_warning": str(self.critical_warning),
            "composite_temperature_celsius": self.composite_temperature_celsius,
            "available_spare": self.available_spare,
            "available_spare_threshold": self.available_spare_threshold,
            "percentage_used": self.percentage_used,
            "endurance_group_critical_warning_summary": (
                self.endurance_group_critical_warning_summary
            ),
            "data_units_read": self.data_units_read,
            "data_units_written": self.data_units_written,
            "host_read_commands": self.host_read_commands,
            "host_write_commands": self.host_write_commands,
            "controller_busy_time_minutes": self.controller_busy_time_minutes,
            "power_cycles": self.power_cycles,
            "power_on_hours": self.power_on_hours,
            "unsafe_shutdowns": self.unsafe_shutdowns,
            "media_and_data_integrity_errors": self.media_and_data_integrity_errors,
            "number_of_error_information_log_entries": self.number_of_error_information_log_entries,
            "warning_composite_temp_time_minutes": self.warning_composite_temp_time_minutes,
            "critical_composite_temp_time_minutes": self.critical_composite_temp_time_minutes,
        }

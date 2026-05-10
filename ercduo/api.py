"""High-level API for ERC-DUO configuration and calibration.

Wraps the raw protocol layer with typed methods for reading and
writing device parameters. All read methods return Python types;
all write methods accept Python types and handle formatting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .models import (
    Axis, Protocol, ConfigData, CalibrationData, DeviceInfo,
    SPEED_ANGLE_VALUES,
)

if TYPE_CHECKING:
    from .protocol import ERCConnection

log = logging.getLogger(__name__)


def _fmt4(value: int) -> str:
    """Format an integer as a 4-character zero-padded string.

    Handles negative values with a leading minus sign.
    """
    if value < 0:
        return f"-{abs(value):03d}"
    return f"{value:04d}"


def _parse_int(value: str) -> int:
    """Parse a 4-character value string to int, handling signs."""
    return int(value.strip())


class ERCAPI:
    """Typed interface to ERC-DUO configuration and calibration commands.

    Args:
        conn: An open ERCConnection instance.
    """

    def __init__(self, conn: ERCConnection) -> None:
        self.conn = conn

    # ---- Device Info ----

    def read_device_info(self) -> DeviceInfo:
        """Read firmware version, baudrate, and protocol."""
        fw_raw = self.conn.read_param("FMW")
        # Firmware is returned as e.g. "0100" → "1.00"
        fw_major = int(fw_raw[:2])
        fw_minor = int(fw_raw[2:])
        firmware = f"{fw_major}.{fw_minor:02d}"

        baudrate = _parse_int(self.conn.read_param("BAU"))
        proto_val = _parse_int(self.conn.read_param("PRO"))

        return DeviceInfo(
            firmware_version=firmware,
            baudrate=baudrate,
            protocol=Protocol(proto_val),
        )

    def set_baudrate(self, baudrate: int) -> None:
        """Set communication baudrate (4800 or 9600)."""
        if baudrate not in (4800, 9600):
            raise ValueError(f"Baudrate must be 4800 or 9600, got {baudrate}")
        self.conn.set_param("BAU", _fmt4(baudrate))

    def set_protocol(self, protocol: Protocol) -> None:
        """Set the rotor communication protocol."""
        self.conn.set_param("PRO", _fmt4(protocol.value))

    def factory_reset(self) -> None:
        """Reset all settings to factory defaults. WARNING: irreversible."""
        self.conn.set_param("FDV", "0000")

    # ---- Configuration Read ----

    def read_config(self, axis: Axis) -> ConfigData:
        """Read all configuration parameters for the given axis."""
        suffix = str(axis.value)

        cfg = ConfigData(
            axis=axis,
            delay_before_move=_parse_int(self.conn.read_param(f"DM{suffix}")),
            tolerance=_parse_int(self.conn.read_param(f"TO{suffix}")),
            antenna_offset=_parse_int(self.conn.read_param(f"AO{suffix}")),
        )

        if axis == Axis.AZ:
            cfg.speed_low = _parse_int(self.conn.read_param(f"SL{suffix}"))
            cfg.speed_high = _parse_int(self.conn.read_param(f"SH{suffix}"))
            sa_code = _parse_int(self.conn.read_param(f"SA{suffix}"))
            cfg.speed_angle = sa_code

        return cfg

    # ---- Configuration Write ----

    def set_delay_before_move(self, axis: Axis, ms: int) -> None:
        """Set delay before move in milliseconds (0-5000)."""
        if not 0 <= ms <= 5000:
            raise ValueError(f"Delay must be 0-5000, got {ms}")
        self.conn.set_param(f"DM{axis.value}", _fmt4(ms))

    def set_tolerance(self, axis: Axis, degrees: int) -> None:
        """Set position tolerance in degrees (0-10)."""
        if not 0 <= degrees <= 10:
            raise ValueError(f"Tolerance must be 0-10, got {degrees}")
        self.conn.set_param(f"TO{axis.value}", _fmt4(degrees))

    def set_antenna_offset(self, axis: Axis, degrees: int) -> None:
        """Set antenna offset in degrees (AZ: -180..+180, EL: -90..+90)."""
        if axis == Axis.AZ and not -180 <= degrees <= 180:
            raise ValueError(f"AZ offset must be -180..+180, got {degrees}")
        if axis == Axis.EL and not -90 <= degrees <= 90:
            raise ValueError(f"EL offset must be -90..+90, got {degrees}")
        self.conn.set_param(f"AO{axis.value}", _fmt4(degrees))

    def set_speed_low(self, speed: int) -> None:
        """Set AZ low speed stage (1-4)."""
        if not 1 <= speed <= 4:
            raise ValueError(f"Speed must be 1-4, got {speed}")
        self.conn.set_param("SL1", _fmt4(speed))

    def set_speed_high(self, speed: int) -> None:
        """Set AZ high speed stage (1-4)."""
        if not 1 <= speed <= 4:
            raise ValueError(f"Speed must be 1-4, got {speed}")
        self.conn.set_param("SH1", _fmt4(speed))

    def set_speed_angle(self, degrees: int) -> None:
        """Set AZ speed angle in degrees (0, 10, 20, or 30)."""
        if degrees not in SPEED_ANGLE_VALUES:
            raise ValueError(f"Speed angle must be one of {SPEED_ANGLE_VALUES}, got {degrees}")
        self.conn.set_param("SA1", _fmt4(degrees))

    # ---- Calibration Read ----

    def read_calibration(self, axis: Axis) -> CalibrationData:
        """Read calibration data for the given axis."""
        suffix = str(axis.value)

        return CalibrationData(
            axis=axis,
            angle_max=_parse_int(self.conn.read_param(f"AR{suffix}")),
            angle_min=_parse_int(self.conn.read_param(f"AL{suffix}")),
            adc_max=_parse_int(self.conn.read_param(f"CR{suffix}")),
            adc_min=_parse_int(self.conn.read_param(f"CL{suffix}")),
        )

    # ---- Calibration Write ----

    def set_calibration_angle_max(self, axis: Axis, angle: int) -> None:
        """Record calibration at the CW/UP limit (sCR1 / sCR2).

        Sends the bearing shown on the dial at the current physical position.
        The ERC internally captures the ADC at this instant.

        Per manual: AZ range 0-360, EL range 0-180.
        For a 450° overlap rotator, enter 90 (the dial reading at CW limit).
        """
        max_val = 360 if axis == Axis.AZ else 180
        if not 0 <= angle <= max_val:
            raise ValueError(f"Angle must be 0-{max_val}, got {angle}")
        self.conn.set_param(f"CR{axis.value}", _fmt4(angle))

    def set_calibration_angle_min(self, axis: Axis, angle: int) -> None:
        """Record calibration at the CCW/DWN limit (sCL1 / sCL2).

        Sends the bearing shown on the dial at the current physical position.
        The ERC internally captures the ADC at this instant.

        Per manual: AZ range 0-360, EL range 0-360.
        """
        max_val = 360
        if not 0 <= angle <= max_val:
            raise ValueError(f"Angle must be 0-{max_val}, got {angle}")
        self.conn.set_param(f"CL{axis.value}", _fmt4(angle))

    # ---- Bulk operations ----

    def read_all(self) -> dict:
        """Read all device parameters. Returns a dict with structured data."""
        info = self.read_device_info()
        az_config = self.read_config(Axis.AZ)
        el_config = self.read_config(Axis.EL)
        az_cal = self.read_calibration(Axis.AZ)
        el_cal = self.read_calibration(Axis.EL)

        return {
            "device": info,
            "config_az": az_config,
            "config_el": el_config,
            "calibration_az": az_cal,
            "calibration_el": el_cal,
        }

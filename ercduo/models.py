"""Data models for ERC-DUO configuration and calibration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Axis(IntEnum):
    """Rotator axis identifier. Suffix used in API commands."""
    AZ = 1
    EL = 2


class Protocol(IntEnum):
    """Supported rotor communication protocols."""
    GS232A = 0
    GS232B = 1
    DCU1 = 2

    def __str__(self) -> str:
        return self.name


PROTOCOL_NAMES = {
    Protocol.GS232A: "GS232A (Yaesu)",
    Protocol.GS232B: "GS232B (Yaesu)",
    Protocol.DCU1: "DCU-1 (Hy-Gain)",
}


@dataclass
class CalibrationData:
    """Calibration parameters for one axis."""
    axis: Axis
    angle_min: int = 0       # bearing at CCW/DWN limit
    angle_max: int = 360     # bearing at CW/UP limit
    adc_min: int = 0         # ADC reading at min position
    adc_max: int = 1023      # ADC reading at max position

    @property
    def angle_range(self) -> int:
        return self.angle_max - self.angle_min

    @property
    def adc_range(self) -> int:
        return self.adc_max - self.adc_min


@dataclass
class ConfigData:
    """Configuration parameters for one axis."""
    axis: Axis
    delay_before_move: int = 1000   # ms, 0-5000
    antenna_offset: int = 0         # degrees
    tolerance: int = 2              # degrees, 0-10

    # AZ-only speed settings
    speed_low: int | None = None    # 1-4 (AZ only)
    speed_high: int | None = None   # 1-4 (AZ only)
    speed_angle: int | None = None  # 0/10/20/30 degrees (AZ only)


SPEED_ANGLE_VALUES = (0, 10, 20, 30)


@dataclass
class DeviceInfo:
    """Device identification and communication settings."""
    firmware_version: str = ""
    baudrate: int = 9600
    protocol: Protocol = Protocol.GS232B


@dataclass
class Position:
    """Current rotator position."""
    azimuth: float | None = None
    elevation: float | None = None

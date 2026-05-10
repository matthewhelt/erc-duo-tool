"""ERC-DUO V1.1 rotator interface control package.

Provides serial protocol, configuration, calibration, and rotor control
for the Easy-Rotor-Control DUO interface by Ing.-Büro E. Alba de Schmidt.
"""

from .models import Axis, Protocol, ConfigData, CalibrationData, DeviceInfo
from .protocol import ERCConnection
from .api import ERCAPI
from .control import RotorControl
from .calibration import CalibrationRunner

__all__ = [
    "Axis",
    "Protocol",
    "ConfigData",
    "CalibrationData",
    "DeviceInfo",
    "ERCConnection",
    "ERCAPI",
    "RotorControl",
    "CalibrationRunner",
]

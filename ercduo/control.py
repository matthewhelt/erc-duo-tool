"""Rotor control commands for ERC-DUO (GS232A/B and DCU-1).

Provides methods for reading position, commanding rotation,
and stopping the rotator. These are the operational commands
(as opposed to configuration/calibration).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .models import Axis, Position, Protocol

if TYPE_CHECKING:
    from .protocol import ERCConnection

log = logging.getLogger(__name__)


class RotorControl:
    """Rotor movement and position control via GS232 or DCU-1 protocol.

    Args:
        conn: An open ERCConnection instance.
        protocol: Active protocol on the ERC-DUO (affects response parsing).
    """

    def __init__(self, conn: ERCConnection, protocol: Protocol = Protocol.GS232B) -> None:
        self.conn = conn
        self.protocol = protocol

    # ---- Position queries ----

    def get_azimuth(self) -> float:
        """Request current azimuth position in degrees."""
        if self.protocol == Protocol.DCU1:
            response = self.conn.send_rotor_command("AI1;")
            if response and response.startswith(";"):
                return float(response[1:].strip())
            raise ValueError(f"Unexpected DCU-1 AZ response: {response!r}")

        # GS232A/B
        response = self.conn.send_rotor_command("C")
        if response is None:
            raise TimeoutError("No response to position query")

        if self.protocol == Protocol.GS232B:
            # Expected: AZ=aaa
            m = re.match(r"AZ=(\d+)", response)
            if m:
                return float(m.group(1))
        else:
            # GS232A: +0aaa
            m = re.match(r"\+0(\d+)", response)
            if m:
                return float(m.group(1))

        raise ValueError(f"Cannot parse AZ response: {response!r}")

    def get_elevation(self) -> float:
        """Request current elevation position in degrees."""
        if self.protocol == Protocol.DCU1:
            raise NotImplementedError("DCU-1 does not support elevation")

        response = self.conn.send_rotor_command("B")
        if response is None:
            raise TimeoutError("No response to elevation query")

        if self.protocol == Protocol.GS232B:
            m = re.match(r"EL=(\d+)", response)
            if m:
                return float(m.group(1))
        else:
            m = re.match(r"\+0(\d+)", response)
            if m:
                return float(m.group(1))

        raise ValueError(f"Cannot parse EL response: {response!r}")

    def get_position(self) -> Position:
        """Request both azimuth and elevation positions."""
        if self.protocol == Protocol.DCU1:
            return Position(azimuth=self.get_azimuth(), elevation=None)

        response = self.conn.send_rotor_command("C2")
        if response is None:
            raise TimeoutError("No response to position query")

        if self.protocol == Protocol.GS232B:
            # AZ=aaa  EL=eee
            m = re.match(r"AZ=(\d+)\s+EL=(\d+)", response)
            if m:
                return Position(
                    azimuth=float(m.group(1)),
                    elevation=float(m.group(2)),
                )
        else:
            # +0aaa+0eee
            m = re.match(r"\+0(\d+)\+0(\d+)", response)
            if m:
                return Position(
                    azimuth=float(m.group(1)),
                    elevation=float(m.group(2)),
                )

        raise ValueError(f"Cannot parse C2 response: {response!r}")

    # ---- Movement commands ----

    def rotate_to_azimuth(self, degrees: int) -> None:
        """Rotate azimuth to a specific bearing (0-450)."""
        if not 0 <= degrees <= 450:
            raise ValueError(f"Azimuth must be 0-450, got {degrees}")
        if self.protocol == Protocol.DCU1:
            self.conn.send_rotor_command(f"AP1{degrees:03d};")
            self.conn.send_rotor_command("AM1;")
        else:
            self.conn.send_rotor_command(f"M{degrees:03d}")

    def rotate_to_elevation(self, degrees: int) -> None:
        """Rotate elevation to a specific angle (0-180)."""
        if self.protocol == Protocol.DCU1:
            raise NotImplementedError("DCU-1 does not support elevation")
        if not 0 <= degrees <= 180:
            raise ValueError(f"Elevation must be 0-180, got {degrees}")
        # GS232 W command sets both, but we can use it with current AZ
        # For EL-only, there's no single-axis command in GS232
        # We'll use W with current AZ position
        az = self.get_azimuth()
        self.conn.send_rotor_command(f"W{int(az):03d} {degrees:03d}")

    def rotate_to(self, azimuth: int, elevation: int) -> None:
        """Rotate both axes to specified positions."""
        if self.protocol == Protocol.DCU1:
            self.rotate_to_azimuth(azimuth)
            return
        if not 0 <= azimuth <= 450:
            raise ValueError(f"Azimuth must be 0-450, got {azimuth}")
        if not 0 <= elevation <= 180:
            raise ValueError(f"Elevation must be 0-180, got {elevation}")
        self.conn.send_rotor_command(f"W{azimuth:03d} {elevation:03d}")

    # ---- Manual rotation ----

    def rotate_cw(self) -> None:
        """Start continuous clockwise rotation."""
        if self.protocol == Protocol.DCU1:
            self.conn.send_rotor_command("U")
        else:
            self.conn.send_rotor_command("R")

    def rotate_ccw(self) -> None:
        """Start continuous counter-clockwise rotation."""
        if self.protocol == Protocol.DCU1:
            self.conn.send_rotor_command("D")
        else:
            self.conn.send_rotor_command("L")

    def rotate_up(self) -> None:
        """Start continuous upward elevation rotation."""
        if self.protocol == Protocol.DCU1:
            raise NotImplementedError("DCU-1 does not support elevation")
        self.conn.send_rotor_command("U")

    def rotate_down(self) -> None:
        """Start continuous downward elevation rotation."""
        if self.protocol == Protocol.DCU1:
            raise NotImplementedError("DCU-1 does not support elevation")
        self.conn.send_rotor_command("D")

    # ---- Stop commands ----

    def stop_all(self) -> None:
        """Stop all rotation (both axes)."""
        if self.protocol == Protocol.DCU1:
            self.conn.send_rotor_command("AS1;")
        else:
            self.conn.send_rotor_command("S")

    def stop_azimuth(self) -> None:
        """Stop azimuth rotation only."""
        if self.protocol == Protocol.DCU1:
            self.conn.send_rotor_command(";")
        else:
            self.conn.send_rotor_command("A")

    def stop_elevation(self) -> None:
        """Stop elevation rotation only."""
        if self.protocol == Protocol.DCU1:
            return  # DCU-1 has no elevation
        self.conn.send_rotor_command("E")

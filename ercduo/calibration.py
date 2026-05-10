"""Automated calibration routines for ERC-DUO.

Drives the rotator to its mechanical end-stops, detects when movement
has ceased (position stable), and records the ADC values via the
ERC calibration commands.

The user only needs to specify the expected bearing range for each axis
(e.g. AZ: 0-360, EL: 0-180). Per manual, AZ range is 0-360
(enter 90 for a 450° overlap rotator at CW limit). The rest is fully automated.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TYPE_CHECKING

from .models import Axis, CalibrationData
from .verification import StorageCheckResult, verify_calibration_storage

if TYPE_CHECKING:
    from .api import ERCAPI
    from .control import RotorControl

log = logging.getLogger(__name__)


class CalibrationState(Enum):
    """States in the automated calibration sequence."""
    IDLE = "idle"
    MOVING_TO_MIN = "moving_to_min"
    SETTLING_MIN = "settling_min"
    RECORDING_MIN = "recording_min"
    MOVING_TO_MAX = "moving_to_max"
    SETTLING_MAX = "settling_max"
    RECORDING_MAX = "recording_max"
    COMPLETE = "complete"
    ABORTED = "aborted"


@dataclass
class CalibrationProgress:
    """Progress update passed to callbacks during calibration."""
    state: CalibrationState
    axis: Axis
    message: str
    position: float | None = None
    elapsed_seconds: float = 0.0


# How we detect "stopped": position must not change by more than
# this many degrees over `stable_count` consecutive readings.
DEFAULT_STABLE_THRESHOLD = 1.0   # degrees
DEFAULT_STABLE_COUNT = 5         # consecutive stable readings
DEFAULT_POLL_INTERVAL = 1.0      # seconds between position polls
DEFAULT_TIMEOUT = 300.0          # max seconds to wait for a limit


class CalibrationRunner:
    """Automated end-to-end calibration for one axis.

    Procedure for AZ:
      1. Rotate CCW continuously until mechanical stop detected
      2. Record position → sCL1 (ERC stores ADC at this position)
      3. Rotate CW continuously until mechanical stop detected
      4. Record position → sCR1 (ERC stores ADC at this position)

    Procedure for EL:
      1. Rotate DOWN continuously until mechanical stop detected
      2. Record position → sCL2
      3. Rotate UP continuously until mechanical stop detected
      4. Record position → sCR2

    End-stop detection: position is polled every `poll_interval` seconds.
    When the position changes by less than `stable_threshold` degrees
    for `stable_count` consecutive readings, the rotor is considered stopped.

    Args:
        api: ERCAPI instance for sending calibration commands.
        control: RotorControl instance for movement commands.
        stable_threshold: Max position change (°) to count as "stable".
        stable_count: Number of consecutive stable reads to confirm stop.
        poll_interval: Seconds between position polls.
        timeout: Max seconds to wait for a limit before aborting.
        progress_callback: Optional callback for status updates.
    """

    def __init__(
        self,
        api: ERCAPI,
        control: RotorControl,
        *,
        stable_threshold: float = DEFAULT_STABLE_THRESHOLD,
        stable_count: int = DEFAULT_STABLE_COUNT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
        progress_callback: Callable[[CalibrationProgress], None] | None = None,
    ) -> None:
        self.api = api
        self.control = control
        self.stable_threshold = stable_threshold
        self.stable_count = stable_count
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._progress = progress_callback
        self._abort = False

    def abort(self) -> None:
        """Request calibration abort (checked between polls)."""
        self._abort = True

    def _notify(self, state: CalibrationState, axis: Axis, msg: str,
                position: float | None = None, elapsed: float = 0.0) -> None:
        log.info("[%s] %s: %s (pos=%s)", state.value, axis.name, msg, position)
        if self._progress:
            self._progress(CalibrationProgress(
                state=state, axis=axis, message=msg,
                position=position, elapsed_seconds=elapsed,
            ))

    def _read_position(self, axis: Axis) -> float:
        """Read current position for the given axis."""
        if axis == Axis.AZ:
            return self.control.get_azimuth()
        else:
            return self.control.get_elevation()

    def _wait_for_stop(self, axis: Axis, state: CalibrationState) -> float | None:
        """Poll position until the rotor stops moving.

        Returns the final stable position, or None if timed out / aborted.
        """
        consecutive_stable = 0
        last_position: float | None = None
        start = time.monotonic()

        while True:
            if self._abort:
                self.control.stop_all()
                self._notify(CalibrationState.ABORTED, axis, "Aborted by user")
                return None

            elapsed = time.monotonic() - start
            if elapsed > self.timeout:
                self.control.stop_all()
                self._notify(CalibrationState.ABORTED, axis,
                             f"Timeout after {self.timeout:.0f}s")
                return None

            try:
                position = self._read_position(axis)
            except Exception as e:
                log.warning("Position read failed: %s", e)
                time.sleep(self.poll_interval)
                continue

            self._notify(state, axis,
                         f"Position: {position:.1f}° (stable: {consecutive_stable}/{self.stable_count})",
                         position=position, elapsed=elapsed)

            if last_position is not None:
                delta = abs(position - last_position)
                if delta <= self.stable_threshold:
                    consecutive_stable += 1
                else:
                    consecutive_stable = 0

            last_position = position

            if consecutive_stable >= self.stable_count:
                return position

            time.sleep(self.poll_interval)

    def calibrate(self, axis: Axis, angle_min: int, angle_max: int) -> CalibrationData | None:
        """Run full automated calibration for one axis.

        Args:
            axis: Which axis to calibrate (AZ or EL).
            angle_min: The bearing at the minimum limit (e.g. 0).
            angle_max: The bearing at the maximum limit (e.g. 360 for AZ, or 90 for 450° overlap, 180 for EL).

        Returns:
            CalibrationData read back after calibration, or None if aborted.
        """
        self._abort = False
        axis_label = axis.name

        # ── Phase 1: Drive to minimum limit ──────────────────────────
        self._notify(CalibrationState.MOVING_TO_MIN, axis,
                     f"Driving {axis_label} to {'CCW' if axis == Axis.AZ else 'DOWN'} limit...")

        if axis == Axis.AZ:
            self.control.rotate_ccw()
        else:
            self.control.rotate_down()

        # Wait until stopped
        self._notify(CalibrationState.SETTLING_MIN, axis,
                     "Waiting for end-stop...")

        min_position = self._wait_for_stop(axis, CalibrationState.SETTLING_MIN)
        self.control.stop_all()

        if min_position is None:
            return None

        # Give an extra settle after stop
        time.sleep(2.0)

        # ── Phase 2: Record minimum ──────────────────────────────────
        self._notify(CalibrationState.RECORDING_MIN, axis,
                     f"Recording {axis_label} minimum at {min_position:.1f}° → bearing {angle_min}°",
                     position=min_position)

        self.api.set_calibration_angle_min(axis, angle_min)

        log.info("%s min recorded: physical=%.1f° → bearing=%d°",
                 axis_label, min_position, angle_min)

        # Brief pause before reversing direction
        time.sleep(3.0)

        # ── Phase 3: Drive to maximum limit ──────────────────────────
        self._notify(CalibrationState.MOVING_TO_MAX, axis,
                     f"Driving {axis_label} to {'CW' if axis == Axis.AZ else 'UP'} limit...")

        if axis == Axis.AZ:
            self.control.rotate_cw()
        else:
            self.control.rotate_up()

        self._notify(CalibrationState.SETTLING_MAX, axis,
                     "Waiting for end-stop...")

        max_position = self._wait_for_stop(axis, CalibrationState.SETTLING_MAX)
        self.control.stop_all()

        if max_position is None:
            return None

        time.sleep(2.0)

        # ── Phase 4: Record maximum ──────────────────────────────────
        self._notify(CalibrationState.RECORDING_MAX, axis,
                     f"Recording {axis_label} maximum at {max_position:.1f}° → bearing {angle_max}°",
                     position=max_position)

        self.api.set_calibration_angle_max(axis, angle_max)

        log.info("%s max recorded: physical=%.1f° → bearing=%d°",
                 axis_label, max_position, angle_max)

        # ── Read back and verify storage ────────────────────────────
        self._notify(CalibrationState.COMPLETE, axis,
                     "Calibration complete — verifying EEPROM storage...")

        storage_check = verify_calibration_storage(
            self.api, axis,
            expected_angle_min=angle_min,
            expected_angle_max=angle_max,
        )
        self._notify(CalibrationState.COMPLETE, axis,
                     storage_check.summary())

        if not storage_check.all_passed:
            log.warning("%s calibration storage verification FAILED", axis_label)

        result = self.api.read_calibration(axis)
        return result

    def calibrate_both(
        self,
        az_min: int = 0,
        az_max: int = 360,
        el_min: int = 0,
        el_max: int = 180,
    ) -> tuple[CalibrationData | None, CalibrationData | None]:
        """Calibrate both axes sequentially.

        Returns:
            Tuple of (az_calibration, el_calibration). Either may be None if aborted.
        """
        az_cal = self.calibrate(Axis.AZ, az_min, az_max)
        if az_cal is None:
            return (None, None)

        el_cal = self.calibrate(Axis.EL, el_min, el_max)
        return (az_cal, el_cal)


# ── Manual (User-Guided) Calibration ─────────────────────────────────────


@dataclass
class ManualCalibrationPoint:
    """Result of recording one calibration endpoint."""
    axis: Axis
    label: str           # e.g. "CCW limit", "CW limit"
    user_bearing: int    # bearing entered by user
    reported_position: float | None  # position reported by ERC at time of recording


@dataclass
class ManualCalibrationResult:
    """Result of a full manual calibration for one axis."""
    axis: Axis
    point_min: ManualCalibrationPoint
    point_max: ManualCalibrationPoint
    calibration: CalibrationData        # read-back from device after recording
    storage_check: StorageCheckResult   # EEPROM verification

    @property
    def stored_ok(self) -> bool:
        return self.storage_check.all_passed

    def summary(self) -> str:
        lines = [
            f"Manual Calibration Result: {self.axis.name}",
            f"  Min: {self.point_min.label} → {self.point_min.user_bearing}°"
            f"  (ERC reported: {self.point_min.reported_position}°)",
            f"  Max: {self.point_max.label} → {self.point_max.user_bearing}°"
            f"  (ERC reported: {self.point_max.reported_position}°)",
            f"  Stored ADC: {self.calibration.adc_min} — {self.calibration.adc_max}"
            f"  (span: {self.calibration.adc_range})",
            f"  Stored angles: {self.calibration.angle_min}° — {self.calibration.angle_max}°",
            f"  EEPROM check: {'PASS' if self.stored_ok else 'FAIL'}",
        ]
        return "\n".join(lines)


class ManualCalibration:
    """User-guided calibration where the operator positions the rotator.

    Unlike CalibrationRunner which drives to end-stops automatically,
    this class lets the user physically position the rotator (using
    the control box, or jog commands from this tool), confirm the
    actual bearing from the control head or a compass, then record
    the calibration point.

    The ERC sCL/sCR commands capture the ADC value at the *current*
    physical position and store it alongside the bearing label.

    Args:
        api: ERCAPI instance for calibration commands.
        control: RotorControl instance for position reads and jog commands.
    """

    def __init__(self, api: ERCAPI, control: RotorControl) -> None:
        self.api = api
        self.control = control

    def read_current_position(self, axis: Axis) -> float:
        """Read current reported position for an axis."""
        if axis == Axis.AZ:
            return self.control.get_azimuth()
        return self.control.get_elevation()

    def jog_positive(self, axis: Axis) -> None:
        """Start continuous rotation in positive direction (CW / UP)."""
        if axis == Axis.AZ:
            self.control.rotate_cw()
        else:
            self.control.rotate_up()

    def jog_negative(self, axis: Axis) -> None:
        """Start continuous rotation in negative direction (CCW / DOWN)."""
        if axis == Axis.AZ:
            self.control.rotate_ccw()
        else:
            self.control.rotate_down()

    def stop(self) -> None:
        """Stop all rotation."""
        self.control.stop_all()

    def record_min(self, axis: Axis, bearing: int) -> ManualCalibrationPoint:
        """Record the minimum calibration point at the current position.

        The ERC reads the ADC at this instant and stores it with the bearing.

        Args:
            axis: AZ or EL.
            bearing: The true bearing at this position (from control head / compass).

        Returns:
            ManualCalibrationPoint with the recorded data.
        """
        try:
            pos = self.read_current_position(axis)
        except Exception:
            pos = None

        self.api.set_calibration_angle_min(axis, bearing)

        label = "CCW limit" if axis == Axis.AZ else "DWN limit"
        log.info("Recorded %s %s: bearing=%d° (reported pos=%s)",
                 axis.name, label, bearing, pos)

        return ManualCalibrationPoint(
            axis=axis, label=label,
            user_bearing=bearing, reported_position=pos,
        )

    def record_max(self, axis: Axis, bearing: int) -> ManualCalibrationPoint:
        """Record the maximum calibration point at the current position.

        Args:
            axis: AZ or EL.
            bearing: The true bearing at this position.

        Returns:
            ManualCalibrationPoint with the recorded data.
        """
        try:
            pos = self.read_current_position(axis)
        except Exception:
            pos = None

        self.api.set_calibration_angle_max(axis, bearing)

        label = "CW limit" if axis == Axis.AZ else "UP limit"
        log.info("Recorded %s %s: bearing=%d° (reported pos=%s)",
                 axis.name, label, bearing, pos)

        return ManualCalibrationPoint(
            axis=axis, label=label,
            user_bearing=bearing, reported_position=pos,
        )

    def calibrate(self, axis: Axis, bearing_min: int, bearing_max: int,
                  ) -> ManualCalibrationResult:
        """Full manual calibration assuming the user has already positioned
        the rotator and called record_min/record_max.

        This is a convenience for programmatic use — the TUI wizard
        calls record_min/record_max interactively with jog controls
        in between.
        """
        self.stop()

        point_min = self.record_min(axis, bearing_min)
        # Caller must move rotator between these calls
        point_max = self.record_max(axis, bearing_max)

        return self._finalize(axis, bearing_min, bearing_max, point_min, point_max)

    def _finalize(self, axis: Axis, bearing_min: int, bearing_max: int,
                  point_min: ManualCalibrationPoint,
                  point_max: ManualCalibrationPoint) -> ManualCalibrationResult:
        """Read back calibration and verify EEPROM storage."""
        cal = self.api.read_calibration(axis)

        storage_check = verify_calibration_storage(
            self.api, axis,
            expected_angle_min=bearing_min,
            expected_angle_max=bearing_max,
        )

        return ManualCalibrationResult(
            axis=axis,
            point_min=point_min,
            point_max=point_max,
            calibration=cal,
            storage_check=storage_check,
        )

"""Calibration verification utilities for ERC-DUO.

Provides automated routines to verify rotator calibration by commanding
specific positions and comparing reported vs. expected values.
Also provides EEPROM storage verification to confirm calibration data
was persisted to the device.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .models import Axis, CalibrationData, Position

if TYPE_CHECKING:
    from .api import ERCAPI
    from .control import RotorControl

log = logging.getLogger(__name__)


def _angular_error(reported: float, target: float, axis: Axis) -> float:
    """Compute signed angular error, wrapping at 360° for AZ."""
    error = reported - target
    if axis == Axis.AZ:
        # Normalize to [-180, +180] so 0° vs 360° gives ~0° error
        while error > 180:
            error -= 360
        while error < -180:
            error += 360
    return error


@dataclass
class VerificationPoint:
    """A single calibration verification measurement."""
    target_degrees: int
    reported_degrees: float
    error_degrees: float
    axis: Axis
    timestamp: float = 0.0

    @property
    def passed(self) -> bool:
        return abs(self.error_degrees) <= self.tolerance

    tolerance: float = 5.0  # configurable threshold


@dataclass
class VerificationResult:
    """Full calibration verification report."""
    axis: Axis
    points: list[VerificationPoint] = field(default_factory=list)

    @property
    def max_error(self) -> float:
        if not self.points:
            return 0.0
        return max(abs(p.error_degrees) for p in self.points)

    @property
    def mean_error(self) -> float:
        if not self.points:
            return 0.0
        return sum(abs(p.error_degrees) for p in self.points) / len(self.points)

    @property
    def all_passed(self) -> bool:
        return all(p.passed for p in self.points)

    def summary(self) -> str:
        lines = [
            f"Calibration Verification: {self.axis.name}",
            f"  Points tested: {len(self.points)}",
            f"  Max error:     {self.max_error:.1f}°",
            f"  Mean error:    {self.mean_error:.1f}°",
            f"  Status:        {'PASS' if self.all_passed else 'FAIL'}",
            "",
        ]
        for p in self.points:
            status = "OK" if p.passed else "FAIL"
            lines.append(
                f"  {p.target_degrees:>4d}° → {p.reported_degrees:>5.1f}° "
                f"(err: {p.error_degrees:+.1f}°) [{status}]"
            )
        return "\n".join(lines)


class CalibrationVerifier:
    """Automated calibration verification tool.

    Rotates to known positions and checks that reported position
    matches the expected target within a given tolerance.

    Waits for the rotator to reach each target (position stable)
    before reading back the final position.

    Args:
        api: ERCAPI instance for reading calibration data.
        control: RotorControl instance for commanding rotation.
        settle_time: Seconds to wait after position stabilises
            before reading back. Allows final mechanical settle.
        tolerance: Maximum acceptable error in degrees.
        poll_interval: Seconds between position polls while waiting.
        stable_count: Number of consecutive stable reads required.
        stable_threshold: Max degrees change between reads to count as stable.
        move_timeout: Max seconds to wait for the rotator to arrive.
    """

    def __init__(
        self,
        api: ERCAPI,
        control: RotorControl,
        settle_time: float = 3.0,
        tolerance: float = 5.0,
        poll_interval: float = 2.0,
        stable_count: int = 3,
        stable_threshold: float = 1.0,
        move_timeout: float = 180.0,
    ) -> None:
        self.api = api
        self.control = control
        self.settle_time = settle_time
        self.tolerance = tolerance
        self.poll_interval = poll_interval
        self.stable_count = stable_count
        self.stable_threshold = stable_threshold
        self.move_timeout = move_timeout

    def _wait_for_position(self, axis: Axis, target: int) -> float | None:
        """Poll until the rotator stops moving (position stable).

        Returns the final stable position, or None if timed out.
        """
        consecutive_stable = 0
        last_position: float | None = None
        start = time.monotonic()

        while True:
            elapsed = time.monotonic() - start
            if elapsed > self.move_timeout:
                log.warning("Timed out waiting for %s to reach %d° (%.0fs)",
                            axis.name, target, elapsed)
                return None

            try:
                if axis == Axis.AZ:
                    position = self.control.get_azimuth()
                else:
                    position = self.control.get_elevation()
            except Exception as e:
                log.warning("Position read failed: %s", e)
                time.sleep(self.poll_interval)
                continue

            if last_position is not None:
                delta = abs(position - last_position)
                if delta <= self.stable_threshold:
                    consecutive_stable += 1
                else:
                    consecutive_stable = 0

            last_position = position

            if consecutive_stable >= self.stable_count:
                # Position stable — apply post-settle delay
                time.sleep(self.settle_time)
                # Final read after settle
                if axis == Axis.AZ:
                    return self.control.get_azimuth()
                else:
                    return self.control.get_elevation()

            time.sleep(self.poll_interval)

    def verify_azimuth(
        self,
        test_points: list[int] | None = None,
        progress_callback=None,
    ) -> VerificationResult:
        """Run azimuth calibration verification.

        Args:
            test_points: List of azimuth bearings to test.
                Defaults to [0, 90, 180, 270, 360].
            progress_callback: Optional callable(target, step, total)
                called before each test point.

        Returns:
            VerificationResult with all measurements.
        """
        if test_points is None:
            cal = self.api.read_calibration(Axis.AZ)
            test_points = self._generate_test_points(
                cal.angle_min, cal.angle_max
            )

        result = VerificationResult(axis=Axis.AZ)
        total = len(test_points)

        for i, target in enumerate(test_points):
            if progress_callback:
                progress_callback(target, i + 1, total)

            log.info("Verifying AZ %d° (%d/%d)", target, i + 1, total)
            self.control.rotate_to_azimuth(target)

            reported = self._wait_for_position(Axis.AZ, target)
            if reported is None:
                log.warning("Timed out waiting for AZ %d°", target)
                reported = self.control.get_azimuth()

            error = _angular_error(reported, target, Axis.AZ)

            point = VerificationPoint(
                target_degrees=target,
                reported_degrees=reported,
                error_degrees=error,
                axis=Axis.AZ,
                timestamp=time.time(),
                tolerance=self.tolerance,
            )
            result.points.append(point)

        return result

    def verify_elevation(
        self,
        test_points: list[int] | None = None,
        progress_callback=None,
    ) -> VerificationResult:
        """Run elevation calibration verification.

        Args:
            test_points: List of elevation angles to test.
                Defaults to [0, 45, 90, 135, 180].
            progress_callback: Optional callable(target, step, total).

        Returns:
            VerificationResult with all measurements.
        """
        if test_points is None:
            cal = self.api.read_calibration(Axis.EL)
            test_points = self._generate_test_points(
                cal.angle_min, cal.angle_max
            )

        result = VerificationResult(axis=Axis.EL)
        total = len(test_points)

        for i, target in enumerate(test_points):
            if progress_callback:
                progress_callback(target, i + 1, total)

            log.info("Verifying EL %d° (%d/%d)", target, i + 1, total)
            self.control.rotate_to_elevation(target)

            reported = self._wait_for_position(Axis.EL, target)
            if reported is None:
                log.warning("Timed out waiting for EL %d°", target)
                reported = self.control.get_elevation()

            error = _angular_error(reported, target, Axis.EL)

            point = VerificationPoint(
                target_degrees=target,
                reported_degrees=reported,
                error_degrees=error,
                axis=Axis.EL,
                timestamp=time.time(),
                tolerance=self.tolerance,
            )
            result.points.append(point)

        return result

    def verify_single_point(self, axis: Axis, target: int) -> VerificationPoint:
        """Verify a single position on the given axis.

        Useful for quick spot-checks without a full sweep.
        """
        if axis == Axis.AZ:
            self.control.rotate_to_azimuth(target)
        else:
            self.control.rotate_to_elevation(target)

        reported = self._wait_for_position(axis, target)
        if reported is None:
            log.warning("Timed out waiting for %s %d°", axis.name, target)
            if axis == Axis.AZ:
                reported = self.control.get_azimuth()
            else:
                reported = self.control.get_elevation()

        return VerificationPoint(
            target_degrees=target,
            reported_degrees=reported,
            error_degrees=_angular_error(reported, target, axis),
            axis=axis,
            timestamp=time.time(),
            tolerance=self.tolerance,
        )

    @staticmethod
    def _generate_test_points(angle_min: int, angle_max: int, count: int = 5) -> list[int]:
        """Generate evenly-spaced test points across the calibration range."""
        if count < 2:
            return [angle_min]
        step = (angle_max - angle_min) / (count - 1)
        return [round(angle_min + i * step) for i in range(count)]


# ── EEPROM Storage Verification ──────────────────────────────────────────


@dataclass
class StorageCheckField:
    """Result of verifying one stored calibration parameter."""
    parameter: str
    expected: int | None
    actual: int

    @property
    def matches(self) -> bool:
        if self.expected is None:
            return True  # No expectation — informational only
        return self.expected == self.actual


@dataclass
class StorageCheckResult:
    """Full EEPROM calibration storage verification report."""
    axis: Axis
    fields: list[StorageCheckField] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(f.matches for f in self.fields)

    def summary(self) -> str:
        lines = [
            f"Storage Verification: {self.axis.name}",
            f"  Status: {'PASS' if self.all_passed else 'FAIL'}",
            "",
        ]
        for f in self.fields:
            if f.expected is None:
                status = "INFO"
                lines.append(f"  {f.parameter:<24} = {f.actual:>6}  [{status}]")
            else:
                status = "OK" if f.matches else "MISMATCH"
                lines.append(
                    f"  {f.parameter:<24} expected={f.expected:>6}  "
                    f"actual={f.actual:>6}  [{status}]"
                )
        return "\n".join(lines)


def verify_calibration_storage(
    api: ERCAPI,
    axis: Axis,
    expected_angle_min: int | None = None,
    expected_angle_max: int | None = None,
) -> StorageCheckResult:
    """Read calibration data from device EEPROM and verify against expected values.

    Checks that the angle bearings and ADC values are stored and sensible.
    If expected angles are provided, verifies they match.
    ADC values are checked for basic sanity (min < max, within 0-1023).

    This is a non-destructive, no-movement operation — safe to run anytime.

    Args:
        api: ERCAPI instance for reading device parameters.
        axis: Which axis to check.
        expected_angle_min: If provided, verify the stored min angle matches.
        expected_angle_max: If provided, verify the stored max angle matches.

    Returns:
        StorageCheckResult with per-field pass/fail.
    """
    cal = api.read_calibration(axis)
    result = StorageCheckResult(axis=axis)

    result.fields.append(StorageCheckField(
        parameter=f"{axis.name} angle min",
        expected=expected_angle_min,
        actual=cal.angle_min,
    ))
    result.fields.append(StorageCheckField(
        parameter=f"{axis.name} angle max",
        expected=expected_angle_max,
        actual=cal.angle_max,
    ))

    # ADC values — always informational (we don't know the expected ADC)
    # but we validate sanity: both in range, and min < max
    result.fields.append(StorageCheckField(
        parameter=f"{axis.name} ADC min",
        expected=None,
        actual=cal.adc_min,
    ))
    result.fields.append(StorageCheckField(
        parameter=f"{axis.name} ADC max",
        expected=None,
        actual=cal.adc_max,
    ))

    # Sanity checks as synthetic fields
    adc_valid = 0 <= cal.adc_min < cal.adc_max <= 1023
    result.fields.append(StorageCheckField(
        parameter=f"{axis.name} ADC range valid",
        expected=1,  # 1 = True
        actual=1 if adc_valid else 0,
    ))

    adc_range = cal.adc_max - cal.adc_min
    result.fields.append(StorageCheckField(
        parameter=f"{axis.name} ADC span",
        expected=None,
        actual=adc_range,
    ))

    return result

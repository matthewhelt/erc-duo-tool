"""Terminal User Interface for ERC-DUO configuration, calibration and control.

Simple menu-driven TUI using ANSI escape codes for formatting.
No external dependencies beyond pyserial.
"""

from __future__ import annotations

import os
import sys
import logging
from typing import Callable

from .protocol import ERCConnection, ERCProtocolError, ERCTimeoutError
from .api import ERCAPI
from .control import RotorControl
from .verification import CalibrationVerifier, VerificationResult
from .models import (
    Axis, Protocol, ConfigData, CalibrationData, DeviceInfo,
    SPEED_ANGLE_VALUES, PROTOCOL_NAMES,
)
from .calibration import CalibrationRunner, CalibrationState, ManualCalibration

log = logging.getLogger(__name__)

# ── ANSI helpers ──────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"


def _clear() -> None:
    os.system("clear" if os.name != "nt" else "cls")


def _header(title: str) -> None:
    width = 60
    print(f"\n{BOLD}{CYAN}{'─' * width}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * width}{RESET}\n")


def _info(label: str, value: str, unit: str = "") -> None:
    suffix = f" {unit}" if unit else ""
    print(f"  {DIM}{label:<28}{RESET}{BOLD}{value}{RESET}{suffix}")


def _success(msg: str) -> None:
    print(f"\n  {GREEN}✓ {msg}{RESET}")


def _error(msg: str) -> None:
    print(f"\n  {RED}✗ {msg}{RESET}")


def _warn(msg: str) -> None:
    print(f"\n  {YELLOW}⚠ {msg}{RESET}")


def _prompt(msg: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    result = input(f"\n  {msg}{hint}: ").strip()
    return result if result else default


def _prompt_int(msg: str, default: int | None = None, lo: int | None = None, hi: int | None = None) -> int | None:
    hint_parts = []
    if lo is not None and hi is not None:
        hint_parts.append(f"{lo}-{hi}")
    if default is not None:
        hint_parts.append(f"default={default}")
    hint = f" ({', '.join(hint_parts)})" if hint_parts else ""

    raw = input(f"  {msg}{hint}: ").strip()
    if not raw:
        return default

    try:
        val = int(raw)
    except ValueError:
        _error(f"Invalid integer: {raw!r}")
        return None

    if lo is not None and val < lo:
        _error(f"Value must be >= {lo}")
        return None
    if hi is not None and val > hi:
        _error(f"Value must be <= {hi}")
        return None
    return val


def _confirm(msg: str) -> bool:
    answer = input(f"  {msg} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _pause() -> None:
    input(f"\n  {DIM}Press Enter to continue...{RESET}")


def _menu(title: str, options: list[tuple[str, str]]) -> str:
    _header(title)
    for key, label in options:
        print(f"    {BOLD}{key}{RESET}  {label}")
    print()
    return input(f"  Select: ").strip().lower()


# ── Display helpers ───────────────────────────────────────────────────────

def _show_device_info(info: DeviceInfo) -> None:
    _header("Device Information")
    _info("Firmware:", info.firmware_version)
    _info("Baudrate:", str(info.baudrate), "baud")
    _info("Protocol:", PROTOCOL_NAMES.get(info.protocol, str(info.protocol)))


def _show_config(cfg: ConfigData) -> None:
    axis_name = cfg.axis.name
    _header(f"Configuration — {axis_name}")
    _info("Delay before move:", str(cfg.delay_before_move), "ms")
    _info("Antenna offset:", str(cfg.antenna_offset), "°")
    _info("Tolerance:", str(cfg.tolerance), "°")
    if cfg.axis == Axis.AZ:
        _info("Speed (low):", str(cfg.speed_low))
        _info("Speed (high):", str(cfg.speed_high))
        _info("Speed angle:", f"{cfg.speed_angle}°")


def _show_calibration(cal: CalibrationData) -> None:
    axis_name = cal.axis.name
    _header(f"Calibration Data — {axis_name}")
    _info("Angle (min / max):", f"{cal.angle_min}° / {cal.angle_max}°")
    _info("ADC   (min / max):", f"{cal.adc_min} / {cal.adc_max}")
    _info("Angle range:", f"{cal.angle_range}°")
    _info("ADC range:", str(cal.adc_range))


# ── Menus ─────────────────────────────────────────────────────────────────

class TUI:
    """Main TUI application controller."""

    def __init__(self, conn: ERCConnection) -> None:
        self.conn = conn
        self.api = ERCAPI(conn)
        self._device_info: DeviceInfo | None = None

    @property
    def control(self) -> RotorControl:
        proto = self._device_info.protocol if self._device_info else Protocol.GS232B
        return RotorControl(self.conn, proto)

    def run(self) -> None:
        """Main TUI loop."""
        _clear()
        print(f"\n{BOLD}{CYAN}  ERC-DUO Service Tool (Linux){RESET}")
        print(f"  {DIM}Connected to {self.conn.port} at {self.conn.baudrate} baud{RESET}")

        try:
            self._device_info = self.api.read_device_info()
            _show_device_info(self._device_info)
            _success("Connection established")
        except (ERCProtocolError, ERCTimeoutError) as e:
            _error(f"Cannot communicate with ERC-DUO: {e}")
            _warn("Check port, baudrate, and cable connections")
            return

        while True:
            choice = _menu("Main Menu", [
                ("1", "Configuration"),
                ("2", "Calibration"),
                ("3", "Rotor Control"),
                ("4", "Verification"),
                ("5", "Device Info (refresh)"),
                ("6", "Factory Reset"),
                ("q", "Quit"),
            ])
            try:
                if choice == "1":
                    self._config_menu()
                elif choice == "2":
                    self._calibration_menu()
                elif choice == "3":
                    self._control_menu()
                elif choice == "4":
                    self._verification_menu()
                elif choice == "5":
                    self._device_info = self.api.read_device_info()
                    _show_device_info(self._device_info)
                    _pause()
                elif choice == "6":
                    self._factory_reset()
                elif choice == "q":
                    print(f"\n  {DIM}73 de ERC-DUO Tool{RESET}\n")
                    break
            except (ERCProtocolError, ERCTimeoutError) as e:
                _error(f"Communication error: {e}")
                _pause()
            except KeyboardInterrupt:
                print()
                continue

    # ── Configuration ─────────────────────────────────────────────────────

    def _config_menu(self) -> None:
        while True:
            choice = _menu("Configuration", [
                ("1", "View AZ configuration"),
                ("2", "View EL configuration"),
                ("3", "Edit AZ configuration"),
                ("4", "Edit EL configuration"),
                ("5", "Set protocol"),
                ("6", "Set baudrate"),
                ("b", "Back"),
            ])
            if choice == "1":
                _show_config(self.api.read_config(Axis.AZ))
                _pause()
            elif choice == "2":
                _show_config(self.api.read_config(Axis.EL))
                _pause()
            elif choice == "3":
                self._edit_config(Axis.AZ)
            elif choice == "4":
                self._edit_config(Axis.EL)
            elif choice == "5":
                self._set_protocol()
            elif choice == "6":
                self._set_baudrate()
            elif choice == "b":
                break

    def _edit_config(self, axis: Axis) -> None:
        cfg = self.api.read_config(axis)
        _show_config(cfg)

        print(f"\n  {DIM}Enter new values (leave blank to keep current):{RESET}\n")

        val = _prompt_int("Delay before move", default=cfg.delay_before_move, lo=0, hi=5000)
        if val is not None and val != cfg.delay_before_move:
            self.api.set_delay_before_move(axis, val)
            _success(f"Delay set to {val} ms")

        if axis == Axis.AZ:
            offset_lo, offset_hi = -180, 180
        else:
            offset_lo, offset_hi = -90, 90

        val = _prompt_int("Antenna offset", default=cfg.antenna_offset, lo=offset_lo, hi=offset_hi)
        if val is not None and val != cfg.antenna_offset:
            self.api.set_antenna_offset(axis, val)
            _success(f"Antenna offset set to {val}°")

        val = _prompt_int("Tolerance", default=cfg.tolerance, lo=0, hi=10)
        if val is not None and val != cfg.tolerance:
            self.api.set_tolerance(axis, val)
            _success(f"Tolerance set to {val}°")

        if axis == Axis.AZ:
            val = _prompt_int("Low speed", default=cfg.speed_low, lo=1, hi=4)
            if val is not None and val != cfg.speed_low:
                self.api.set_speed_low(val)
                _success(f"Low speed set to {val}")

            val = _prompt_int("High speed", default=cfg.speed_high, lo=1, hi=4)
            if val is not None and val != cfg.speed_high:
                self.api.set_speed_high(val)
                _success(f"High speed set to {val}")

            print(f"\n  {DIM}Speed angle (degrees of low-speed at start/end of turn):{RESET}")
            print(f"  {DIM}Valid values: 0, 10, 20, 30{RESET}")
            val = _prompt_int("Speed angle (degrees)", default=cfg.speed_angle, lo=0, hi=30)
            if val is not None and val != cfg.speed_angle:
                if val not in SPEED_ANGLE_VALUES:
                    _error(f"Must be one of {SPEED_ANGLE_VALUES}")
                else:
                    self.api.set_speed_angle(val)
                    _success(f"Speed angle set to {val}°")

        # Read back and display
        print()
        _show_config(self.api.read_config(axis))
        _pause()

    def _set_protocol(self) -> None:
        _header("Set Protocol")
        for p in Protocol:
            marker = " ←" if self._device_info and p == self._device_info.protocol else ""
            print(f"    {p.value}  {PROTOCOL_NAMES[p]}{BOLD}{marker}{RESET}")

        val = _prompt_int("\n  Protocol", lo=0, hi=2)
        if val is not None:
            self.api.set_protocol(Protocol(val))
            self._device_info = self.api.read_device_info()
            _success(f"Protocol set to {PROTOCOL_NAMES[Protocol(val)]}")
            _pause()

    def _set_baudrate(self) -> None:
        _header("Set Baudrate")
        print(f"    {DIM}Current: {self._device_info.baudrate if self._device_info else '?'} baud{RESET}")
        print(f"    Options: 4800, 9600")

        val = _prompt_int("\n  Baudrate")
        if val in (4800, 9600):
            _warn("After changing baudrate, you must reconnect with the new speed.")
            if _confirm("Proceed?"):
                self.api.set_baudrate(val)
                _success(f"Baudrate set to {val}. Reconnect required.")
        elif val is not None:
            _error("Must be 4800 or 9600")
        _pause()

    def _factory_reset(self) -> None:
        _header("Factory Reset")
        _warn("This will reset ALL configuration and calibration to defaults!")
        _warn("This action cannot be undone!")
        if _confirm("Are you absolutely sure?"):
            self.api.factory_reset()
            self._device_info = self.api.read_device_info()
            _success("Factory defaults restored")
        else:
            print(f"  {DIM}Cancelled{RESET}")
        _pause()

    # ── Calibration ───────────────────────────────────────────────────────

    def _calibration_menu(self) -> None:
        while True:
            choice = _menu("Calibration", [
                ("1", "View AZ calibration"),
                ("2", "View EL calibration"),
                ("3", "Calibrate AZ (manual)"),
                ("4", "Calibrate EL (manual)"),
                ("5", "Auto-calibrate AZ"),
                ("6", "Auto-calibrate EL"),
                ("7", "Auto-calibrate BOTH"),
                ("b", "Back"),
            ])
            if choice == "1":
                _show_calibration(self.api.read_calibration(Axis.AZ))
                _pause()
            elif choice == "2":
                _show_calibration(self.api.read_calibration(Axis.EL))
                _pause()
            elif choice == "3":
                self._run_calibration(Axis.AZ)
            elif choice == "4":
                self._run_calibration(Axis.EL)
            elif choice == "5":
                self._auto_calibrate(Axis.AZ)
            elif choice == "6":
                self._auto_calibrate(Axis.EL)
            elif choice == "7":
                self._auto_calibrate_both()
            elif choice == "b":
                break

    def _run_calibration(self, axis: Axis) -> None:
        """User-guided calibration wizard with jog controls and live position."""
        manual = ManualCalibration(self.api, self.control)

        if axis == Axis.AZ:
            min_label, max_label = "CCW (counter-clockwise)", "CW (clockwise)"
            neg_key, pos_key = "CCW", "CW"
            default_min, default_max = 0, 360
            bearing_lo, bearing_hi = 0, 360
        else:
            min_label, max_label = "DOWN (lowest)", "UP (highest)"
            neg_key, pos_key = "DOWN", "UP"
            default_min, default_max = 0, 180
            bearing_lo, bearing_hi = 0, 180

        _header(f"Guided {axis.name} Calibration")
        print(f"  {BOLD}How this works:{RESET}")
        print(f"  1. You physically move the rotator to a known position")
        print(f"     using the jog controls below or the control box.")
        print(f"     {YELLOW}This tool will NOT move the rotator automatically.{RESET}")
        print(f"  2. When the rotator is where you want it, press 'r'.")
        print(f"  3. You tell the tool what bearing the rotator is actually at")
        print(f"     (read this from the control head dial or a compass).")
        print(f"  4. The ERC-DUO captures the feedback position at that instant")
        print(f"     and stores it alongside the bearing you entered.")
        print(f"  This is done twice: once at each end of the rotation range.\n")

        # ── Step 1: Position at minimum ──────────────────────────────
        _header(f"Step 1 of 2: Move to {axis.name} {min_label} limit")
        print(f"  {BOLD}Action:{RESET} Physically move the rotator to its {min_label} limit.")
        print(f"  Use + / - to jog, or move it from the control box.")
        print(f"  The rotator will {BOLD}not{RESET} move until you press a jog key.\n")

        point_min = self._jog_and_record(manual, axis, "min",
                                          neg_key, pos_key,
                                          default_min, bearing_lo, bearing_hi)
        if point_min is None:
            return

        # ── Step 2: Position at maximum ──────────────────────────────
        _header(f"Step 2 of 2: Move to {axis.name} {max_label} limit")
        print(f"  {BOLD}Action:{RESET} Now physically move the rotator to its {max_label} limit.")
        print(f"  Use + / - to jog, or move it from the control box.")
        print(f"  The rotator will {BOLD}not{RESET} move until you press a jog key.\n")

        point_max = self._jog_and_record(manual, axis, "max",
                                          neg_key, pos_key,
                                          default_max, bearing_lo, bearing_hi)
        if point_max is None:
            return

        # ── Finalize ─────────────────────────────────────────────────
        result = manual._finalize(axis,
                                   point_min.user_bearing,
                                   point_max.user_bearing,
                                   point_min, point_max)

        _header(f"{axis.name} Calibration Result")
        print(f"  {result.summary()}")

        if result.stored_ok:
            _success("Calibration stored and verified")
        else:
            _error("EEPROM verification failed — check results above")
            print(f"\n{result.storage_check.summary()}")

        _pause()

    def _jog_and_record(self, manual: ManualCalibration, axis: Axis,
                        endpoint: str, neg_key: str, pos_key: str,
                        default_bearing: int,
                        bearing_lo: int, bearing_hi: int):
        """Interactive jog-and-record loop for one calibration endpoint.

        Shows live position and provides motor controls until the user
        confirms the position and enters the true bearing.

        Returns:
            ManualCalibrationPoint, or None if cancelled.
        """
        print(f"  {DIM}Controls (rotator does NOT move until you press a jog key):{RESET}")
        print(f"    {BOLD}+{RESET} / {BOLD}>{RESET}  Start motor: jog {pos_key}")
        print(f"    {BOLD}-{RESET} / {BOLD}<{RESET}  Start motor: jog {neg_key}")
        print(f"    {BOLD}s{RESET}        Stop motor")
        print(f"    {BOLD}p{RESET}        Read position (does not move rotator)")
        print(f"    {BOLD}r{RESET}        Stop motor & record calibration point")
        print(f"            (you will be asked for the true bearing)")
        print(f"    {BOLD}q{RESET}        Cancel calibration\n")

        while True:
            try:
                pos = manual.read_current_position(axis)
                pos_str = f"{pos:.0f}°"
            except Exception:
                pos_str = "N/A"

            cmd = input(f"  {DIM}[ERC reports: {pos_str}]{RESET} > ").strip().lower()

            if cmd in ("+", ">", "cw", "up"):
                manual.jog_positive(axis)
                _info("Jogging", f"{pos_key}...")
            elif cmd in ("-", "<", "ccw", "down"):
                manual.jog_negative(axis)
                _info("Jogging", f"{neg_key}...")
            elif cmd == "s":
                manual.stop()
                _info("Stopped", "")
            elif cmd == "p":
                try:
                    pos = manual.read_current_position(axis)
                    _info(f"{axis.name} position:", f"{pos:.0f}°")
                except Exception as e:
                    _error(f"Read failed: {e}")
            elif cmd == "r":
                manual.stop()
                print()
                print(f"  {BOLD}Motor stopped.{RESET} The rotator is now stationary.")
                print(f"  The ERC-DUO will capture the feedback position at")
                print(f"  this exact position when you confirm below.\n")
                print(f"  {BOLD}What bearing is the rotator actually at right now?{RESET}")
                print(f"  Read this from the control head dial, or measure with")
                print(f"  a compass. Do NOT guess — accuracy matters here.")
                print(f"  For AZ with overlap (e.g. 450° rotator), enter the")
                print(f"  bearing shown on the dial (e.g. 90 at the CW stop).\n")

                bearing = _prompt_int(
                    "True bearing at this position",
                    default=default_bearing,
                    lo=bearing_lo,
                    hi=bearing_hi,
                )
                if bearing is None:
                    _error("Invalid bearing")
                    continue

                if not _confirm(f"Record {axis.name} {endpoint} = {bearing}°?"):
                    continue

                if endpoint == "min":
                    point = manual.record_min(axis, bearing)
                else:
                    point = manual.record_max(axis, bearing)

                _success(f"Recorded {axis.name} {endpoint}: {bearing}° "
                         f"(ERC position: {point.reported_position}°)")
                return point

            elif cmd == "q":
                manual.stop()
                print(f"  {DIM}Cancelled{RESET}")
                return None
            else:
                print(f"  {DIM}Unknown command: {cmd!r}{RESET}")

    def _auto_cal_progress(self, p) -> None:
        """Callback for auto-calibration status updates."""
        state_icon = {
            CalibrationState.MOVING_TO_MIN: "⟵",
            CalibrationState.SETTLING_MIN: "◉",
            CalibrationState.RECORDING_MIN: "✎",
            CalibrationState.MOVING_TO_MAX: "⟶",
            CalibrationState.SETTLING_MAX: "◉",
            CalibrationState.RECORDING_MAX: "✎",
            CalibrationState.COMPLETE: "✓",
            CalibrationState.ABORTED: "✗",
        }.get(p.state, "·")
        pos_str = f"{p.position:.1f}°" if p.position is not None else ""
        elapsed = f"{p.elapsed_seconds:.0f}s" if p.elapsed_seconds else ""
        # Use carriage return for in-place updates during settling
        if p.state in (CalibrationState.SETTLING_MIN, CalibrationState.SETTLING_MAX):
            print(f"    {state_icon} [{elapsed:>4s}] {p.message:<60s}", end="\r", flush=True)
        else:
            print(f"    {state_icon} {p.message} {pos_str}")

    def _auto_calibrate(self, axis: Axis) -> None:
        """Run automated calibration for one axis."""
        name = axis.name
        _header(f"Auto-Calibrate {name}")

        print(f"  This will automatically drive the {name} rotator to both")
        print(f"  mechanical end-stops and record the ADC values.")
        print()
        print(f"  {YELLOW}The rotator WILL move to its full limits!{RESET}")
        print(f"  {YELLOW}Ensure nothing is obstructing the antenna.{RESET}")
        print()

        if axis == Axis.AZ:
            angle_min = _prompt_int("Bearing at CCW limit", default=0, lo=0, hi=360)
            if angle_min is None:
                return
            angle_max = _prompt_int("Bearing at CW limit (e.g. 360, or 90 for 450° overlap)",
                                    default=360, lo=0, hi=360)
            if angle_max is None:
                return
        else:
            angle_min = _prompt_int("Angle at DOWN limit", default=0, lo=0, hi=180)
            if angle_min is None:
                return
            angle_max = _prompt_int("Angle at UP limit", default=180, lo=0, hi=180)
            if angle_max is None:
                return

        print(f"\n  Will calibrate {name}: {angle_min}° → {angle_max}°")
        _warn("Rotator will move now!")
        if not _confirm("Start auto-calibration?"):
            return

        runner = CalibrationRunner(
            self.api, self.control,
            progress_callback=self._auto_cal_progress,
        )

        try:
            result = runner.calibrate(axis, angle_min, angle_max)
        except KeyboardInterrupt:
            runner.abort()
            self.control.stop_all()
            _warn("Calibration aborted — rotator stopped")
            _pause()
            return

        print()  # clear the \r line
        if result is None:
            _error("Calibration did not complete")
        else:
            _show_calibration(result)
            _success(f"{name} auto-calibration complete")
        _pause()

    def _auto_calibrate_both(self) -> None:
        """Run automated calibration for both axes."""
        _header("Auto-Calibrate Both Axes")

        print(f"  This will calibrate AZ then EL by driving to all")
        print(f"  four mechanical end-stops automatically.")
        print()
        print(f"  {YELLOW}The rotator WILL move to its full limits!{RESET}")
        print(f"  {YELLOW}Ensure nothing is obstructing the antenna.{RESET}")
        print()

        az_min = _prompt_int("AZ bearing at CCW limit", default=0, lo=0, hi=360)
        if az_min is None:
            return
        az_max = _prompt_int("AZ bearing at CW limit (e.g. 360, or 90 for 450° overlap)",
                            default=360, lo=0, hi=360)
        if az_max is None:
            return
        el_min = _prompt_int("EL angle at DOWN limit", default=0, lo=0, hi=180)
        if el_min is None:
            return
        el_max = _prompt_int("EL angle at UP limit", default=180, lo=0, hi=180)
        if el_max is None:
            return

        print(f"\n  AZ: {az_min}° → {az_max}°")
        print(f"  EL: {el_min}° → {el_max}°")
        _warn("Rotator will move now!")
        if not _confirm("Start auto-calibration for both axes?"):
            return

        runner = CalibrationRunner(
            self.api, self.control,
            progress_callback=self._auto_cal_progress,
        )

        try:
            az_cal, el_cal = runner.calibrate_both(az_min, az_max, el_min, el_max)
        except KeyboardInterrupt:
            runner.abort()
            self.control.stop_all()
            _warn("Calibration aborted — rotator stopped")
            _pause()
            return

        print()
        if az_cal:
            _show_calibration(az_cal)
            _success("AZ auto-calibration complete")
        else:
            _error("AZ calibration did not complete")

        if el_cal:
            _show_calibration(el_cal)
            _success("EL auto-calibration complete")
        else:
            _error("EL calibration did not complete")
        _pause()

    # ── Rotor Control ─────────────────────────────────────────────────────

    def _control_menu(self) -> None:
        ctrl = self.control
        while True:
            choice = _menu("Rotor Control", [
                ("1", "Read position"),
                ("2", "Rotate to AZ bearing"),
                ("3", "Rotate to AZ + EL"),
                ("4", "Rotate CW"),
                ("5", "Rotate CCW"),
                ("6", "Rotate UP"),
                ("7", "Rotate DOWN"),
                ("s", "STOP ALL"),
                ("b", "Back"),
            ])
            try:
                if choice == "1":
                    pos = ctrl.get_position()
                    _header("Current Position")
                    if pos.azimuth is not None:
                        _info("Azimuth:", f"{pos.azimuth:.0f}", "°")
                    if pos.elevation is not None:
                        _info("Elevation:", f"{pos.elevation:.0f}", "°")
                    _pause()
                elif choice == "2":
                    val = _prompt_int("Target azimuth", lo=0, hi=450)
                    if val is not None:
                        ctrl.rotate_to_azimuth(val)
                        _success(f"Rotating to {val}°")
                        _pause()
                elif choice == "3":
                    az = _prompt_int("Target azimuth", lo=0, hi=450)
                    el = _prompt_int("Target elevation", lo=0, hi=180)
                    if az is not None and el is not None:
                        ctrl.rotate_to(az, el)
                        _success(f"Rotating to AZ={az}° EL={el}°")
                        _pause()
                elif choice == "4":
                    ctrl.rotate_cw()
                    _success("Rotating CW — press Enter then 's' to stop")
                    _pause()
                elif choice == "5":
                    ctrl.rotate_ccw()
                    _success("Rotating CCW — press Enter then 's' to stop")
                    _pause()
                elif choice == "6":
                    ctrl.rotate_up()
                    _success("Rotating UP — press Enter then 's' to stop")
                    _pause()
                elif choice == "7":
                    ctrl.rotate_down()
                    _success("Rotating DOWN — press Enter then 's' to stop")
                    _pause()
                elif choice == "s":
                    ctrl.stop_all()
                    _success("STOPPED")
                    _pause()
                elif choice == "b":
                    break
            except (NotImplementedError, ValueError, TimeoutError) as e:
                _error(str(e))
                _pause()

    # ── Verification ──────────────────────────────────────────────────────

    def _verification_menu(self) -> None:
        while True:
            choice = _menu("Calibration Verification", [
                ("1", "Quick AZ check (current position)"),
                ("2", "Quick EL check (current position)"),
                ("3", "Full AZ sweep verification"),
                ("4", "Full EL sweep verification"),
                ("5", "Custom single-point test"),
                ("b", "Back"),
            ])
            if choice == "1":
                self._quick_check(Axis.AZ)
            elif choice == "2":
                self._quick_check(Axis.EL)
            elif choice == "3":
                self._full_verification(Axis.AZ)
            elif choice == "4":
                self._full_verification(Axis.EL)
            elif choice == "5":
                self._single_point_verify()
            elif choice == "b":
                break

    def _quick_check(self, axis: Axis) -> None:
        """Read current position without moving the rotator."""
        ctrl = self.control
        _header(f"Quick {axis.name} Check")
        try:
            if axis == Axis.AZ:
                pos = ctrl.get_azimuth()
            else:
                pos = ctrl.get_elevation()
            _info(f"Current {axis.name}:", f"{pos:.0f}", "°")
            cal = self.api.read_calibration(axis)
            _info("Calibrated range:", f"{cal.angle_min}° — {cal.angle_max}°")
        except Exception as e:
            _error(str(e))
        _pause()

    def _full_verification(self, axis: Axis) -> None:
        _header(f"Full {axis.name} Calibration Verification")

        tolerance = _prompt_int("Tolerance (degrees)", default=5, lo=1, hi=20)
        if tolerance is None:
            return
        num_points = _prompt_int("Number of test points", default=5, lo=2, hi=10)
        if num_points is None:
            return

        verifier = CalibrationVerifier(
            self.api, self.control,
            tolerance=tolerance,
        )

        cal = self.api.read_calibration(axis)
        test_points = CalibrationVerifier._generate_test_points(
            cal.angle_min, cal.angle_max, num_points
        )

        print(f"\n  Test points: {test_points}")
        _warn(f"This will move the rotator to {num_points} positions!")
        print(f"  {DIM}The tool waits for the rotator to arrive at each point{RESET}")
        print(f"  {DIM}before reading back. This may take several minutes.{RESET}")

        if not _confirm("Proceed with verification?"):
            return

        def progress(target, step, total):
            print(f"\n  [{step}/{total}] Moving to {target}°, waiting for arrival...")

        if axis == Axis.AZ:
            result = verifier.verify_azimuth(test_points, progress_callback=progress)
        else:
            result = verifier.verify_elevation(test_points, progress_callback=progress)

        print(f"\n{result.summary()}")
        _pause()

    def _single_point_verify(self) -> None:
        _header("Single Point Verification")

        print(f"  {DIM}1=AZ, 2=EL{RESET}")
        axis_val = _prompt_int("Axis", lo=1, hi=2)
        if axis_val is None:
            return
        axis = Axis(axis_val)

        max_angle = 450 if axis == Axis.AZ else 180
        target = _prompt_int(f"Target {axis.name} bearing", lo=0, hi=max_angle)
        if target is None:
            return

        _warn(f"This will rotate {axis.name} to {target}°!")
        if not _confirm("Proceed?"):
            return

        verifier = CalibrationVerifier(
            self.api, self.control,
        )

        print(f"\n  Moving to {target}°, waiting for arrival...")
        point = verifier.verify_single_point(axis, target)

        _header("Result")
        _info("Target:", f"{point.target_degrees}°")
        _info("Reported:", f"{point.reported_degrees:.1f}°")
        _info("Error:", f"{point.error_degrees:+.1f}°")
        status = f"{GREEN}PASS{RESET}" if point.passed else f"{RED}FAIL{RESET}"
        print(f"\n  Status: {status}")
        _pause()

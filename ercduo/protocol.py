"""Low-level serial protocol for ERC-DUO communication.

Handles connection management and raw command send/receive
following the ERC-DUO API specification (Appendix 5).

API format:
  Read:  r<CMD>\r  →  a<CMD><VALUE>\r
  Set:   s<CMD><VALUE>\r  →  (no response; verify with read)
  Error: r-ERROR or s-ERROR
"""

from __future__ import annotations

import logging
import serial
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)

# Defaults from ERC-DUO spec
DEFAULT_BAUDRATE = 9600
DEFAULT_TIMEOUT = 2.0
CR = b"\r"
LF = b"\n"


class ERCProtocolError(Exception):
    """Raised when the ERC-DUO returns an error response."""


class ERCTimeoutError(Exception):
    """Raised when no response is received within the timeout."""


class ERCConnection:
    """Manages the serial connection to an ERC-DUO device.

    Use as a context manager or call open()/close() manually.

    Example::

        with ERCConnection("/dev/ttyUSB0") as conn:
            response = conn.read_param("FMW")
    """

    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: serial.Serial | None = None

    # -- lifecycle --

    def open(self) -> None:
        if self._serial and self._serial.is_open:
            return
        log.info("Opening %s at %d baud", self.port, self.baudrate)
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )
        # Flush any stale data
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            log.info("Closing %s", self.port)
            self._serial.close()
        self._serial = None

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def __enter__(self) -> ERCConnection:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- raw I/O --

    def _ensure_open(self) -> serial.Serial:
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Connection not open. Call open() first.")
        return self._serial

    def send_raw(self, data: bytes) -> None:
        """Send raw bytes to the ERC-DUO."""
        ser = self._ensure_open()
        log.debug("TX: %r", data)
        ser.write(data)
        ser.flush()

    def recv_line(self, timeout: float | None = None) -> str:
        """Read a CR-terminated line from the ERC-DUO.

        Returns the decoded line with trailing CR/LF stripped.
        """
        ser = self._ensure_open()
        old_timeout = ser.timeout
        if timeout is not None:
            ser.timeout = timeout
        try:
            raw = ser.read_until(CR)
            # Also consume any trailing LF
            if ser.in_waiting:
                extra = ser.read(ser.in_waiting)
                raw += extra
        finally:
            if timeout is not None:
                ser.timeout = old_timeout

        log.debug("RX raw: %r", raw)
        if not raw:
            raise ERCTimeoutError(f"No response within {timeout or self.timeout}s")
        decoded = raw.decode("ascii", errors="replace").strip()
        log.debug("RX: %r", decoded)
        return decoded

    # -- API-level commands --

    def read_param(self, cmd: str) -> str:
        """Send a read command and return the 4-digit value string.

        Args:
            cmd: 3-letter API command (e.g. "FMW", "BAU", "DM1")

        Returns:
            The value portion of the response (e.g. "0100", "9600")

        Raises:
            ERCProtocolError: If the device returns r-ERROR
            ERCTimeoutError: If no response is received
        """
        if len(cmd) != 3:
            raise ValueError(f"Command must be 3 characters, got: {cmd!r}")

        self.send_raw(f"r{cmd}\r".encode("ascii"))
        # Small delay to allow device to process
        time.sleep(0.05)
        response = self.recv_line()

        if response == "r-ERROR":
            raise ERCProtocolError(f"Read error for command: {cmd}")

        # Expected: a<CMD><4-digit value>
        prefix = f"a{cmd}"
        if not response.startswith(prefix):
            raise ERCProtocolError(
                f"Unexpected response for r{cmd}: {response!r}"
            )
        return response[len(prefix):]

    def set_param(self, cmd: str, value: str) -> None:
        """Send a set command to the ERC-DUO.

        Args:
            cmd: 3-letter API command (e.g. "BAU", "DM1")
            value: 4-digit value string (e.g. "9600", "1000")

        Note:
            The ERC-DUO does not acknowledge set commands.
            Use read_param() to verify the value was accepted.
        """
        if len(cmd) != 3:
            raise ValueError(f"Command must be 3 characters, got: {cmd!r}")

        self.send_raw(f"s{cmd}{value}\r".encode("ascii"))
        # Allow time for the device to process
        time.sleep(0.1)

        # Check for error response (device may send s-ERROR)
        ser = self._ensure_open()
        if ser.in_waiting:
            try:
                response = self.recv_line(timeout=0.5)
                if response == "s-ERROR":
                    raise ERCProtocolError(
                        f"Set error for command: {cmd}={value}"
                    )
            except ERCTimeoutError:
                pass  # No error response means success

    def send_rotor_command(self, cmd: str) -> str | None:
        """Send a GS232 rotor-control command.

        Args:
            cmd: Full command string (without trailing CR)

        Returns:
            Response string if the command produces one, None otherwise.
            Empty string means the device acknowledged with a bare CR.
        """
        self.send_raw(f"{cmd}\r".encode("ascii"))
        time.sleep(0.05)

        ser = self._ensure_open()
        if ser.in_waiting:
            try:
                return self.recv_line()
            except ERCTimeoutError:
                return None
        # Some commands produce delayed responses
        time.sleep(0.2)
        if ser.in_waiting:
            try:
                return self.recv_line()
            except ERCTimeoutError:
                return None
        return None

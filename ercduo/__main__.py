"""CLI entry point for the ERC-DUO service tool.

Usage:
    python -m erc_duo [OPTIONS]

    Options:
      -p, --port PORT       Serial port (default: /dev/ttyUSB0)
      -b, --baud BAUD       Baudrate: 4800 or 9600 (default: 9600)
      -v, --verbose         Enable debug logging
      --list-ports          List available serial ports and exit
      --dump                Dump all parameters (JSON) and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from .protocol import ERCConnection, ERCProtocolError, ERCTimeoutError
from .api import ERCAPI
from .models import Axis
from .tui import TUI


def list_serial_ports() -> list[str]:
    """List available serial ports on the system."""
    try:
        from serial.tools.list_ports import comports
        return [p.device for p in comports()]
    except ImportError:
        return []


def dump_params(conn: ERCConnection) -> None:
    """Dump all device parameters as JSON to stdout."""
    api = ERCAPI(conn)
    data = api.read_all()

    # Convert to serializable dict
    out = {}
    for key, val in data.items():
        d = asdict(val)
        # Convert Axis/Protocol enums to strings
        for k, v in d.items():
            if hasattr(v, "name"):
                d[k] = v.name
        out[key] = d

    print(json.dumps(out, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="erc_duo",
        description="ERC-DUO V1.1 Service Tool for Linux",
    )
    parser.add_argument(
        "-p", "--port",
        default="/dev/ttyUSB0",
        help="Serial port (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "-b", "--baud",
        type=int, default=9600,
        choices=[4800, 9600],
        help="Baudrate (default: 9600)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Dump all parameters as JSON and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    if args.list_ports:
        ports = list_serial_ports()
        if ports:
            print("Available serial ports:")
            for p in ports:
                print(f"  {p}")
        else:
            print("No serial ports found")
        return 0

    try:
        with ERCConnection(args.port, args.baud) as conn:
            if args.dump:
                dump_params(conn)
                return 0

            tui = TUI(conn)
            tui.run()
    except OSError as e:
        print(f"Error opening {args.port}: {e}", file=sys.stderr)
        print("Try --list-ports to see available ports", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())

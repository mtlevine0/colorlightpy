#!/usr/bin/env python3
"""Colorlight 5A-75B LED display — test pattern streamer.

Prerequisites
-------------
* Python 3.9+
* ``pip install -r requirements.txt``
* **Npcap** installed on Windows  (https://npcap.com)
* May need to run as Administrator for raw Ethernet access.

Examples
--------
List network interfaces::

    python main.py --list-interfaces

Stream a scrolling rainbow at 30 fps::

    python main.py -i Ethernet -W 192 -H 384 --fps 30 -p rainbow

Static colour bars at full brightness::

    python main.py -i Ethernet -p bars -b 255
"""

from __future__ import annotations

import argparse
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Colorlight 5A-75B LED receiver card — test tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--list-interfaces",
        action="store_true",
        help="List available network interfaces and exit.",
    )
    parser.add_argument(
        "-i", "--interface",
        help="Network interface (name, substring, or index from --list-interfaces).",
    )
    parser.add_argument(
        "-W", "--width",
        type=int, default=192,
        help="Display width in pixels  (default: 192).",
    )
    parser.add_argument(
        "-H", "--height",
        type=int, default=384,
        help="Display height in pixels (default: 384).",
    )
    parser.add_argument(
        "--fps",
        type=float, default=30.0,
        help="Target frames per second (default: 30).",
    )
    parser.add_argument(
        "-b", "--brightness",
        type=int, default=255,
        help="Brightness 0-255 (default: 255).",
    )
    parser.add_argument(
        "--scan-mode",
        choices=["sequential", "interlaced", "interlaced-32"],
        default="sequential",
        help=(
            "Row scanning mode for LED panels with multiplexing.  "
            "sequential: send rows top-to-bottom (default).  "
            "interlaced: send even rows first, then odd rows.  "
            "interlaced-32: send all Group A rows (0-31 of each 64-row module), "
            "then all Group B rows (32-63 of each module) - optimized for "
            "32-line scan displays."
        ),
    )
    parser.add_argument(
        "-p", "--pattern",
        default="rainbow",
        help=(
            "Test pattern to display.  Options: "
            "rainbow, diagonal, bars, bounce, solid  (default: rainbow)."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    # Defer heavy imports so --help is instant
    try:
        from colorlight.driver import ColorlightDriver, list_interfaces
        from colorlight.patterns import PATTERNS
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with:  pip install -r requirements.txt", file=sys.stderr)
        print(
            "On Windows you also need Npcap: https://npcap.com",
            file=sys.stderr,
        )
        return 1

    # --list-interfaces mode
    if args.list_interfaces:
        ifaces = list_interfaces()
        if not ifaces:
            print("No interfaces found.  Is Npcap installed?")
            return 1
        print(f"{'Idx':<5} {'Name':<30} {'MAC':<20} Description")
        print("-" * 85)
        for idx, iface in enumerate(ifaces):
            print(
                f"{idx:<5} {iface['name']:<30} {iface['mac']:<20} "
                f"{iface['description']}"
            )
        return 0

    # Validate required args
    if not args.interface:
        print(
            "Error: --interface is required  "
            "(use --list-interfaces to see options)",
            file=sys.stderr,
        )
        return 1

    if args.pattern not in PATTERNS:
        print(
            f"Unknown pattern '{args.pattern}'.  "
            f"Choose from: {', '.join(PATTERNS)}",
            file=sys.stderr,
        )
        return 1

    # Build pattern and driver
    pattern = PATTERNS[args.pattern](args.width, args.height)

    print(f"Display : {args.width} x {args.height}")
    print(f"Pattern : {args.pattern}")
    print(f"FPS     : {args.fps}")
    print(f"Bright  : {args.brightness}")
    print(f"Scan    : {args.scan_mode}")
    print(f"NIC     : {args.interface}")
    print()

    try:
        with ColorlightDriver(
            interface=args.interface,
            width=args.width,
            height=args.height,
            brightness=args.brightness,
            scan_mode=args.scan_mode,
        ) as driver:
            driver.stream(pattern.generate, fps=args.fps)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        print(
            "Make sure Npcap is installed and you have permission "
            "to send raw packets (try running as Administrator).",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

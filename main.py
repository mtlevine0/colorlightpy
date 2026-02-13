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
import time


def _read_frame_from_stdin(width: int, height: int, frame_buffer) -> bool:
    """Read one raw RGB frame from stdin into the provided buffer.

    Parameters
    ----------
    width : int
        Frame width in pixels.
    height : int
        Frame height in pixels.
    frame_buffer : np.ndarray
        Pre-allocated buffer of shape (height, width, 3), dtype uint8.

    Returns
    -------
    bool
        True if frame was successfully read, False on EOF.

    Raises
    ------
    ValueError
        If incomplete frame received before EOF.
    """
    expected_bytes = width * height * 3
    data = bytearray()

    # Read exactly expected_bytes, handling partial reads
    while len(data) < expected_bytes:
        remaining = expected_bytes - len(data)
        chunk = sys.stdin.buffer.read(remaining)

        if not chunk:
            # EOF reached
            if len(data) == 0:
                return False  # Clean EOF, no data read yet
            else:
                raise ValueError(
                    f"Incomplete frame: expected {expected_bytes} bytes, "
                    f"got {len(data)} bytes before EOF"
                )

        data.extend(chunk)

    # Copy into numpy buffer (reshape from flat bytes)
    import numpy as np
    np.copyto(frame_buffer, np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3)))
    return True


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
    parser.add_argument(
        "--stdin",
        action="store_true",
        help=(
            "Read raw RGB frames from stdin instead of using a pattern.  "
            "Expects continuous stream of WIDTH * HEIGHT * 3 bytes per frame (RGB order)."
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

    # Validate pattern if not using stdin
    if not args.stdin:
        if args.pattern not in PATTERNS:
            print(
                f"Unknown pattern '{args.pattern}'.  "
                f"Choose from: {', '.join(PATTERNS)}",
                file=sys.stderr,
            )
            return 1

    if args.stdin:
        # Stdin mode: read raw RGB frames from stdin
        import numpy as np

        frame_buffer = np.zeros((args.height, args.width, 3), dtype=np.uint8)
        frame_count = 0

        print(f"Display : {args.width} x {args.height}")
        print(f"Mode    : stdin (raw RGB)")
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
                interval = 1.0 / args.fps
                fps_report_period = 5.0

                start = time.perf_counter()
                next_frame_time = start
                last_report = start
                report_frame_count = 0

                print(
                    f"Streaming {args.width}x{args.height} @ {args.fps} fps  "
                    f"(brightness={args.brightness})  — Reading from stdin..."
                )

                while True:
                    loop_start = time.perf_counter()

                    # Read frame from stdin (blocking)
                    try:
                        if not _read_frame_from_stdin(args.width, args.height, frame_buffer):
                            # Clean EOF
                            print(f"\nEOF reached after {frame_count} frames.")
                            break
                    except ValueError as exc:
                        print(f"\nError: {exc}", file=sys.stderr)
                        return 1

                    # Send frame to display
                    driver.send_frame(frame_buffer)
                    frame_count += 1
                    report_frame_count += 1

                    # FPS reporting
                    if loop_start - last_report >= fps_report_period:
                        actual = report_frame_count / (loop_start - last_report)
                        print(
                            f"  {actual:.1f} fps  ({frame_count} total frames)",
                            file=sys.stderr
                        )
                        report_frame_count = 0
                        last_report = loop_start

                    # Rate limiting to match target FPS
                    next_frame_time += interval
                    now = time.perf_counter()
                    sleep_time = next_frame_time - now

                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    elif sleep_time < -interval:
                        # If more than one frame behind, resync
                        next_frame_time = now + interval

        except KeyboardInterrupt:
            print(f"\nStopped after {frame_count} frames.")
        except OSError as exc:
            print(f"Network error: {exc}", file=sys.stderr)
            print(
                "Make sure Npcap is installed and you have permission "
                "to send raw packets (try running as Administrator).",
                file=sys.stderr,
            )
            return 1

        return 0

    else:
        # Pattern mode: use built-in test patterns
        pattern = PATTERNS[args.pattern](args.width, args.height)

        print(f"Display : {args.width} x {args.height}")
        print(f"Pattern : {args.pattern}")
        print(f"Speed   : {args.speed}x")
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
                speed = args.speed
                frame_fn = (lambda t: pattern.generate(t * speed)) if speed != 1.0 else pattern.generate
                driver.stream(frame_fn, fps=args.fps)
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

#!/usr/bin/env python3
"""Pattern frame emitter for testing stdin video streaming.

Generates test patterns and outputs raw RGB frames to stdout, suitable for
piping to main.py --stdin mode.

Examples
--------
Pipe rainbow pattern to display::

    python pattern_emitter.py -p rainbow -W 192 -H 384 --fps 30 | \\
        python main.py --stdin -i Ethernet -W 192 -H 384 --fps 30

Generate diagonal rainbow for 10 seconds::

    python pattern_emitter.py -p diagonal -W 192 -H 384 --duration 10 | \\
        python main.py --stdin -i Ethernet -W 192 -H 384 --fps 30

Save frames to file for later playback::

    python pattern_emitter.py -p bars -W 192 -H 384 --fps 30 --duration 5 > frames.rgb
    cat frames.rgb | python main.py --stdin -i Ethernet -W 192 -H 384 --fps 30
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pattern frame emitter for stdin video streaming",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-W", "--width",
        type=int, default=192,
        help="Frame width in pixels (default: 192).",
    )
    parser.add_argument(
        "-H", "--height",
        type=int, default=384,
        help="Frame height in pixels (default: 384).",
    )
    parser.add_argument(
        "-p", "--pattern",
        default="rainbow",
        help=(
            "Test pattern to generate.  Options: "
            "rainbow, diagonal, bars, bounce, solid  (default: rainbow)."
        ),
    )
    parser.add_argument(
        "--fps",
        type=float, default=30.0,
        help="Target frames per second (default: 30).",
    )
    parser.add_argument(
        "-s", "--speed",
        type=float, default=1.0,
        help="Pattern animation speed multiplier (default: 1.0).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Duration in seconds (default: run forever).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stderr progress messages.",
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    # Import dependencies (deferred for fast --help)
    try:
        from colorlight.patterns import PATTERNS
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with:  pip install -r requirements.txt", file=sys.stderr)
        return 1

    # Validate pattern
    if args.pattern not in PATTERNS:
        print(
            f"Unknown pattern '{args.pattern}'.  "
            f"Choose from: {', '.join(PATTERNS)}",
            file=sys.stderr,
        )
        return 1

    # Build pattern generator
    pattern = PATTERNS[args.pattern](args.width, args.height)

    if not args.quiet:
        print(f"Emitting : {args.width} x {args.height}", file=sys.stderr)
        print(f"Pattern  : {args.pattern}", file=sys.stderr)
        print(f"Speed    : {args.speed}x", file=sys.stderr)
        print(f"FPS      : {args.fps}", file=sys.stderr)
        if args.duration:
            print(f"Duration : {args.duration}s", file=sys.stderr)
        else:
            print(f"Duration : infinite (until pipe closes)", file=sys.stderr)
        print(file=sys.stderr)

    interval = 1.0 / args.fps
    fps_report_period = 5.0

    start = time.perf_counter()
    next_frame_time = start
    last_report = start
    frame_count = 0
    report_frame_count = 0

    try:
        while True:
            loop_start = time.perf_counter()
            elapsed = loop_start - start

            # Check duration limit
            if args.duration is not None and elapsed >= args.duration:
                break

            # Generate frame
            frame = pattern.generate(elapsed * args.speed)

            # Write raw RGB bytes to stdout (unbuffered)
            try:
                sys.stdout.buffer.write(frame.tobytes())
                sys.stdout.buffer.flush()
            except BrokenPipeError:
                # Reader closed pipe - exit cleanly
                if not args.quiet:
                    print("\nPipe closed by reader.", file=sys.stderr)
                break

            frame_count += 1
            report_frame_count += 1

            # FPS reporting
            if not args.quiet and loop_start - last_report >= fps_report_period:
                actual = report_frame_count / (loop_start - last_report)
                print(
                    f"  {actual:.1f} fps  ({frame_count} total frames)",
                    file=sys.stderr
                )
                report_frame_count = 0
                last_report = loop_start

            # Rate limiting
            next_frame_time += interval
            now = time.perf_counter()
            sleep_time = next_frame_time - now

            if sleep_time > 0:
                time.sleep(sleep_time)
            elif sleep_time < -interval:
                # More than one frame behind, resync
                next_frame_time = now + interval

    except KeyboardInterrupt:
        if not args.quiet:
            print(f"\nStopped after {frame_count} frames.", file=sys.stderr)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Emitted {frame_count} frames.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

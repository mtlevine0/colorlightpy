"""High-level driver for the Colorlight 5A-75B LED receiver card.

Manages network interface discovery, raw L2 socket lifecycle,
frame transmission, and an FPS-controlled streaming loop.
"""

from __future__ import annotations

import sys
import time
import threading
from typing import Callable, Dict, List, Optional

import numpy as np

from . import protocol


# ---------------------------------------------------------------------------
# Interface utilities
# ---------------------------------------------------------------------------

def list_interfaces() -> List[Dict[str, str]]:
    """Return available network interfaces as a list of dicts.

    Each dict contains: ``name``, ``description``, ``mac``, ``id``.
    """
    results: List[Dict[str, str]] = []

    if sys.platform == "win32":
        from scapy.arch.windows import get_windows_if_list

        for iface in get_windows_if_list():
            results.append({
                "name": iface.get("name", ""),
                "description": iface.get("description", ""),
                "mac": iface.get("mac", ""),
                "id": iface.get("guid", iface.get("name", "")),
            })
    else:
        from scapy.all import get_if_list, get_if_hwaddr

        for name in get_if_list():
            try:
                mac = get_if_hwaddr(name)
            except Exception:
                mac = ""
            results.append({
                "name": name,
                "description": name,
                "mac": mac,
                "id": name,
            })

    return results


def resolve_interface(identifier: str):
    """Resolve a user-supplied interface string to a scapy interface.

    Accepts a friendly name (e.g. ``"Ethernet"``), a numeric index from
    :func:`list_interfaces`, or a raw device path / GUID.

    Returns
    -------
    object
        A scapy-compatible interface reference suitable for
        ``conf.L2socket(iface=...)``.

    Raises
    ------
    ValueError
        If no matching interface is found.
    """
    from scapy.all import conf

    if sys.platform == "win32":
        # Try numeric index first
        try:
            idx = int(identifier)
            ifaces = list_interfaces()
            if 0 <= idx < len(ifaces):
                target = ifaces[idx]["name"]
                for iface in conf.ifaces.values():
                    if getattr(iface, "name", "") == target:
                        return iface
                    if getattr(iface, "description", "") == target:
                        return iface
        except ValueError:
            pass

        # Try substring match on name or description (case-insensitive)
        ident_lower = identifier.lower()
        for iface in conf.ifaces.values():
            name = (getattr(iface, "name", "") or "").lower()
            desc = (getattr(iface, "description", "") or "").lower()
            if ident_lower in name or ident_lower in desc:
                return iface

        raise ValueError(
            f"No interface matching '{identifier}'.  "
            "Run with --list-interfaces to see available options."
        )

    # Linux / macOS — the string is used directly
    return identifier


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class ColorlightDriver:
    """Stream RGB frames to a Colorlight 5A-75B over raw Ethernet.

    Parameters
    ----------
    interface : str
        Network interface name, description substring, or numeric index.
    width : int
        Display width in pixels.
    height : int
        Display height in pixels.
    brightness : int
        Initial brightness (0–255).
    """

    def __init__(
        self,
        interface: str,
        width: int = 192,
        height: int = 384,
        brightness: int = 255,
        scan_mode: str = "sequential",
    ) -> None:
        self.width = width
        self.height = height
        self.brightness = max(0, min(255, brightness))
        self._iface = resolve_interface(interface)
        self._socket = None
        self._lock = threading.Lock()
        self._running = False
        self.src_mac = protocol.SRC_MAC
        # Pre-allocate packet builder for fast frame conversion
        self._packet_builder = protocol.PacketBuilder(
            width, height, self.src_mac, scan_mode=scan_mode
        )
        # Pre-create sync and brightness Raw packets
        self._sync_raw = None
        self._brightness_raw = None

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> "ColorlightDriver":
        """Open the raw L2 socket and send initialization sequence.

        The vendor software sends brightness and sync packets twice
        before streaming frame data.  We replicate that here.
        """
        from scapy.all import conf
        from scapy.packet import Raw

        self._socket = conf.L2socket(iface=self._iface)

        # Pre-create Raw packets for brightness and sync
        brightness_pkt = protocol.build_brightness_packet(self.brightness, self.src_mac)
        self._brightness_raw = Raw(load=brightness_pkt)

        sync_pkt = protocol.build_sync_packet(self.brightness, src_mac=self.src_mac)
        self._sync_raw = Raw(load=sync_pkt)

        # Initialization sequence
        self._send_brightness()
        self._send_sync()
        self._send_brightness()
        self._send_sync()
        return self

    def close(self) -> None:
        """Stop any running stream and close the socket."""
        self._running = False
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> "ColorlightDriver":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- commands ------------------------------------------------------------

    def set_brightness(self, value: int) -> None:
        """Update display brightness (0–255) and send immediately."""
        from scapy.packet import Raw

        self.brightness = max(0, min(255, value))

        # Update pre-created Raw packets with new brightness
        if self._brightness_raw is not None:
            brightness_pkt = protocol.build_brightness_packet(self.brightness, self.src_mac)
            self._brightness_raw.load = brightness_pkt

        if self._sync_raw is not None:
            sync_pkt = protocol.build_sync_packet(self.brightness, src_mac=self.src_mac)
            self._sync_raw.load = sync_pkt

        self._send_brightness()

    def send_frame(self, frame: np.ndarray) -> None:
        """Send a single frame to the display.

        Parameters
        ----------
        frame : numpy.ndarray
            Shape ``(height, width, 3)``, dtype ``uint8``, RGB order.
        """
        if frame.shape != (self.height, self.width, 3):
            raise ValueError(
                f"Frame shape {frame.shape} != expected "
                f"({self.height}, {self.width}, 3)"
            )

        # Match documented protocol order: brightness → row data → sync
        # (See CLAUDE.md: "Per-frame send order: brightness (0x0A) → row data (0x55xx) → sync (0x0107)")
        self._send_brightness()

        # Use pre-allocated packet builder for faster conversion
        # Returns pre-created Scapy Raw packets (not bytes)
        raw_packets = self._packet_builder.frame_to_packets_fast(frame)

        # Send all row packets
        self._send_raw_packets(raw_packets)

        # Send sync packet
        self._send_sync()

    def stream(
        self,
        frame_fn: Callable[[float], np.ndarray],
        fps: float = 30.0,
        duration: Optional[float] = None,
    ) -> None:
        """Run an FPS-controlled streaming loop.

        Blocks until *duration* elapses, ``stop()`` is called, or the
        user presses Ctrl-C.

        Parameters
        ----------
        frame_fn : callable
            ``frame_fn(elapsed_seconds) -> ndarray`` returning the frame
            to display at the given timestamp.
        fps : float
            Target frames per second.
        duration : float or None
            Seconds to stream.  ``None`` means run forever.
        """
        interval = 1.0 / fps
        fps_report_period = 5.0

        self._running = True
        start = time.perf_counter()
        next_frame_time = start  # Track target time for next frame
        last_report = start
        frame_count = 0
        total_gen_time = 0.0
        total_send_time = 0.0

        print(
            f"Streaming {self.width}x{self.height} @ {fps} fps  "
            f"(brightness={self.brightness})  — Ctrl-C to stop"
        )

        try:
            while self._running:
                loop_start = time.perf_counter()
                elapsed = loop_start - start

                if duration is not None and elapsed >= duration:
                    break

                # Generate and send frame (atomic operation - no interruptions)
                t_gen_start = time.perf_counter()
                frame = frame_fn(elapsed)
                t_gen_end = time.perf_counter()

                self.send_frame(frame)
                t_send_end = time.perf_counter()

                frame_count += 1

                # Track timing for diagnostics
                gen_time = (t_gen_end - t_gen_start) * 1000
                send_time = (t_send_end - t_gen_end) * 1000
                total_gen_time += gen_time
                total_send_time += send_time

                # FPS reporting
                if loop_start - last_report >= fps_report_period:
                    actual = frame_count / (loop_start - last_report)
                    avg_gen = total_gen_time / frame_count if frame_count > 0 else 0
                    avg_send = total_send_time / frame_count if frame_count > 0 else 0
                    print(
                        f"  {actual:.1f} fps  ({frame_count} frames)  "
                        f"gen={avg_gen:.2f}ms  send={avg_send:.2f}ms",
                        file=sys.stderr
                    )
                    frame_count = 0
                    total_gen_time = 0.0
                    total_send_time = 0.0
                    last_report = loop_start

                # Drift-compensated timing: sleep until target time for next frame
                next_frame_time += interval
                now = time.perf_counter()
                sleep_time = next_frame_time - now

                if sleep_time > 0:
                    time.sleep(sleep_time)
                elif sleep_time < -interval:
                    # If we're more than one frame behind, resync to avoid runaway drift
                    next_frame_time = now + interval

        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            self._running = False

    def stop(self) -> None:
        """Signal the streaming loop to exit."""
        self._running = False

    # -- internals -----------------------------------------------------------

    def _send_packets(self, packets: List[bytes]) -> None:
        """Send all packets with lock held for entire batch to prevent interruption."""
        from scapy.packet import Raw

        with self._lock:
            for pkt in packets:
                self._socket.send(Raw(load=pkt))

    def _send_raw_packets(self, raw_packets: List) -> None:
        """Send pre-created Scapy Raw packets (no wrapping overhead).

        Parameters
        ----------
        raw_packets : list
            List of pre-created scapy.packet.Raw objects.
        """
        with self._lock:
            for pkt in raw_packets:
                self._socket.send(pkt)

    def _send_brightness(self) -> None:
        """Send brightness packet using pre-created Raw object."""
        with self._lock:
            self._socket.send(self._brightness_raw)

    def _send_sync(self) -> None:
        """Send sync packet using pre-created Raw object."""
        with self._lock:
            self._socket.send(self._sync_raw)

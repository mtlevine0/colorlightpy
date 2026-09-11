"""High-level driver for the Colorlight 5A-75B LED receiver card.

Manages network interface discovery, raw L2 socket lifecycle,
frame transmission, and an FPS-controlled streaming loop.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import threading
from typing import Callable, Dict, List, Optional

import numpy as np

from . import protocol
from .sleep_monitor import LogindSleepMonitor


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
        # Frame sends are also performed by the logind listener.  An RLock
        # makes a complete frame atomic while allowing the low-level helpers
        # to retain their own locking contract.
        self._lock = threading.RLock()
        self._running = False
        self._suspend_requested = False
        self._sleep_monitor = LogindSleepMonitor(self._handle_prepare_for_sleep)
        self.src_mac = protocol.SRC_MAC
        # Pre-allocate packet builder for fast frame conversion
        self._packet_builder = protocol.PacketBuilder(
            width, height, self.src_mac, scan_mode=scan_mode
        )
        # Pre-create sync and brightness Raw packets
        self._sync_raw = None
        self._brightness_raw = None

        # CLOCK_BOOTTIME advances while Linux is suspended, whereas
        # monotonic/perf_counter does not.  Comparing the two lets a daemon
        # notice resume without depending on a desktop session or D-Bus.
        if hasattr(time, "CLOCK_BOOTTIME"):
            self._resume_clock = lambda: time.clock_gettime(time.CLOCK_BOOTTIME)
        else:
            # Portable fallback.  A large wall-clock adjustment can cause a
            # harmless extra recovery on platforms without CLOCK_BOOTTIME.
            self._resume_clock = time.time
        self._last_resume_clock = self._resume_clock()
        self._last_monotonic = time.monotonic()

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> "ColorlightDriver":
        """Open the raw L2 socket and send initialization sequence.

        The vendor software sends brightness and sync packets twice
        before streaming frame data.  We replicate that here.
        """
        from scapy.all import conf
        from scapy.packet import Raw

        # A Colorlight receiver expects a gigabit link.  Some Realtek NICs
        # occasionally resume at 100 Mbps/half duplex; packets still appear to
        # send successfully in that state, but the receiver remains frozen.
        self._keep_link_awake()
        self._wait_for_link()

        # Make open idempotent so it can also be used after suspend.
        if self._socket is not None:
            self._socket.close()
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
        self._sleep_monitor.start()
        return self

    def resume_detected(self, threshold: float = 1.0) -> bool:
        """Return True once after the host resumes from a significant sleep.

        ``threshold`` is the minimum time spent suspended.  The clock samples
        are always advanced, so a single resume only triggers one recovery.
        """
        resume_now = self._resume_clock()
        monotonic_now = time.monotonic()
        suspended_for = (
            (resume_now - self._last_resume_clock)
            - (monotonic_now - self._last_monotonic)
        )
        self._last_resume_clock = resume_now
        self._last_monotonic = monotonic_now
        return suspended_for >= threshold

    def recover(self, frame: np.ndarray, frame_repeats: int = 3) -> None:
        """Reopen the L2 socket, reinitialize the receiver, and replay a frame.

        Colorlight's protocol has no acknowledgement.  Repeating a complete
        frame makes recovery robust when the NIC and receiver are settling
        immediately after resume.
        """
        if frame_repeats < 1:
            raise ValueError("frame_repeats must be at least 1")

        self.open()
        for _ in range(frame_repeats):
            self.send_frame(frame)

    def _wait_for_link(self, timeout: float = 5.0, minimum_speed: int = 1000) -> None:
        """Wait for carrier and a usable Linux Ethernet link.

        If the link negotiates below ``minimum_speed``, ask the driver to
        restart autonegotiation.  This requires ``CAP_NET_ADMIN`` when running
        as a systemd service.
        """
        if not sys.platform.startswith("linux"):
            return

        interface = getattr(self, "_iface", None)
        if not isinstance(interface, str):
            return
        carrier_path = f"/sys/class/net/{os.path.basename(interface)}/carrier"
        speed_path = f"/sys/class/net/{os.path.basename(interface)}/speed"
        if not os.path.exists(carrier_path):
            return

        deadline = time.monotonic() + timeout
        renegotiated = False
        last_speed = None
        while time.monotonic() < deadline:
            try:
                with open(carrier_path, "r", encoding="ascii") as carrier_file:
                    has_carrier = carrier_file.read().strip() == "1"
                if has_carrier:
                    with open(speed_path, "r", encoding="ascii") as speed_file:
                        last_speed = int(speed_file.read().strip())
                    if last_speed >= minimum_speed:
                        return

                    if not renegotiated:
                        subprocess.run(
                            [
                                "/usr/sbin/ethtool",
                                "--change",
                                interface,
                                "autoneg",
                                "on",
                                "advertise",
                                "0x020",  # 1000baseT/Full only
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        renegotiated = True
            except OSError:
                pass
            time.sleep(0.25)

        detail = "no carrier" if last_speed is None else f"{last_speed} Mbps"
        print(
            f"Warning: {interface} did not establish a {minimum_speed} Mbps "
            f"link within {timeout:g}s (last state: {detail}); continuing "
            "without restarting the display service",
            file=sys.stderr,
        )

    def _keep_link_awake(self) -> None:
        """Ask a Linux NIC to retain PHY power across system suspend."""
        if not sys.platform.startswith("linux"):
            return
        interface = getattr(self, "_iface", None)
        if not isinstance(interface, str):
            return

        try:
            subprocess.run(
                ["/usr/sbin/ethtool", "--change", interface, "wol", "g"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(
                f"Warning: could not enable Wake-on-LAN for {interface}: {exc}",
                file=sys.stderr,
            )

    def close(self) -> None:
        """Stop any running stream and close the socket."""
        self._running = False
        self._sleep_monitor.stop()
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

    def _handle_prepare_for_sleep(self, sleeping: bool) -> None:
        """Blank the receiver after logind announces an imminent suspend."""
        with self._lock:
            self._suspend_requested = sleeping
            if not sleeping or self._socket is None:
                return

            print("Host is suspending; blanking Colorlight output", file=sys.stderr)
            self.send_frame(
                np.zeros((self.height, self.width, 3), dtype=np.uint8), force=True
            )

    def send_frame(self, frame: np.ndarray, *, force: bool = False) -> None:
        """Send a single frame to the display.

        Parameters
        ----------
        frame : numpy.ndarray
            Shape ``(height, width, 3)``, dtype ``uint8``, RGB order.
        """
        with self._lock:
            if self._suspend_requested and not force:
                return
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

                if self.resume_detected():
                    print("Host resumed; reinitializing Colorlight output", file=sys.stderr)
                    self.recover(frame)
                else:
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

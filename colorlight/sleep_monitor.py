"""Linux logind integration for blanking output before system suspend."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Callable, Optional


class LogindSleepMonitor:
    """Invoke a callback when logind announces a sleep-state change.

    The listener is deliberately optional: systems without systemd/logind (and
    all non-Linux platforms) continue to operate normally.
    """

    def __init__(self, callback: Callable[[bool], None]) -> None:
        self._callback = callback
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._inhibitor_fd: Optional[int] = None
        self._inhibitor_lock = threading.Lock()

    def start(self) -> None:
        """Start listening to ``org.freedesktop.login1`` once."""
        if not sys.platform.startswith("linux") or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="colorlight-logind-sleep", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the listener without delaying application shutdown."""
        self._stop_event.set()
        self._release_inhibitor()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        try:
            asyncio.run(self._listen())
        except Exception as exc:
            # A missing system bus must not prevent the display service from
            # starting.  The driver still recovers after resume via BOOTTIME.
            print(
                f"Warning: logind sleep monitoring is unavailable: {exc}",
                file=sys.stderr,
            )

    async def _listen(self) -> None:
        from dbus_next import BusType, Message, MessageType
        from dbus_next.aio import MessageBus

        # negotiate_unix_fd is required to receive the delay-inhibitor file
        # descriptor Inhibit() returns below -- without it, dbus-next can't
        # carry a fd across the connection at all.
        bus = await MessageBus(
            bus_type=BusType.SYSTEM, negotiate_unix_fd=True
        ).connect()

        # A message handler only processes messages that arrive at this
        # connection.  Ask the bus daemon to forward this broadcast signal;
        # without this AddMatch call, PrepareForSleep is never delivered.
        reply = await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[
                    "type='signal',"
                    "sender='org.freedesktop.login1',"
                    "interface='org.freedesktop.login1.Manager',"
                    "member='PrepareForSleep'"
                ],
            )
        )
        if reply.message_type is MessageType.ERROR:
            raise RuntimeError(reply.body[0] if reply.body else "D-Bus AddMatch failed")

        async def acquire_inhibitor() -> None:
            reply = await bus.call(
                Message(
                    destination="org.freedesktop.login1",
                    path="/org/freedesktop/login1",
                    interface="org.freedesktop.login1.Manager",
                    member="Inhibit",
                    signature="ssss",
                    body=[
                        "sleep",
                        "colorlightpy",
                        "Blank Colorlight output before suspend",
                        "delay",
                    ],
                )
            )
            if reply.message_type is MessageType.ERROR:
                raise RuntimeError(
                    reply.body[0] if reply.body else "logind Inhibit failed"
                )
            if not reply.body or not reply.unix_fds:
                raise RuntimeError("logind Inhibit returned no file descriptor")
            fd_index = reply.body[0]
            if not isinstance(fd_index, int) or not 0 <= fd_index < len(reply.unix_fds):
                raise RuntimeError("logind Inhibit returned an invalid file descriptor")
            fd = reply.unix_fds[fd_index]
            with self._inhibitor_lock:
                if self._stop_event.is_set():
                    os.close(fd)
                else:
                    self._inhibitor_fd = fd

        def handle_message(message) -> bool:
            if (
                message.message_type is MessageType.SIGNAL
                and message.interface == "org.freedesktop.login1.Manager"
                and message.member == "PrepareForSleep"
                and len(message.body) == 1
                and isinstance(message.body[0], bool)
            ):
                sleeping = message.body[0]
                if sleeping:
                    # logind waits for our delay-inhibitor fd to close (up to
                    # InhibitDelayMaxSec), so release it only after output has
                    # been made safe for suspend.
                    try:
                        self._callback(True)
                    finally:
                        self._release_inhibitor()
                else:
                    # Schedule reacquisition on the listener loop before
                    # notifying the application that resume is complete.
                    async def resume() -> None:
                        try:
                            await acquire_inhibitor()
                        except Exception as exc:
                            print(
                                f"Warning: could not reacquire sleep inhibitor: {exc}",
                                file=sys.stderr,
                            )
                        self._callback(False)

                    asyncio.create_task(resume())
            return False

        bus.add_message_handler(handle_message)
        try:
            try:
                await acquire_inhibitor()
            except Exception as exc:
                # Signal monitoring is still useful without the guarantee.
                print(
                    f"Warning: could not acquire sleep inhibitor: {exc}",
                    file=sys.stderr,
                )
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)
        finally:
            self._release_inhibitor()
            bus.disconnect()

    def _release_inhibitor(self) -> None:
        """Close the delay-inhibitor fd, allowing logind to continue."""
        with self._inhibitor_lock:
            fd = self._inhibitor_fd
            self._inhibitor_fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

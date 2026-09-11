"""Linux logind integration for blanking output before system suspend."""

from __future__ import annotations

import asyncio
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

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

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

        def handle_message(message) -> bool:
            if (
                message.message_type is MessageType.SIGNAL
                and message.interface == "org.freedesktop.login1.Manager"
                and message.member == "PrepareForSleep"
                and len(message.body) == 1
                and isinstance(message.body[0], bool)
            ):
                self._callback(message.body[0])
            return False

        bus.add_message_handler(handle_message)
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(0.1)
        finally:
            bus.disconnect()

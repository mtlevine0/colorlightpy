import numpy as np

import colorlight.driver as driver_module
from colorlight.driver import ColorlightDriver


def test_resume_detected_only_for_suspend_gap():
    driver = ColorlightDriver.__new__(ColorlightDriver)
    samples = iter([100.1, 105.1, 105.2])
    driver._resume_clock = lambda: next(samples)
    driver._last_resume_clock = 100.0
    driver._last_monotonic = 50.0

    monotonic_samples = iter([50.1, 50.2, 50.3])
    original_monotonic = driver_module.time.monotonic
    driver_module.time.monotonic = lambda: next(monotonic_samples)
    try:
        assert driver.resume_detected() is False
        assert driver.resume_detected() is True
        assert driver.resume_detected() is False
    finally:
        driver_module.time.monotonic = original_monotonic


def test_recover_reopens_and_replays_complete_frame():
    driver = ColorlightDriver.__new__(ColorlightDriver)
    calls = []
    driver.open = lambda: calls.append("open")
    driver.send_frame = lambda frame: calls.append(frame)
    frame = np.zeros((2, 3, 3), dtype=np.uint8)

    driver.recover(frame)

    assert calls[0] == "open"
    assert len(calls) == 4
    assert all(item is frame for item in calls[1:])


def test_wait_for_link_restarts_bad_negotiation(tmp_path, monkeypatch):
    driver = ColorlightDriver.__new__(ColorlightDriver)
    driver._iface = "test0"
    carrier = tmp_path / "carrier"
    speed = tmp_path / "speed"
    carrier.write_text("1")
    speed.write_text("100")
    calls = []

    monkeypatch.setattr(driver_module.os.path, "exists", lambda path: True)
    real_open = open
    monkeypatch.setattr(
        "builtins.open",
        lambda path, *args, **kwargs: real_open(
            carrier if path.endswith("/carrier") else speed, *args, **kwargs
        ),
    )
    monkeypatch.setattr(
        driver_module.subprocess,
        "run",
        lambda command, **kwargs: (calls.append(command), speed.write_text("1000")),
    )
    monkeypatch.setattr(driver_module.time, "sleep", lambda seconds: None)

    driver._wait_for_link(timeout=1)

    assert calls == [[
        "/usr/sbin/ethtool", "--change", "test0", "autoneg", "on",
        "advertise", "0x020",
    ]]


def test_recover_rejects_zero_repeats():
    driver = ColorlightDriver.__new__(ColorlightDriver)
    frame = np.zeros((1, 1, 3), dtype=np.uint8)

    try:
        driver.recover(frame, frame_repeats=0)
    except ValueError as exc:
        assert str(exc) == "frame_repeats must be at least 1"
    else:
        raise AssertionError("recover accepted zero repeats")


def test_prepare_for_sleep_repeats_blank_and_blocks_new_frames(monkeypatch):
    driver = ColorlightDriver.__new__(ColorlightDriver)
    driver.width = 3
    driver.height = 2
    driver._lock = driver_module.threading.RLock()
    driver._socket = object()
    driver._suspend_requested = False
    calls = []

    def send_frame(frame, *, force=False):
        calls.append((frame, force))

    monkeypatch.setattr(driver, "send_frame", send_frame)

    driver._handle_prepare_for_sleep(True)

    # Repeated like recover()'s frame_repeats, and for the same reason: the
    # protocol has no acknowledgement, and a single send can be lost while
    # the NIC is still settling right as suspend begins.
    assert driver._suspend_requested is True
    assert len(calls) == 3
    assert all(force is True for _, force in calls)
    assert all(frame.shape == (2, 3, 3) and not frame.any() for frame, _ in calls)

    # Normal stream writes are suppressed until logind announces wake-up.
    ColorlightDriver.send_frame(driver, np.zeros((2, 3, 3), dtype=np.uint8))
    assert len(calls) == 3

    driver._handle_prepare_for_sleep(False)
    assert driver._suspend_requested is False
    assert len(calls) == 3


def _sleeping_driver(monkeypatch, calls):
    """A driver wired up far enough to run the suspend handler."""
    driver = ColorlightDriver.__new__(ColorlightDriver)
    driver.width = 3
    driver.height = 2
    driver._lock = driver_module.threading.RLock()
    driver._socket = object()
    driver._suspend_requested = False
    monkeypatch.setattr(
        driver, "send_frame",
        lambda frame, *, force=False: calls.append(("send", force)))
    monkeypatch.setattr(
        driver, "_wait_for_link",
        lambda timeout=None: calls.append(("link", timeout)))
    monkeypatch.setattr(
        driver_module.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))
    return driver


def test_prepare_for_sleep_waits_for_the_link_before_blanking(monkeypatch):
    """A blank fired into a down or 100 Mbps link is silently swallowed.

    open() already refuses to stream until the link is back at gigabit, for
    exactly this reason; the suspend path used to skip that check and send
    anyway, which is how the wall stayed lit through a suspend.
    """
    calls = []
    driver = _sleeping_driver(monkeypatch, calls)

    driver._handle_prepare_for_sleep(True)

    assert calls[0] == ("link", ColorlightDriver.SUSPEND_LINK_TIMEOUT)
    assert [name for name, _ in calls].count("send") == 3
    # Well inside logind's 5s InhibitDelayMaxSec, which is all that holds the
    # host up while this runs.
    budget = (ColorlightDriver.SUSPEND_LINK_TIMEOUT
              + 2 * ColorlightDriver.SUSPEND_BLANK_SPACING)
    assert budget < 5.0


def test_prepare_for_sleep_spaces_the_repeats(monkeypatch):
    """Back-to-back repeats all land in the same instant and die together."""
    calls = []
    driver = _sleeping_driver(monkeypatch, calls)

    driver._handle_prepare_for_sleep(True)

    sends = [index for index, (name, _) in enumerate(calls) if name == "send"]
    sleeps = [index for index, (name, _) in enumerate(calls) if name == "sleep"]
    assert len(sends) == 3
    # A pause between each pair of sends, and none before the first: the point
    # is to straddle the settling window, not to delay the suspend.
    assert len(sleeps) == 2
    assert sends[0] < sleeps[0] < sends[1] < sleeps[1] < sends[2]
    assert all(
        seconds == ColorlightDriver.SUSPEND_BLANK_SPACING
        for name, seconds in calls if name == "sleep"
    )


def test_resume_neither_waits_nor_blanks(monkeypatch):
    calls = []
    driver = _sleeping_driver(monkeypatch, calls)
    driver._suspend_requested = True

    driver._handle_prepare_for_sleep(False)

    assert driver._suspend_requested is False
    assert calls == []


def test_prepare_for_sleep_without_a_socket_does_nothing(monkeypatch):
    calls = []
    driver = _sleeping_driver(monkeypatch, calls)
    driver._socket = None

    driver._handle_prepare_for_sleep(True)

    assert calls == []


def test_prepare_for_sleep_repeats_configurable(monkeypatch):
    driver = ColorlightDriver.__new__(ColorlightDriver)
    driver.width = 1
    driver.height = 1
    driver._lock = driver_module.threading.RLock()
    driver._socket = object()
    driver._suspend_requested = False
    calls = []
    monkeypatch.setattr(driver, "send_frame", lambda frame, *, force=False: calls.append(force))

    driver._handle_prepare_for_sleep(True, frame_repeats=1)

    assert len(calls) == 1

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


def test_prepare_for_sleep_blanks_once_and_blocks_new_frames(monkeypatch):
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

    assert driver._suspend_requested is True
    assert len(calls) == 1
    assert calls[0][1] is True
    assert calls[0][0].shape == (2, 3, 3)
    assert not calls[0][0].any()

    # Normal stream writes are suppressed until logind announces wake-up.
    ColorlightDriver.send_frame(driver, np.zeros((2, 3, 3), dtype=np.uint8))
    assert len(calls) == 1

    driver._handle_prepare_for_sleep(False)
    assert driver._suspend_requested is False
    assert len(calls) == 1

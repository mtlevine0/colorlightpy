# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python driver for streaming RGB frames to a **Colorlight 5A-75B** LED receiver card over raw Ethernet (Layer 2). The protocol was reverse-engineered by the open-source community (chubby75, LED_Matrix-1). Includes built-in test pattern generators for display validation.

## Setup & Running

```bash
pip install -r requirements.txt
# Windows: also install Npcap from https://npcap.com
# May need Administrator/root for raw socket access

python main.py --list-interfaces
python main.py -i Ethernet -W 192 -H 384 --fps 30 -p rainbow
```

No test framework, linter, or build step is configured — this is a pure Python project.

## Architecture

```
main.py                  CLI entry point (argparse, deferred imports for fast --help)
colorlight/
  __init__.py            Re-exports ColorlightDriver, list_interfaces, PATTERNS
  driver.py              High-level driver: interface discovery, L2 socket, streaming loop
  protocol.py            Low-level packet construction (no scapy dependency, testable standalone)
  patterns.py            Test pattern generators (numpy-based, ABC with generate(t) → ndarray)
```

**Data flow:** CLI → `Pattern.generate(t)` → `(H, W, 3) uint8 ndarray` → `protocol.frame_to_packets()` → raw Ethernet frames → `scapy L2socket.send()`

### Key design decisions

- **`protocol.py` has no scapy dependency** — it builds raw `bytes` frames so it can be tested and profiled independently. Only `driver.py` touches scapy.
- **Patterns use vectorized NumPy** — custom `_hsv_to_rgb()` operates on full arrays, no per-pixel loops. Patterns pre-compute static data in `__init__`.
- **Thread-safe sending** — `ColorlightDriver._lock` serializes packet sends for potential multi-threaded use.
- **Platform-aware interface discovery** — Windows uses `scapy.arch.windows.get_windows_if_list()` with GUID-based matching; Linux/macOS uses interface name strings directly.

### Protocol details

- **Row data:** EtherType `0x5500 | (row >> 8)`, payload carries row low byte + column offset + pixel count + magic `0x0888` + packed RGB bytes
- **Brightness:** EtherType `0x0A00 + brightness_value`
- **Sync/display frame:** EtherType `0x0107` — latches pixel buffer to display, carries brightness + per-channel colour temperature (R/G/B). Sent after each frame's row data.
- **Fixed MACs:** dst `11:22:33:44:55:66`, src `22:22:33:44:55:66`
- **Max 493 pixels per packet** (MTU constraint); wider rows are split into multiple packets
- **Per-frame send order:** brightness (`0x0A`) → row data (`0x55xx`) → sync (`0x0107`)

### Adding a new test pattern

1. Subclass `Pattern` in `patterns.py`, implement `generate(t: float) -> np.ndarray` returning `(height, width, 3)` uint8 RGB
2. Add an entry to the `PATTERNS` dict at the bottom of `patterns.py`
3. The CLI picks it up automatically via the `-p` flag

## Dependencies

- **scapy** (>=2.5.0) — raw Ethernet socket and interface enumeration
- **numpy** (>=1.21.0) — frame buffer manipulation and vectorized color math

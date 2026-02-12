"""Colorlight 5A-75B packet protocol.

Constructs raw Ethernet frames for the Colorlight 5A-75B LED receiver card.
The protocol was reverse-engineered by the open-source community (chubby75,
LED_Matrix-1, etc.).

Frame types:
  - Row pixel data: EtherType 0x5500 | (row >> 8)
  - Brightness:     EtherType 0x0A00 + brightness_value

All public functions return complete Ethernet frames as ``bytes``, ready
to be sent on a raw L2 socket.  This module has **no** scapy dependency
so it can be tested and profiled independently.
"""

from __future__ import annotations

import struct
from typing import List

import numpy as np

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

DST_MAC: bytes = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
SRC_MAC: bytes = bytes([0x22, 0x22, 0x33, 0x44, 0x55, 0x66])

ETHERTYPE_DATA: int = 0x5500
ETHERTYPE_BRIGHTNESS: int = 0x0A00
ETHERTYPE_SYNC: int = 0x0107

# Magic bytes that follow the 5-byte row header (purpose unknown, required)
ROW_MAGIC: bytes = bytes([0x08, 0x88])

# Max columns per packet: (1500 MTU - 14 eth - 7 row hdr) / 3 ≈ 493
MAX_COLS_PER_PACKET: int = 493

# Ethernet frames must be >= 60 bytes (excluding 4-byte FCS added by NIC)
MIN_FRAME_LEN: int = 60


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ether_header(ethertype: int, src_mac: bytes = SRC_MAC) -> bytes:
    """Build a 14-byte Ethernet header (dst + src + type)."""
    return DST_MAC + src_mac + struct.pack(">H", ethertype)


def _pad(frame: bytes) -> bytes:
    """Pad frame to the Ethernet minimum of 60 bytes."""
    if len(frame) < MIN_FRAME_LEN:
        return frame + b"\x00" * (MIN_FRAME_LEN - len(frame))
    return frame


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_row_packet(
    row: int,
    col_offset: int,
    rgb_data: bytes,
    src_mac: bytes = SRC_MAC,
) -> bytes:
    """Build one row-data Ethernet frame.

    Parameters
    ----------
    row : int
        Row index (0-based).  The high byte is encoded in the EtherType
        (``0x5500 | (row >> 8)``), and the low byte goes in payload[0].
    col_offset : int
        Starting pixel column for this chunk (usually 0).
    rgb_data : bytes
        Packed R, G, B bytes — 3 bytes per pixel.
    src_mac : bytes
        Source MAC address (6 bytes).

    Returns
    -------
    bytes
        Complete Ethernet frame, padded to >= 60 bytes.
    """
    pixel_count = len(rgb_data) // 3
    ethertype = ETHERTYPE_DATA | ((row >> 8) & 0xFF)
    header = _ether_header(ethertype, src_mac)

    # Payload: row_lo (1B) + offset (2B) + count (2B) + magic (2B) + RGB
    payload = struct.pack(
        ">BHHH",
        row & 0xFF,
        col_offset,
        pixel_count,
        0x0888,            # magic collapsed into a single uint16
    )
    return _pad(header + payload + rgb_data)


def build_brightness_packet(
    brightness: int,
    src_mac: bytes = SRC_MAC,
) -> bytes:
    """Build a brightness-control Ethernet frame.

    Parameters
    ----------
    brightness : int
        Display brightness 0–255.
    src_mac : bytes
        Source MAC address.
    """
    brightness = max(0, min(255, brightness))
    ethertype = ETHERTYPE_BRIGHTNESS + brightness
    header = _ether_header(ethertype, src_mac)

    payload = bytearray(63)
    payload[0] = brightness
    payload[1] = brightness
    payload[2] = 0xFF
    return _pad(header + bytes(payload))


def build_sync_packet(
    brightness: int = 255,
    r: int | None = None,
    g: int | None = None,
    b: int | None = None,
    src_mac: bytes = SRC_MAC,
) -> bytes:
    """Build a sync/display-frame Ethernet packet (EtherType 0x0107).

    This packet tells the receiver card to latch its pixel buffer to the
    display outputs.  It also carries overall brightness and per-channel
    colour-temperature values.

    Parameters
    ----------
    brightness : int
        Overall brightness 0–255 (linear).
    r, g, b : int or None
        Per-channel brightness for colour temperature.  Defaults to
        *brightness* (neutral white / 6500 K).
    src_mac : bytes
        Source MAC address.
    """
    brightness = max(0, min(255, brightness))
    r = brightness if r is None else max(0, min(255, r))
    g = brightness if g is None else max(0, min(255, g))
    b = brightness if b is None else max(0, min(255, b))

    payload = bytearray(98)
    payload[21] = brightness       # overall brightness
    payload[22] = 0x05             # source type: PC / netcard
    # payload[23] reserved (0x00)
    payload[24] = r                # red channel brightness
    payload[25] = g                # green channel brightness
    payload[26] = b                # blue channel brightness

    header = _ether_header(ETHERTYPE_SYNC, src_mac)
    return header + bytes(payload)


def frame_to_packets(
    frame: np.ndarray,
    width: int,
    height: int,
    src_mac: bytes = SRC_MAC,
) -> List[bytes]:
    """Convert a full frame into an ordered list of row packets.

    Parameters
    ----------
    frame : numpy.ndarray
        Shape ``(height, width, 3)``, dtype ``uint8``, RGB order.
    width : int
        Display width in pixels.
    height : int
        Display height in pixels.
    src_mac : bytes
        Source MAC address.

    Returns
    -------
    list[bytes]
        One or more Ethernet frames per row, ordered top-to-bottom.
    """
    packets: List[bytes] = []

    for row in range(height):
        row_rgb = frame[row].tobytes()  # width * 3 bytes

        # Split into chunks if the row is wider than MAX_COLS_PER_PACKET
        for col_off in range(0, width, MAX_COLS_PER_PACKET):
            chunk_w = min(MAX_COLS_PER_PACKET, width - col_off)
            start = col_off * 3
            chunk = row_rgb[start : start + chunk_w * 3]
            packets.append(build_row_packet(row, col_off, chunk, src_mac))

    return packets

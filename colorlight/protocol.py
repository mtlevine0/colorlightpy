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


# ---------------------------------------------------------------------------
# Pre-allocated packet builder (performance-optimized)
# ---------------------------------------------------------------------------

class PacketBuilder:
    """Pre-allocates packet buffers for zero-copy frame transmission.

    Reuses packet structure across frames, only updating RGB data.
    """

    def __init__(self, width: int, height: int, src_mac: bytes = SRC_MAC, scan_mode: str = "sequential"):
        self.width = width
        self.height = height
        self.src_mac = src_mac
        self.scan_mode = scan_mode

        # Compute row sending order based on scan mode
        if scan_mode == "interlaced":
            # Send even rows first, then odd rows
            self._row_order = list(range(0, height, 2)) + list(range(1, height, 2))
        elif scan_mode == "interlaced-32":
            # 32-line scan interlacing for 64-row modules
            # Send all Group A rows (0-31 of each module), then all Group B rows (32-63)
            module_height = 64
            scan_lines = 32
            num_modules = height // module_height

            row_order = []
            # All Group A rows (lines 0-31 of each 64-row module)
            for module in range(num_modules):
                for line in range(scan_lines):
                    row_order.append(module * module_height + line)

            # All Group B rows (lines 32-63 of each 64-row module)
            for module in range(num_modules):
                for line in range(scan_lines, module_height):
                    row_order.append(module * module_height + line)

            self._row_order = row_order
        else:  # sequential
            self._row_order = list(range(height))

        # Pre-build packet templates with headers
        self._packet_templates: List[bytearray] = []
        self._raw_packets: List = []  # Pre-created Scapy Raw packets
        self._rgb_offsets: List[int] = []
        self._rgb_lengths: List[int] = []

        # Lazy import scapy only when needed
        from scapy.packet import Raw

        # Build templates for each row
        for row in range(height):
            for col_off in range(0, width, MAX_COLS_PER_PACKET):
                chunk_w = min(MAX_COLS_PER_PACKET, width - col_off)
                rgb_len = chunk_w * 3

                # Build template packet
                pixel_count = chunk_w
                ethertype = ETHERTYPE_DATA | ((row >> 8) & 0xFF)
                header = _ether_header(ethertype, src_mac)

                # Payload header: row_lo (1B) + offset (2B) + count (2B) + magic (2B)
                payload_header = struct.pack(
                    ">BHHH",
                    row & 0xFF,
                    col_off,
                    pixel_count,
                    0x0888,
                )

                # Create packet buffer with space for RGB data
                packet = bytearray(header + payload_header + bytes(rgb_len))

                # Pad to minimum frame length
                if len(packet) < MIN_FRAME_LEN:
                    packet.extend(bytes(MIN_FRAME_LEN - len(packet)))

                self._packet_templates.append(packet)
                self._rgb_offsets.append(len(header) + len(payload_header))
                self._rgb_lengths.append(rgb_len)

                # Pre-create Scapy Raw packet object (reuse across frames)
                self._raw_packets.append(Raw(load=bytes(packet)))

    def frame_to_packets_fast(self, frame: np.ndarray) -> List:
        """Convert frame to packets using pre-allocated buffers.

        Returns packets in scan_mode order (sequential or interlaced).

        Parameters
        ----------
        frame : numpy.ndarray
            Shape ``(height, width, 3)``, dtype ``uint8``, RGB order.

        Returns
        -------
        list
            Pre-created Scapy Raw packets with updated data, ordered by scan_mode.
        """
        packet_idx = 0

        # First, update all packets in sequential order (matches allocation order)
        for row in range(self.height):
            # Get row as contiguous memory
            row_data = np.ascontiguousarray(frame[row])
            row_rgb = row_data.tobytes()

            # Split into chunks
            for col_off in range(0, self.width, MAX_COLS_PER_PACKET):
                chunk_w = min(MAX_COLS_PER_PACKET, self.width - col_off)
                rgb_len = chunk_w * 3

                # Copy RGB data into pre-allocated packet buffer
                template = self._packet_templates[packet_idx]
                offset = self._rgb_offsets[packet_idx]

                start = col_off * 3
                template[offset:offset + rgb_len] = row_rgb[start:start + rgb_len]

                # Update the pre-created Raw packet's load in place
                self._raw_packets[packet_idx].load = bytes(template)

                packet_idx += 1

        # Then reorder packets based on scan_mode before returning
        if self.scan_mode != "sequential":
            # Reorder for interlaced or interlaced-32 modes
            # Calculate packets per row (should be 1 for 384-wide display)
            packets_per_row = len(self._raw_packets) // self.height
            reordered = []
            for row in self._row_order:
                start_idx = row * packets_per_row
                end_idx = start_idx + packets_per_row
                reordered.extend(self._raw_packets[start_idx:end_idx])
            return reordered
        else:
            return self._raw_packets

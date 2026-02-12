"""Test-pattern generators for display validation.

Each pattern is a class with a ``generate(t)`` method that returns a
numpy array of shape ``(height, width, 3)`` dtype ``uint8`` (RGB).

The ``PATTERNS`` dict at the bottom maps CLI names to classes.
"""

from __future__ import annotations

import colorsys
import math
from abc import ABC, abstractmethod

import numpy as np


# ---------------------------------------------------------------------------
# Vectorized HSV -> RGB  (S=1, V=1)
# ---------------------------------------------------------------------------

def _hsv_to_rgb(h: np.ndarray) -> np.ndarray:
    """Convert a hue array (0–1, full saturation/value) to uint8 RGB.

    Parameters
    ----------
    h : ndarray
        Hue values in [0, 1), arbitrary shape.

    Returns
    -------
    ndarray
        Shape ``(*h.shape, 3)``, dtype ``uint8``.
    """
    h = h % 1.0
    h6 = h * 6.0
    sector = h6.astype(np.int32) % 6
    f = h6 - np.floor(h6)

    # S=1, V=1  →  p=0, q=1-f, t=f
    # Pre-compute values
    one_minus_f = 1.0 - f

    # Use where() for conditional assignment (faster than select for large arrays)
    r = np.where(sector == 0, 1.0,
         np.where(sector == 1, one_minus_f,
         np.where(sector == 4, f,
         np.where(sector == 5, 1.0, 0.0))))

    g = np.where(sector == 0, f,
         np.where(sector == 1, 1.0,
         np.where(sector == 2, 1.0,
         np.where(sector == 3, one_minus_f, 0.0))))

    b = np.where(sector == 2, f,
         np.where(sector == 3, 1.0,
         np.where(sector == 4, 1.0,
         np.where(sector == 5, one_minus_f, 0.0))))

    return (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class Pattern(ABC):
    """Base class for test patterns."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height

    @abstractmethod
    def generate(self, t: float) -> np.ndarray:
        """Return an ``(H, W, 3)`` uint8 RGB frame at elapsed time *t*."""
        ...


# ---------------------------------------------------------------------------
# Concrete patterns
# ---------------------------------------------------------------------------

class RainbowScroll(Pattern):
    """Horizontal rainbow that scrolls over time."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)
        # Pre-compute column hue ramp (same for every row)
        self._base = np.tile(
            np.linspace(0, 1, width, endpoint=False),
            (height, 1),
        )  # shape (H, W)
        # Pre-allocate hue and output buffers
        self._hue_buffer = np.empty((height, width), dtype=np.float32)

    def generate(self, t: float) -> np.ndarray:
        # Reuse pre-allocated buffer for hue calculation
        np.add(self._base, t * 0.15, out=self._hue_buffer)
        np.mod(self._hue_buffer, 1.0, out=self._hue_buffer)
        return _hsv_to_rgb(self._hue_buffer)


class DiagonalRainbow(Pattern):
    """Diagonal rainbow (x + y) scrolling over time."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)
        x = np.linspace(0, 1, width, endpoint=False)
        y = np.linspace(0, 1, height, endpoint=False)
        xx, yy = np.meshgrid(x, y)
        self._base = (xx + yy) * 0.5   # (H, W)
        # Pre-allocate hue buffer
        self._hue_buffer = np.empty((height, width), dtype=np.float32)

    def generate(self, t: float) -> np.ndarray:
        # Reuse pre-allocated buffer for hue calculation
        np.add(self._base, t * 0.12, out=self._hue_buffer)
        np.mod(self._hue_buffer, 1.0, out=self._hue_buffer)
        return _hsv_to_rgb(self._hue_buffer)


class ColorBars(Pattern):
    """Static RGBCMYW vertical color bars."""

    COLORS = [
        (255, 255, 255),  # white
        (255, 255,   0),  # yellow
        (  0, 255, 255),  # cyan
        (  0, 255,   0),  # green
        (255,   0, 255),  # magenta
        (255,   0,   0),  # red
        (  0,   0, 255),  # blue
    ]

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)
        self._frame = np.zeros((height, width, 3), dtype=np.uint8)
        bar_w = width // len(self.COLORS)
        for i, color in enumerate(self.COLORS):
            x0 = i * bar_w
            x1 = (i + 1) * bar_w if i < len(self.COLORS) - 1 else width
            self._frame[:, x0:x1] = color

    def generate(self, t: float) -> np.ndarray:
        return self._frame


class BouncingSquare(Pattern):
    """A hue-cycling square bouncing around the display."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)
        self._size = max(8, min(width, height) // 6)
        # Pre-allocate frame buffer to avoid allocation every frame
        self._frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def generate(self, t: float) -> np.ndarray:
        # Clear frame (reuse buffer)
        self._frame.fill(0)

        # Lissajous-style bounce
        max_x = self.width - self._size
        max_y = self.height - self._size
        x = int((math.sin(t * 1.3) * 0.5 + 0.5) * max_x)
        y = int((math.sin(t * 0.9) * 0.5 + 0.5) * max_y)

        # Cycling colour
        r, g, b = colorsys.hsv_to_rgb((t * 0.1) % 1.0, 1.0, 1.0)
        color = (int(r * 255), int(g * 255), int(b * 255))

        self._frame[y : y + self._size, x : x + self._size] = color
        return self._frame


class SolidColor(Pattern):
    """Full-screen colour that cycles through the hue wheel."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)
        # Pre-allocate frame buffer
        self._frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def generate(self, t: float) -> np.ndarray:
        r, g, b = colorsys.hsv_to_rgb((t * 0.08) % 1.0, 1.0, 1.0)
        color = (int(r * 255), int(g * 255), int(b * 255))
        # Fill pre-allocated buffer instead of creating new array
        self._frame[:] = color
        return self._frame


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PATTERNS: dict[str, type[Pattern]] = {
    "rainbow":  RainbowScroll,
    "diagonal": DiagonalRainbow,
    "bars":     ColorBars,
    "bounce":   BouncingSquare,
    "solid":    SolidColor,
}

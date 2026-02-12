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
    z = np.zeros_like(h)
    o = np.ones_like(h)

    conds = [sector == k for k in range(6)]
    r = np.select(conds, [o,     1 - f, z,     z,     f,     o    ])
    g = np.select(conds, [f,     o,     o,     1 - f, z,     z    ])
    b = np.select(conds, [z,     z,     f,     o,     o,     1 - f])

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

    def generate(self, t: float) -> np.ndarray:
        hue = (self._base + t * 0.15) % 1.0
        return _hsv_to_rgb(hue)


class DiagonalRainbow(Pattern):
    """Diagonal rainbow (x + y) scrolling over time."""

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)
        x = np.linspace(0, 1, width, endpoint=False)
        y = np.linspace(0, 1, height, endpoint=False)
        xx, yy = np.meshgrid(x, y)
        self._base = (xx + yy) * 0.5   # (H, W)

    def generate(self, t: float) -> np.ndarray:
        hue = (self._base + t * 0.12) % 1.0
        return _hsv_to_rgb(hue)


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

    def generate(self, t: float) -> np.ndarray:
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # Lissajous-style bounce
        max_x = self.width - self._size
        max_y = self.height - self._size
        x = int((math.sin(t * 1.3) * 0.5 + 0.5) * max_x)
        y = int((math.sin(t * 0.9) * 0.5 + 0.5) * max_y)

        # Cycling colour
        r, g, b = colorsys.hsv_to_rgb((t * 0.1) % 1.0, 1.0, 1.0)
        color = (int(r * 255), int(g * 255), int(b * 255))

        frame[y : y + self._size, x : x + self._size] = color
        return frame


class SolidColor(Pattern):
    """Full-screen colour that cycles through the hue wheel."""

    def generate(self, t: float) -> np.ndarray:
        r, g, b = colorsys.hsv_to_rgb((t * 0.08) % 1.0, 1.0, 1.0)
        color = (int(r * 255), int(g * 255), int(b * 255))
        return np.full((self.height, self.width, 3), color, dtype=np.uint8)


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

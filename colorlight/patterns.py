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


class NoSignal(Pattern):
    """Color bars with 'NO SIGNAL' text overlay."""

    COLORS = ColorBars.COLORS  # Reuse RGBCMYW colors

    def __init__(self, width: int, height: int) -> None:
        super().__init__(width, height)

        # Create base color bars
        self._frame = self._create_color_bars()

        # Add text overlay using PIL
        self._add_text_overlay()

    def _create_color_bars(self) -> np.ndarray:
        """Create base color bar pattern (same as ColorBars)."""
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        bar_w = self.width // len(self.COLORS)
        for i, color in enumerate(self.COLORS):
            x0 = i * bar_w
            x1 = (i + 1) * bar_w if i < len(self.COLORS) - 1 else self.width
            frame[:, x0:x1] = color
        return frame

    def _load_font(self, size: int):
        """Load a suitable font with fallbacks."""
        try:
            from PIL import ImageFont
        except ImportError:
            raise ImportError(
                "NoSignal pattern requires Pillow.  "
                "Install with: pip install Pillow"
            )

        # Try common monospace fonts by priority
        font_names = [
            "DejaVuSansMono-Bold.ttf",
            "DejaVuSansMono.ttf",
            "DejaVuSans-Bold.ttf",
            "Arial-Bold.ttf",
            "Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",  # Linux
            "/System/Library/Fonts/Courier.dfont",  # macOS
            "C:\\Windows\\Fonts\\courbd.ttf",  # Windows Courier Bold
            "C:\\Windows\\Fonts\\arialbd.ttf",  # Windows Arial Bold
        ]

        for font_name in font_names:
            try:
                return ImageFont.truetype(font_name, size)
            except (OSError, IOError):
                continue

        # Fallback to default font
        return ImageFont.load_default()

    def _add_text_overlay(self) -> None:
        """Add 'NO SIGNAL' text overlay using PIL."""
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            raise ImportError(
                "NoSignal pattern requires Pillow.  "
                "Install with: pip install Pillow"
            )

        # Convert numpy array to PIL Image
        pil_image = Image.fromarray(self._frame, mode='RGB')
        draw = ImageDraw.Draw(pil_image)

        # Calculate font size (scale with display dimensions)
        font_size = max(40, min(self.height // 20, self.width // 40))
        font = self._load_font(font_size)

        # Text to display
        text = "NO SIGNAL"

        # Calculate centered position
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (self.width - text_width) // 2
        y = (self.height - text_height) // 2

        # Draw text with outline for better visibility
        outline_width = max(2, font_size // 12)
        outline_color = (0, 0, 0)  # Black outline
        fill_color = (255, 255, 255)  # White text

        # Draw outline (8 directions for thickness)
        for offset_x in range(-outline_width, outline_width + 1):
            for offset_y in range(-outline_width, outline_width + 1):
                if offset_x != 0 or offset_y != 0:
                    draw.text(
                        (x + offset_x, y + offset_y),
                        text,
                        font=font,
                        fill=outline_color
                    )

        # Draw main text on top
        draw.text((x, y), text, font=font, fill=fill_color)

        # Convert back to numpy array
        self._frame = np.array(pil_image, dtype=np.uint8)

    def generate(self, t: float) -> np.ndarray:
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
    "nosignal": NoSignal,
}

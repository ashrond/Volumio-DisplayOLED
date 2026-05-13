"""SSD1322 4-bit greyscale OLED driver.

Wraps `luma.oled.device.ssd1322` and installs a vectorized display() override
that replaces the stock pure-Python per-pixel loop with C-level PIL/numpy/
bytes operations. Also houses the OLED burn-in mitigations that piggyback on
the display path:

- Pixel shift (cycles the rendered frame 1px through a 4-position pattern)
- Scanline alternation (dims alternating rows, swaps polarity periodically)

Both are integrated into the fast-path here rather than scattered through
main.py because they're inherently OLED-specific — there's no point pixel-
shifting a TFT, and the scanline trick relies on the 4-bit greyscale pixel
representation we're packing here anyway.

The per-track brightness fade and quiet-hours hide()/show() logic stay in
main.py — those use `device.contrast()` / `device.hide()` / `device.show()`
which our `Device` base class delegates to luma."""

import time
import types
import collections

from PIL import Image, ImageDraw
from luma.core.interface.serial import spi
from luma.oled.device import ssd1322

from .base import Device

# Burn-in mitigation config — read from the runtime configuration loader.
# Importing from config.config here, not main.py, avoids a circular import.
from config.config import (
    SCANLINE_ALT_ENABLED, SCANLINE_DIM_FACTOR, SCANLINE_ALT_INTERVAL_S,
    PIXEL_SHIFT_ENABLED, PIXEL_SHIFT_INTERVAL_S,
)


# numpy is optional. If present, we use the vectorized fast path; otherwise
# the bytes-based fallback (still ~5x faster than the stock luma renderer).
try:
    import numpy as _np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False


# ── Nibble packing LUTs (precomputed once at module load) ──────────────────
_NIBBLE_FROM_BYTE = bytes(b >> 4 for b in range(256))            # 0..255 -> 0..15
_NIBBLE_TO_HIGH   = bytes((b << 4) & 0xFF for b in range(256))   # 0..15  -> 0..240


# ── Scanline alternation ───────────────────────────────────────────────────
# Dims alternating rows of the rendered frame and swaps polarity every
# SCANLINE_ALT_INTERVAL_S seconds. Each pixel spends ~50% of time at reduced
# brightness, halving its effective wear without touching the global contrast.
_SCANLINE_DIM_LUT = bytes(min(15, int(b * SCANLINE_DIM_FACTOR)) for b in range(256))
_scanline_phase = 0
_scanline_last_swap_perf = 0.0


def _maybe_swap_scanline_phase():
    global _scanline_phase, _scanline_last_swap_perf
    if not SCANLINE_ALT_ENABLED:
        return
    now = time.perf_counter()
    if now - _scanline_last_swap_perf >= SCANLINE_ALT_INTERVAL_S:
        _scanline_phase = 1 - _scanline_phase
        _scanline_last_swap_perf = now


def _apply_scanline_dim_bytes(nibbles, seg_width, seg_height, screen_top):
    """Dim every other SCREEN row in `nibbles` (one byte per pixel, value 0-15).
    Uses screen_top so partial-region redraws stay consistent across the screen
    (the framebuffer.redraw bbox can land anywhere)."""
    if not SCANLINE_ALT_ENABLED or seg_height == 0:
        return nibbles
    rows = []
    for r in range(seg_height):
        row = nibbles[r * seg_width:(r + 1) * seg_width]
        if ((screen_top + r) + _scanline_phase) % 2 == 1:
            row = row.translate(_SCANLINE_DIM_LUT)
        rows.append(row)
    return b''.join(rows)


# ── Pixel shift ────────────────────────────────────────────────────────────
# Cycles the rendered frame by 1px through a 4-position pattern. Applied at
# the lowest level — just before SPI — so the rest of the render path is
# blissfully unaware. Adds a single paste per frame (~0.5ms).
_PIXEL_SHIFT_POSITIONS = ((0, 0), (1, 0), (1, 1), (0, 1))
_pixel_shift_idx = 0
_pixel_shift_last_change_perf = 0.0
_shift_canvas = None


def _maybe_advance_pixel_shift():
    global _pixel_shift_idx, _pixel_shift_last_change_perf
    if not PIXEL_SHIFT_ENABLED:
        return
    now = time.perf_counter()
    if now - _pixel_shift_last_change_perf >= PIXEL_SHIFT_INTERVAL_S:
        _pixel_shift_idx = (_pixel_shift_idx + 1) % len(_PIXEL_SHIFT_POSITIONS)
        _pixel_shift_last_change_perf = now


def _apply_pixel_shift(image):
    """Return `image` shifted by the current offset, or the original if no
    shift is active. Reuses a single canvas to avoid per-frame allocation."""
    global _shift_canvas
    if not PIXEL_SHIFT_ENABLED:
        return image
    ox, oy = _PIXEL_SHIFT_POSITIONS[_pixel_shift_idx]
    if ox == 0 and oy == 0:
        return image
    if (_shift_canvas is None
            or _shift_canvas.size != image.size
            or _shift_canvas.mode != image.mode):
        _shift_canvas = Image.new(image.mode, image.size, "black")
    ImageDraw.Draw(_shift_canvas).rectangle(
        (0, 0, image.size[0], image.size[1]), fill="black")
    _shift_canvas.paste(image, (ox, oy))
    return _shift_canvas


# ── Fast display path (numpy + bytes fallback) ─────────────────────────────
# The stock luma greyscale renderer iterates every pixel in pure Python doing
# `grey = (r*306 + g*601 + b*117) >> 14` plus nibble packing — that loop
# dominated CPU on the Pi 0w2 (peak 44.5%, lifetime 12%). Replaced here with
# vectorized C-level ops; both implementations keep framebuffer.redraw()'s
# dirty-region semantics intact.

def _fast_greyscale_display_numpy(self, image):
    assert image.mode == self.mode
    assert image.size == self.size
    image = self.preprocess(image)
    _maybe_advance_pixel_shift()
    image = _apply_pixel_shift(image)
    _maybe_swap_scanline_phase()
    nibble_order = self._nibble_order
    for _, bbox in self.framebuffer.redraw(image):
        left, top, right, bottom = self._inflate_bbox(bbox)
        seg = image.crop((left, top, right, bottom))
        # RGB -> L (PIL C) -> uint8 2D, divided down to 4-bit values.
        arr2d = (_np.asarray(seg.convert("L"), dtype=_np.uint8) >> 4)
        if SCANLINE_ALT_ENABLED and arr2d.shape[0] > 0:
            screen_rows = _np.arange(arr2d.shape[0]) + top
            dim_mask = ((screen_rows + _scanline_phase) & 1) == 1
            arr2d[dim_mask] = (arr2d[dim_mask].astype(_np.uint16)
                               * int(SCANLINE_DIM_FACTOR * 256) // 256).astype(_np.uint8)
        flat = arr2d.ravel()
        if nibble_order == 0:
            packed = (flat[0::2] << 4) | flat[1::2]
        else:
            packed = (flat[1::2] << 4) | flat[0::2]
        self._set_position(top, right, bottom, left)
        self.data(packed.tolist())


def _fast_greyscale_display_bytes(self, image):
    assert image.mode == self.mode
    assert image.size == self.size
    image = self.preprocess(image)
    _maybe_advance_pixel_shift()
    image = _apply_pixel_shift(image)
    _maybe_swap_scanline_phase()
    nibble_order = self._nibble_order
    for _, bbox in self.framebuffer.redraw(image):
        left, top, right, bottom = self._inflate_bbox(bbox)
        seg = image.crop((left, top, right, bottom))
        nibbles = seg.convert("L").tobytes().translate(_NIBBLE_FROM_BYTE)
        nibbles = _apply_scanline_dim_bytes(nibbles, right - left, bottom - top, top)
        if nibble_order == 0:
            highs = nibbles[0::2].translate(_NIBBLE_TO_HIGH)
            lows  = nibbles[1::2]
        else:
            highs = nibbles[1::2].translate(_NIBBLE_TO_HIGH)
            lows  = nibbles[0::2]
        n = len(lows)
        packed = (int.from_bytes(highs, "big") | int.from_bytes(lows, "big")).to_bytes(n, "big")
        self._set_position(top, right, bottom, left)
        self.data(list(packed))


_fast_display_impl = (_fast_greyscale_display_numpy
                      if _HAVE_NUMPY else _fast_greyscale_display_bytes)


# ── Device subclass ────────────────────────────────────────────────────────

class SSD1322Device(Device):
    """SSD1322 4-bit greyscale OLED. Used by the default theme.

    On `__init__` constructs the luma SPI + ssd1322 driver and monkey-patches
    the driver's `display()` with our fast greyscale converter."""

    type_name = "ssd1322"
    category = "OLED"
    is_oled = True

    def _init_luma_device(self):
        serial = spi(device=self.spi_cs, port=self.spi_bus, bus_speed_hz=self.spi_speed_hz)
        self._luma_device = ssd1322(serial)
        # Replace luma's pure-Python per-pixel renderer with our fast path.
        self._luma_device.display = types.MethodType(
            _fast_display_impl, self._luma_device
        )

"""Exact-replica screensaver — replays the per-frame blob data extracted from
assets/idle.gif. Renders each blob as a horizontal Gaussian ellipse sized to
match the actual bounding box from the GIF.

Goal of THIS module: visual fidelity to the original idle.gif. Once the look
is locked, screensaver_random.py can use the same renderer with procedurally
generated FRAMES data instead.

Data format (idle_data.FRAMES):
  list of frames; each frame is a list of (cx, cy, bbox_w, bbox_h, peak_brightness)
  Coordinates are already in screen-space (256x64).
"""
import math
from PIL import Image
from .idle_data import FRAMES

_sprite_cache = {}    # (rx, ry) -> L-mode sprite at peak 255
_scaled_cache = {}    # (rx, ry, peak_bucket) -> pre-scaled L-mode sprite
_frame_idx = 0
_PEAK_BUCKET = 8      # quantize peak to nearest multiple of 8 (32 buckets, 0..255)


def _build_sprite(rx, ry):
    """Sprite for a particle of half-extents (rx, ry).

    For 1x1 (truly tiny) particles, the Gaussian on a 3x3 grid leaves only
    one bright pixel and 4 ~50% pixels — invisible behind a darkened lens.
    So we use a near-flat 3x3 stamp instead (center 255, neighbors 200).
    For larger particles we use a wide Gaussian (sigma = 0.85*r) so the
    bright region fills most of the blob with only the rim tapering."""
    rx = max(rx, 1)
    ry = max(ry, 1)
    w = 2 * rx + 1
    h = 2 * ry + 1
    img = Image.new("L", (w, h), 0)
    pixels = img.load()

    # Special-case the tiny 3x3 stamp so the OLED's gray levels actually show it.
    if rx == 1 and ry == 1:
        pixels[1, 1] = 255
        pixels[0, 1] = 200
        pixels[2, 1] = 200
        pixels[1, 0] = 200
        pixels[1, 2] = 200
        pixels[0, 0] = 90
        pixels[2, 0] = 90
        pixels[0, 2] = 90
        pixels[2, 2] = 90
        return img

    sigma_x = max(0.7, rx * 0.85)
    sigma_y = max(0.7, ry * 0.85)
    for y in range(h):
        for x in range(w):
            dx = x - rx
            dy = y - ry
            d2 = (dx / sigma_x) ** 2 + (dy / sigma_y) ** 2
            v = math.exp(-0.5 * d2)
            pixels[x, y] = int(255 * v)
    return img


def _get_sprite(rx, ry):
    key = (rx, ry)
    if key not in _sprite_cache:
        _sprite_cache[key] = _build_sprite(rx, ry)
    return _sprite_cache[key]


def _adjusted_peak(rx, ry, peak):
    """Size-dependent brightness adjustment.
      Tiny (1x1):  near-max but slightly toned (was 230 → 225, -2%)
      Small (≤2):  +18% boost (was +20%, -2%)
      Medium (3):  -9% (was -8%, -1%)
      Large (≥4):  -5% (was -15%, gives back glow pop)
    """
    if rx <= 1 and ry <= 1:
        return 225
    big = max(rx, ry)
    if big <= 2:
        return min(255, int(peak * 1.18))
    if big >= 4:
        return min(255, int(peak * 1.05))   # large: a bit more glow pop
    if big >= 3:
        return int(peak * 0.91)
    return peak


def _get_scaled(rx, ry, peak):
    """Pre-scaled sprite for a (rx, ry, peak_bucket) combo. Quantizing peak to
    32 buckets means at most ~30*32=960 cache entries, populated lazily."""
    adj = _adjusted_peak(rx, ry, peak)
    pb = (adj // _PEAK_BUCKET) * _PEAK_BUCKET
    if pb < _PEAK_BUCKET:
        return None
    key = (rx, ry, pb)
    cached = _scaled_cache.get(key)
    if cached is None:
        sprite = _get_sprite(rx, ry)
        cached = sprite.point(lambda v, m=pb: (v * m) // 255)
        _scaled_cache[key] = cached
    return cached


def paint(device):
    """Render the current GIF frame programmatically, then advance."""
    global _frame_idx
    w, h = device.width, device.height
    frame = FRAMES[_frame_idx % len(FRAMES)]
    _frame_idx += 1

    img_l = Image.new("L", (w, h), 0)

    # Sort dim-first so brighter sprites win on overlap
    sorted_blobs = sorted(frame, key=lambda b: b[4])

    for cx, cy, bw, bh, peak in sorted_blobs:
        rx = max(1, bw // 2)
        ry = max(1, bh // 2)
        scaled = _get_scaled(rx, ry, peak)
        if scaled is None:
            continue
        sw, sh = scaled.size
        x = int(cx) - sw // 2
        y = int(cy) - sh // 2
        img_l.paste(scaled, (x, y), mask=scaled)

    if device.mode == "L":
        device.display(img_l)
    else:
        device.display(img_l.convert(device.mode))


def reset():
    """Restart from frame 0."""
    global _frame_idx
    _frame_idx = 0

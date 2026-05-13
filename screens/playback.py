import math
import time
from PIL import Image, ImageDraw
from config.config import (
    font_time, font_title, font_artist, text_color, background_color, progress_bar_width,
    PLAYBACK_TIME_Y, PLAYBACK_TITLE_Y, PLAYBACK_PROGRESS_BAR_Y, PLAYBACK_PROGRESS_BAR_HEIGHT,
    PLAYBACK_ARTIST_Y, PLAYBACK_MARQUEE_SPEED_PX_PER_S, PLAYBACK_MARQUEE_GAP_PX,
)

# Local aliases for shorter names in the code below.
SCROLL_SPEED_PX_PER_S = PLAYBACK_MARQUEE_SPEED_PX_PER_S
MARQUEE_GAP_PX        = PLAYBACK_MARQUEE_GAP_PX
TIME_Y                = PLAYBACK_TIME_Y
TITLE_Y               = PLAYBACK_TITLE_Y
PROGRESS_BAR_Y        = PLAYBACK_PROGRESS_BAR_Y
PROGRESS_BAR_H        = PLAYBACK_PROGRESS_BAR_HEIGHT
ARTIST_Y              = PLAYBACK_ARTIST_Y

# Marquee state — tracks scroll offset for long titles between paint calls.
_marquee_offset_px = 0.0
_marquee_last_perf = 0.0
_marquee_title = None  # reset offset when title changes

# Sprite caches — text only re-rasterizes when its content changes.
# Same pattern for marquee (changes per track), clock (changes per minute),
# and artist (changes per track).
_marquee_strip = None
_marquee_strip_title = None
_marquee_strip_mode = None
_marquee_strip_cycle_w = 0

_clock_sprite = None
_clock_sprite_text = None
_clock_sprite_mode = None
_clock_sprite_w = 0

_artist_sprite = None
_artist_sprite_text = None
_artist_sprite_mode = None
_artist_sprite_w = 0


def _get_or_build_sprite(state, text, font, mode):
    """Return (sprite, width) for `text` at `font` in `mode`. `state` is a
    3-tuple (current_sprite, current_text, current_mode); rebuild when text or
    mode differ. Tiny helper to keep the sprite-cache pattern terse."""
    cur_sprite, cur_text, cur_mode = state
    if cur_sprite is not None and cur_text == text and cur_mode == mode:
        return cur_sprite, cur_sprite.size[0]
    w = font.getbbox(text)[2]
    h = font.size + 4
    s = Image.new(mode, (w, h), "black")
    ImageDraw.Draw(s).text((0, 0), text, font=font, fill=text_color)
    return s, w

# Double-buffered persistent canvas — eliminates per-frame Image.new() and
# the GC pressure that comes with it. We alternate between two canvases so
# _last_displayed (held by main.py's crossfade for snapshotting) always points
# to a stable buffer that isn't being mutated.
_canvases = None
_canvas_idx = 0

def needs_marquee(title):
    """True if the title is wider than the screen and should scroll."""
    if not title:
        return False
    return font_title.getbbox(title)[2] > 256  # device width is fixed


def _build_marquee_strip(title, mode):
    """Rasterize the title text into an Image strip one marquee-cycle wide
    (title + trailing gap). Called once per title change; the result is then
    pasted at moving offsets each frame — no per-frame text rendering."""
    title_w = font_title.getbbox(title)[2]
    cycle_w = title_w + MARQUEE_GAP_PX
    strip_h = font_title.size + 4  # comfortably fits the glyph bbox
    strip = Image.new(mode, (cycle_w, strip_h), "black")
    ImageDraw.Draw(strip).text((0, 0), title, font=font_title, fill=text_color)
    return strip, cycle_w


def _advance_marquee(title):
    """Update the scroll offset based on wall-clock time. Returns current offset."""
    global _marquee_offset_px, _marquee_last_perf, _marquee_title
    now = time.perf_counter()
    if title != _marquee_title:
        _marquee_offset_px = 0.0
        _marquee_title = title
    if _marquee_last_perf:
        _marquee_offset_px += SCROLL_SPEED_PX_PER_S * (now - _marquee_last_perf)
    _marquee_last_perf = now
    return _marquee_offset_px


def draw_static_info(draw, device, title, formatted_artist, progress_percent):
    """Draws time, title (centered or scrolling), progress bar, and artist."""
    global _clock_sprite, _clock_sprite_text, _clock_sprite_mode, _clock_sprite_w
    global _artist_sprite, _artist_sprite_text, _artist_sprite_mode, _artist_sprite_w
    screen_width = device.width
    current_time = time.strftime("%I:%M %p")
    title_width = font_title.getbbox(title)[2]
    target_mode = _target_image.mode if _target_image is not None else "L"

    # Time (clock) at top, centered. Cached — rasterized once per minute.
    _clock_sprite, _clock_sprite_w = _get_or_build_sprite(
        (_clock_sprite, _clock_sprite_text, _clock_sprite_mode),
        current_time, font_time, target_mode,
    )
    _clock_sprite_text = current_time
    _clock_sprite_mode = target_mode
    if _target_image is not None:
        _target_image.paste(_clock_sprite, ((screen_width - _clock_sprite_w) // 2, TIME_Y))
    else:
        draw.text(((screen_width - _clock_sprite_w) // 2, TIME_Y),
                  current_time, font=font_time, fill=text_color)

    # Title — centered if it fits, otherwise scrolling marquee with seamless wrap.
    # Marquee path uses a cached pre-rendered sprite (see _build_marquee_strip).
    if title_width > screen_width:
        global _marquee_strip, _marquee_strip_title, _marquee_strip_mode, _marquee_strip_cycle_w
        target_mode = _target_image.mode if _target_image is not None else "L"
        if _marquee_strip_title != title or _marquee_strip_mode != target_mode:
            _marquee_strip, _marquee_strip_cycle_w = _build_marquee_strip(title, target_mode)
            _marquee_strip_title = title
            _marquee_strip_mode = target_mode
        offset = _advance_marquee(title)
        x = -(int(offset) % _marquee_strip_cycle_w)
        if _target_image is not None:
            _target_image.paste(_marquee_strip, (x, TITLE_Y))
            _target_image.paste(_marquee_strip, (x + _marquee_strip_cycle_w, TITLE_Y))
        else:
            # Fallback for any caller that didn't set _target_image (shouldn't
            # happen in normal operation, but keep correctness).
            draw.text((x, TITLE_Y), title, font=font_title, fill=text_color)
            draw.text((x + _marquee_strip_cycle_w, TITLE_Y), title, font=font_title, fill=text_color)
    else:
        draw.text(((screen_width - title_width) // 2, TITLE_Y),
                  title, font=font_title, fill=text_color)

    # Bar area: progress (track) or sweeping-gradient (stream)
    bar_x = (screen_width - progress_bar_width) // 2
    if _is_stream and _target_image is not None:
        _paste_stream_sweep(_target_image, bar_x)
        # Draw the bar outline AFTER the gradient so the bar's frame stays
        # visible — visually contains the sweep within the progress bar shape.
        draw.rectangle((bar_x, PROGRESS_BAR_Y,
                        bar_x + progress_bar_width, PROGRESS_BAR_Y + PROGRESS_BAR_H),
                       outline=text_color)
    else:
        bar_length = max(1, int(progress_bar_width * progress_percent / 100))
        draw.rectangle((bar_x, PROGRESS_BAR_Y, bar_x + progress_bar_width, PROGRESS_BAR_Y + PROGRESS_BAR_H),
                       outline=text_color, fill=background_color)
        draw.rectangle((bar_x, PROGRESS_BAR_Y, bar_x + bar_length, PROGRESS_BAR_Y + PROGRESS_BAR_H),
                       outline=text_color, fill=text_color)

    # Artist — cached, rasterized only when the artist string changes.
    _artist_sprite, _artist_sprite_w = _get_or_build_sprite(
        (_artist_sprite, _artist_sprite_text, _artist_sprite_mode),
        formatted_artist, font_artist, target_mode,
    )
    _artist_sprite_text = formatted_artist
    _artist_sprite_mode = target_mode
    if _target_image is not None:
        _target_image.paste(_artist_sprite, ((screen_width - _artist_sprite_w) // 2, ARTIST_Y))
    else:
        draw.text(((screen_width - _artist_sprite_w) // 2, ARTIST_Y),
                  formatted_artist, font=font_artist, fill=text_color)


# Stream sweep tuning
STREAM_SWEEP_PERIOD_S = 4.0   # full cycle (one sweep across and back)
STREAM_SWEEP_SIGMA_FRAC = 0.28  # width of the bright Gaussian peak as fraction of bar width
_is_stream = False
# The canvas image we're currently painting into — set by display_playback_screen
# so draw_static_info / _paste_stream_sweep can do paste operations (which need
# the actual PIL Image, not just the ImageDraw wrapper).
_target_image = None


def _make_sweep_strip(width, height, peaks, sigma_px):
    """Build a one-row grayscale strip with one or more Gaussian bright peaks,
    then expand vertically. Where peaks overlap, the brighter value wins
    (so converging peaks combine smoothly into a single bright center)."""
    row = Image.new("L", (width, 1), 0)
    pixels = row.load()
    cutoff = 3 * sigma_px
    inv2sig2 = 1.0 / (2 * sigma_px * sigma_px)
    for peak_x in peaks:
        x_lo = max(0, int(peak_x - cutoff))
        x_hi = min(width, int(peak_x + cutoff) + 1)
        for x in range(x_lo, x_hi):
            d = x - peak_x
            v = math.exp(-(d * d) * inv2sig2)
            new_val = int(255 * v)
            if new_val > pixels[x, 0]:   # take brighter on overlap
                pixels[x, 0] = new_val
    if height > 1:
        row = row.resize((width, height), Image.NEAREST)
    return row


def _paste_stream_sweep(target_img, bar_x):
    """Paint two gradient peaks that sweep inward only (sawtooth, no return).
    Each peak emerges at its bar edge and travels toward the center. The bar
    is split at its midpoint: the left peak is rendered only in the left half,
    the right peak only in the right half. As a peak's position moves past
    the center, the part of its Gaussian past the midline is clipped — so the
    bell appears to "trail off" as it disappears into the center void."""
    bar_y_top = PROGRESS_BAR_Y
    W = progress_bar_width
    H = PROGRESS_BAR_H + 1
    half = W // 2  # split point — clip line at the absolute middle

    sigma = max(4.0, W * STREAM_SWEEP_SIGMA_FRAC)
    fade_margin = sigma * 3.0  # extends travel past edges so peaks fade in/out

    t = time.perf_counter()
    # Two overlapping waves on each side, offset by 0.45 of a cycle — slightly
    # tighter than half-period so the gradients sit a hair closer when both
    # are visible. Sawtooth (0 → 1, snap to 0).
    base_phase = (t / STREAM_SWEEP_PERIOD_S) % 1.0
    phases = tuple((base_phase + i * 0.45) % 1.0 for i in range(2))

    # Travel range: peaks start fully off the bar (3σ past the edge, invisible)
    # and end fully past the center (3σ past the midline, invisible). This gives
    # a gentle fade-in at the edge and a clean trail-off at the center.
    travel = half + 2 * fade_margin
    left_peaks = tuple(-fade_margin + travel * p for p in phases)
    right_peaks = tuple(W + fade_margin - travel * p for p in phases)

    # Render each half on its own narrow strip — the strip's width naturally
    # enforces the center clip. Right peaks' x is translated into the right
    # strip's local coordinates (subtract `half`). brighter-wins compositing
    # in _make_sweep_strip handles any overlap.
    left_strip = _make_sweep_strip(half, H, left_peaks, sigma)
    right_strip = _make_sweep_strip(W - half, H,
                                     tuple(p - half for p in right_peaks), sigma)

    if target_img.mode != "L":
        ls = left_strip.convert(target_img.mode)
        rs = right_strip.convert(target_img.mode)
        target_img.paste(ls, (bar_x, bar_y_top), mask=left_strip)
        target_img.paste(rs, (bar_x + half, bar_y_top), mask=right_strip)
    else:
        target_img.paste(left_strip, (bar_x, bar_y_top), mask=left_strip)
        target_img.paste(right_strip, (bar_x + half, bar_y_top), mask=right_strip)


def display_playback_screen(device, title, artist, seek, duration, is_stream=False):
    """Paints ONE frame of the playback screen. Must return quickly — called from render loop hot path."""
    global _is_stream, _target_image, _canvases, _canvas_idx
    _is_stream = is_stream
    seek_seconds = seek / 1000
    progress_percent = min(100, int((seek_seconds / duration) * 100)) if duration > 0 else 0
    formatted_artist = f"-{artist}-" if artist else "-Unknown Artist-"

    # Lazy-init the double-buffered canvas pair (size/mode taken from device).
    if (_canvases is None
            or _canvases[0].size != (device.width, device.height)
            or _canvases[0].mode != device.mode):
        _canvases = [
            Image.new(device.mode, (device.width, device.height), "black"),
            Image.new(device.mode, (device.width, device.height), "black"),
        ]

    canvas = _canvases[_canvas_idx]
    draw = ImageDraw.Draw(canvas)
    # Clear last frame's contents (mode-agnostic via ImageDraw).
    draw.rectangle((0, 0, device.width, device.height), fill="black")
    _target_image = canvas
    draw_static_info(draw, device, title, formatted_artist, progress_percent)
    _target_image = None
    device.display(canvas)
    # Swap so next frame paints into the OTHER buffer; the just-displayed
    # canvas is now what main.py's _last_displayed references and won't be
    # mutated again until 2 frames later (after the next display() returns).
    _canvas_idx = 1 - _canvas_idx

import math
import time
from PIL import Image, ImageDraw
from config.config import (
    font_time, font_title, font_artist, text_color, background_color, progress_bar_width
)

# Marquee state — tracks scroll offset for long titles between paint calls.
_marquee_offset_px = 0.0
_marquee_last_perf = 0.0
_marquee_title = None  # reset offset when title changes

# Marquee tuning (could be moved to theme.toml later if desired)
SCROLL_SPEED_PX_PER_S = 28      # how fast the title scrolls
MARQUEE_GAP_PX = 40             # blank space between repetitions of the title

# Vertical layout. Time is at the top; title sits in the middle with breathing
# room above (away from clock) and small gap below to the progress bar.
TIME_Y = 0           # font_time = 24px tall, ends at y=24
TITLE_Y = 28         # was 22 (overlapped clock); now well clear of it
PROGRESS_BAR_Y = 44
PROGRESS_BAR_H = 3
ARTIST_Y = 50


def needs_marquee(title):
    """True if the title is wider than the screen and should scroll."""
    if not title:
        return False
    return font_title.getbbox(title)[2] > 256  # device width is fixed


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
    screen_width = device.width
    current_time = time.strftime("%I:%M %p")
    time_width = font_time.getbbox(current_time)[2]
    artist_width = font_artist.getbbox(formatted_artist)[2]
    title_width = font_title.getbbox(title)[2]

    # Time (clock) at top, centered
    draw.text(((screen_width - time_width) // 2, TIME_Y),
              current_time, font=font_time, fill=text_color)

    # Title — centered if it fits, otherwise scrolling marquee with seamless wrap
    if title_width > screen_width:
        offset = _advance_marquee(title)
        cycle = title_width + MARQUEE_GAP_PX
        x = -(int(offset) % cycle)
        # Two copies side by side so the wrap-around is invisible
        draw.text((x, TITLE_Y), title, font=font_title, fill=text_color)
        draw.text((x + cycle, TITLE_Y), title, font=font_title, fill=text_color)
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

    # Artist
    draw.text(((screen_width - artist_width) // 2, ARTIST_Y),
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
    global _is_stream, _target_image
    _is_stream = is_stream
    seek_seconds = seek / 1000
    progress_percent = min(100, int((seek_seconds / duration) * 100)) if duration > 0 else 0
    formatted_artist = f"-{artist}-" if artist else "-Unknown Artist-"

    # Build image directly (instead of using canvas()) so the streaming sweep
    # painter can do PIL paste ops on the underlying image.
    img = Image.new(device.mode, (device.width, device.height), "black")
    _target_image = img
    draw = ImageDraw.Draw(img)
    draw_static_info(draw, device, title, formatted_artist, progress_percent)
    _target_image = None
    device.display(img)

"""VFD driver — render-thread architecture.

Design:
- on_message handlers ONLY update state and set current_screen. They return immediately.
- A single render thread is the ONLY writer to SPI. It picks what to paint based on
  current_screen, ticks at RENDER_TICK_SECONDS (default 50ms = 20Hz), and animates
  GIF screens (loading, pause, idle) by stepping through frames at GIF_FRAME_PERIOD.
- This eliminates: concurrent SPI writes, volume-screen twitching, uninterruptible loading.
"""
import os
import sys
import time
import signal
import socket
import subprocess
import queue as _queue
import collections
import threading
import urllib.request
import urllib.parse
import socketio
import json
import logging
from luma.core.interface.serial import spi
from luma.oled.device import ssd1322
from luma.core.render import canvas
from PIL import Image, ImageSequence, ImageDraw, ImageChops

from config.config import (
    log,
    font_title, font_artist, font_volume,
    text_color,
    GIF_PATH_LOADING,
    PLAYBACK_REFRESH_SECONDS, VOLUME_HOLD_SECONDS,
    IDLE_AFTER_STOP_SECONDS, SCREEN_OFF_AFTER_IDLE_SECONDS, RENDER_TICK_SECONDS,
    PAUSE_TO_PLAY_DEBOUNCE_SECONDS, TRANSITION_HOLD_AT_END_SECONDS, TRANSITION_FADE_PORTION_CFG,
    VOLUME_MAX, VOLUME_BUTTON_RECENT_WINDOW,
    SCREENSAVER_MODE, SCREENSAVER_TARGET_POPULATION, SCREENSAVER_SPAWN_RATE,
    SCREENSAVER_MEDIAN_SPEED, SCREENSAVER_DRIFT_X, SCREENSAVER_DRIFT_Y,
    FADE_SECONDS,
    MENU_TIMEOUT_SECONDS, MENU_IR_UDP_PORT,
    QUIET_HOURS_START, QUIET_HOURS_END,
    PIXEL_SHIFT_ENABLED, PIXEL_SHIFT_INTERVAL_S,
    TRACK_FADE_ENABLED, TRACK_FADE_MAX, TRACK_FADE_MIN, TRACK_FADE_MIN_REMAINING_S,
)
from screens.startup import display_startup
from screens.playback import display_playback_screen, needs_marquee
from screens import screensaver, screensaver_replay
from screens.menu import paint_menu

_HERE = os.path.dirname(os.path.abspath(__file__))
GIF_PATH_IDLE = os.path.join(_HERE, "assets/idle.gif")
GIF_PATH_PLAY_TO_PAUSE = os.path.join(_HERE, "assets/play-to-pause.gif")
GIF_PATH_PAUSE_TO_PLAY = os.path.join(_HERE, "assets/pause-to-play.gif")
# Note: pause.gif is intentionally retired; idle.gif is now the universal
# screensaver content for both pause and stop states.
VOLUMIO_WS_URL = "http://localhost:3000"

# Animated screens advance one GIF frame per this interval (10fps). Render tick is faster
# (20Hz) for snappy state transitions, but full SPI repaints at 20Hz are wasteful.
GIF_FRAME_PERIOD = 0.1
# Screensaver runs faster — the source idle.gif was authored at 25fps (40ms).
SCREENSAVER_FRAME_PERIOD = 0.04

# --- SPI device ---
# SSD1322 supports 4-bit (16-level) grayscale. luma.core only accepts modes
# "1", "RGB", "RGBA" — there's no "L" option (verified 2026-05-10). So we keep
# RGB and override display() with a vectorized RGB→4bpp conversion below;
# the stock implementation has a pure-Python per-pixel loop that dominates CPU.
serial = spi(device=0, port=0, bus_speed_hz=8000000)
device = ssd1322(serial)


# --- Fast device.display() override ---
# The stock luma greyscale renderer iterates every pixel in Python doing
# `grey = (r*306 + g*601 + b*117) >> 14` plus nibble packing. On a Pi 0w2
# that's ~80ms for a full-screen frame — the dominant cost in our render path.
# We replace it with vectorized C-level ops. Two implementations:
#   - numpy path (preferred): clearest and fastest
#   - bytes/int fallback: works without numpy, still much faster than stock
# Both keep the framebuffer.redraw() dirty-region logic intact.
import types as _types
try:
    import numpy as _np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False

_NIBBLE_FROM_BYTE = bytes(b >> 4 for b in range(256))            # 0..255 -> 0..15
_NIBBLE_TO_HIGH   = bytes((b << 4) & 0xFF for b in range(256))   # 0..15  -> 0..240

# --- Pixel shift (burn-in mitigation) ---
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
    """Return image shifted by the current pixel-shift offset, or the original
    image if no shift is active. Reuses a single canvas to avoid per-frame
    allocation."""
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


def _fast_greyscale_display_numpy(self, image):
    assert image.mode == self.mode
    assert image.size == self.size
    image = self.preprocess(image)
    _maybe_advance_pixel_shift()
    image = _apply_pixel_shift(image)
    nibble_order = self._nibble_order
    for _, bbox in self.framebuffer.redraw(image):
        left, top, right, bottom = self._inflate_bbox(bbox)
        seg = image.crop((left, top, right, bottom))
        # RGB -> L (PIL C) -> uint8 numpy array, divided down to 4-bit values.
        flat = (_np.asarray(seg.convert("L"), dtype=_np.uint8).ravel() >> 4)
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
    nibble_order = self._nibble_order
    for _, bbox in self.framebuffer.redraw(image):
        left, top, right, bottom = self._inflate_bbox(bbox)
        seg = image.crop((left, top, right, bottom))
        nibbles = seg.convert("L").tobytes().translate(_NIBBLE_FROM_BYTE)
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


_fast_display_impl = _fast_greyscale_display_numpy if _HAVE_NUMPY else _fast_greyscale_display_bytes
device.display = _types.MethodType(_fast_display_impl, device)

# --- Screen-change crossfade ---
# Wrap device.display() so that whenever a screen change is signaled (via
# _start_fade() in _set_screen_unsafe), subsequent paints are blended against
# the captured pre-change frame for FADE_SECONDS. After the fade, normal
# display resumes. Single thread (the render thread) calls device.display,
# so no locking needed on _last_displayed / _fade_state.
_last_displayed = None
_transition_behind_img = None   # snapshot of the screen we left (FROM)
_transition_ahead_img = None    # snapshot/render of where we're going (TO) — used for static targets like playback
_transition_start_perf = 0.0    # perf_counter at transition entry — drives the time-based progress curve

# Pulled from theme.toml [timing] so the future webUI can edit them.
TRANSITION_HOLD_AT_END_S = TRANSITION_HOLD_AT_END_SECONDS
TRANSITION_FADE_PORTION = TRANSITION_FADE_PORTION_CFG
# Track entry into idle so we can fall to screen_off after SCREEN_OFF_AFTER_IDLE_SECONDS.
_idle_entered_perf = 0.0
_screen_off_entered_perf = 0.0
_screen_off_panel_hidden = False

# --- Menu system ---
# Stack-based: top-level menu pushes submenus, MENU button pops back, closes
# when popping past the root. Each menu PAGE has items (label_fn_or_str,
# on_select callable) and a current selected_idx.
_menu_entered_perf = 0.0
_menu_last_input_perf = 0.0
_menu_pre_screen = "playback"
_menu_stack = []                       # list of MenuPage (top-of-stack is the current page)
_menu_stack_lock = threading.Lock()    # protects _menu_stack independently of state_lock
# Mirror Volumio-side state for the menu labels (updated in pushState handler)
last_random = False
last_repeat = False
last_repeat_single = False
# Service type — "webradio" means streaming (no finite duration), drives the
# chasing-gradient indicator instead of the normal progress bar.
last_service = ""
# IR button event queue — populated by ir_udp_listener thread, consumed by ir_dispatcher thread.
_ir_queue = _queue.Queue()


class _MenuPage:
    __slots__ = ("items", "title", "selected_idx")
    def __init__(self, items, title, selected_idx=0):
        self.items = items          # list of (label_or_callable, on_select_callable_or_None)
        self.title = title
        self.selected_idx = selected_idx


def _http_get(path, timeout=2.0):
    """Synchronous GET against Volumio's REST API. Returns parsed JSON or raises."""
    url = "http://localhost:3000" + path
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _label_for(item):
    """Resolve a menu item's label (callable or string)."""
    label = item[0]
    return label() if callable(label) else label


def _menu_current_page():
    with _menu_stack_lock:
        return _menu_stack[-1] if _menu_stack else None


def _menu_set_screen(name):
    """Switch the global screen state, briefly acquiring state_lock."""
    with state_lock:
        _set_screen_unsafe(name)


def _menu_close():
    """Close the menu entirely and return to the screen we came from."""
    with _menu_stack_lock:
        _menu_stack.clear()
    _menu_set_screen(_menu_pre_screen or "playback")


def _menu_pop():
    """Go back one level. If at top, close the menu."""
    with _menu_stack_lock:
        if len(_menu_stack) > 1:
            _menu_stack.pop()
            still_open = True
        else:
            _menu_stack.clear()
            still_open = False
    if still_open:
        global _menu_last_input_perf
        _menu_last_input_perf = time.perf_counter()
        render_wake.set()
    else:
        _menu_set_screen(_menu_pre_screen or "playback")


def _menu_push(page):
    with _menu_stack_lock:
        _menu_stack.append(page)
    global _menu_last_input_perf
    _menu_last_input_perf = time.perf_counter()
    render_wake.set()


def _menu_open_tracks():
    """Fetch current queue, push as submenu."""
    try:
        data = _http_get("/api/v1/getQueue")
        queue = data.get("queue", []) if isinstance(data, dict) else []
    except Exception as e:
        log.warning("fetch queue failed: %s", e)
        return
    items = []
    for idx, track in enumerate(queue):
        name = track.get("name") or track.get("title") or f"Track {idx+1}"
        items.append((name, _menu_make_jump_to_queue(idx)))
    if not items:
        items.append(("(queue is empty)", None))
    items.append(("< Back", _menu_pop))
    _menu_push(_MenuPage(items, "Tracks"))


def _menu_make_jump_to_queue(idx):
    def action():
        try:
            sio.emit("stop")
            sio.emit("play", {"value": idx})
        except Exception as e:
            log.warning("jump to queue idx %d failed: %s", idx, e)
        _menu_close()
    return action


def _menu_open_playlists():
    """Fetch saved playlists, push as submenu."""
    try:
        playlists = _http_get("/api/v1/listplaylists")
        if not isinstance(playlists, list):
            playlists = []
    except Exception as e:
        log.warning("fetch playlists failed: %s", e)
        return
    items = []
    for name in playlists:
        items.append((str(name), _menu_make_play_playlist(str(name))))
    if not items:
        items.append(("(no saved playlists)", None))
    items.append(("< Back", _menu_pop))
    _menu_push(_MenuPage(items, "Playlists"))


def _menu_make_play_playlist(name):
    def action():
        try:
            sio.emit("playPlaylist", {"name": name})
        except Exception as e:
            log.warning("playPlaylist %s failed: %s", name, e)
        _menu_close()
    return action


def _menu_open_webradio():
    """Fetch user's saved web radios, push as submenu.
    Tries /api/v1/browse?uri=radio/myWebRadio first, falls back to favourites."""
    items_data = []
    for src in ("radio/myWebRadio", "radio/favourites"):
        try:
            data = _http_get("/api/v1/browse?" + urllib.parse.urlencode({"uri": src}))
            for lst in data.get("navigation", {}).get("lists", []):
                for itm in lst.get("items", []):
                    items_data.append(itm)
        except Exception as e:
            log.debug("fetch webradio %s failed: %s", src, e)
    items = []
    for itm in items_data:
        title = itm.get("title") or itm.get("name") or "Unknown"
        items.append((title, _menu_make_play_uri(itm)))
    if not items:
        items.append(("(no saved web radios)", None))
    items.append(("< Back", _menu_pop))
    _menu_push(_MenuPage(items, "Webradio"))


def _menu_make_play_uri(item):
    def action():
        try:
            sio.emit("replaceAndPlay", item)
        except Exception as e:
            log.warning("replaceAndPlay failed: %s", e)
        _menu_close()
    return action


def _menu_action_toggle_shuffle():
    global last_random
    new_val = not last_random
    try:
        sio.emit("setRandom", {"value": new_val})
        last_random = new_val
    except Exception as e:
        log.warning("toggle shuffle failed: %s", e)


def _menu_action_toggle_repeat():
    global last_repeat, last_repeat_single
    if not last_repeat and not last_repeat_single:
        new_repeat, new_single = True, False
    elif last_repeat and not last_repeat_single:
        new_repeat, new_single = True, True
    else:
        new_repeat, new_single = False, False
    try:
        sio.emit("setRepeat", {"value": new_repeat, "repeatSingle": new_single})
        last_repeat, last_repeat_single = new_repeat, new_single
    except Exception as e:
        log.warning("toggle repeat failed: %s", e)


def _menu_action_reboot():
    log.warning("MENU: reboot requested")
    try:
        subprocess.Popen(["sudo", "/sbin/reboot"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.error("reboot failed: %s", e)


def _menu_open_bluetooth_stub():
    items = [("(coming soon)", None), ("< Back", _menu_pop)]
    _menu_push(_MenuPage(items, "Bluetooth"))


def _build_top_level_menu():
    return [
        ("Tracks",                                                       _menu_open_tracks),
        ("Playlists",                                                    _menu_open_playlists),
        ("Webradio",                                                     _menu_open_webradio),
        (lambda: "Shuffle: " + ("ON" if last_random else "OFF"),         _menu_action_toggle_shuffle),
        (lambda: ("Repeat: Single" if last_repeat_single
                  else "Repeat: All" if last_repeat else "Repeat: Off"), _menu_action_toggle_repeat),
        ("Bluetooth",                                                    _menu_open_bluetooth_stub),
        ("Reboot",                                                       _menu_action_reboot),
        ("Close",                                                        _menu_close),
    ]
# Fade state: (start_perf, duration, prev_image, new_image_or_None)
# new_image is captured on the FIRST paint after the fade starts, then frozen
# so we blend two static frames smoothly. Without this freeze, the fade would
# blend the old image against a shifting new image (animated screens, etc.),
# which visually looks like "cut to noise + fade in" rather than a crossfade.
_fade_state = None
_device_display_orig = device.display


def _fade_display(img):
    global _last_displayed, _fade_state
    if _fade_state is not None:
        start, duration, prev_img, new_img = _fade_state
        # Capture the first post-fade paint as the frozen target image
        if new_img is None:
            new_img = img.copy()
            _fade_state = (start, duration, prev_img, new_img)
        elapsed = time.perf_counter() - start
        if elapsed < duration:
            # Ease-in (quadratic): alpha grows slowly at first, then quickly.
            # Result: previous screen lingers visible for the first portion of
            # the fade, then drops off in the last portion. Feels like 'old
            # holds, new arrives' rather than uniform crossfade.
            t = elapsed / duration
            alpha = t * t
            try:
                blended = Image.blend(prev_img, new_img, alpha)
            except Exception:
                blended = img
                _fade_state = None
            _device_display_orig(blended)
            _last_displayed = blended
            return
        _fade_state = None
    _device_display_orig(img)
    _last_displayed = img


def _start_fade():
    """Snapshot the current display and begin a crossfade to whatever paints next."""
    global _fade_state
    if _last_displayed is not None and FADE_SECONDS > 0:
        _fade_state = (time.perf_counter(), FADE_SECONDS, _last_displayed.copy(), None)


device.display = _fade_display

# --- State (mutations guarded by state_lock) ---
state_lock = threading.Lock()
current_screen = "startup"
last_volume = None
last_title = None
last_artist = None
last_seek = -1
last_duration = 1
last_status = None
last_event_wall = 0.0           # time of most recent play pushState
last_volume_event_wall = 0.0    # time of most recent volume change
last_stop_wall = 0.0            # time when status=stop first seen
last_status_change_wall = 0.0   # time of last status (play/pause/stop) transition — for IR-bounce debounce
volume_initialized = False
PAUSE_TO_PLAY_DEBOUNCE = PAUSE_TO_PLAY_DEBOUNCE_SECONDS
render_wake = threading.Event()
shutdown_event = threading.Event()  # set on SIGTERM/SIGINT

# --- Burn-in mitigation: quiet hours + per-track brightness fade ---
_was_in_quiet_hours = False        # so we can detect the boundary on exit
_last_contrast_set = -1            # debounce SPI writes for contrast
_last_contrast_update_perf = 0.0
_CONTRAST_UPDATE_INTERVAL_S = 1.0  # update brightness at most once per second

# The "fade origin" is the seek (in ms) at which brightness is at TRACK_FADE_MAX.
# Brightness ramps linearly down to TRACK_FADE_MIN at end-of-track. New tracks
# reset this to 0; pause/menu reset it to current seek so the user gets a bright
# screen and a fresh fade over the remaining track.
_fade_origin_seek_ms = 0


def _in_quiet_hours():
    """True if current local time falls in [QUIET_HOURS_START, QUIET_HOURS_END).
    Handles ranges that wrap midnight (start > end)."""
    if QUIET_HOURS_START == QUIET_HOURS_END:
        return False
    h = time.localtime().tm_hour
    if QUIET_HOURS_START < QUIET_HOURS_END:
        return QUIET_HOURS_START <= h < QUIET_HOURS_END
    return h >= QUIET_HOURS_START or h < QUIET_HOURS_END


def _reset_brightness_fade(origin_seek_ms, reason):
    """Plant a new fade origin: brightness is at MAX here, MIN at end-of-track.
    If the remaining track time is shorter than TRACK_FADE_MIN_REMAINING_S,
    push the origin past end-of-track so brightness stays at MAX for the rest
    of the track (avoids an annoyingly fast fade in the last few seconds).
    Forces an immediate brightness update so the user-visible effect is snappy."""
    global _fade_origin_seek_ms, _last_contrast_update_perf
    if last_duration > 0:
        end_ms = last_duration * 1000
        if end_ms - origin_seek_ms < TRACK_FADE_MIN_REMAINING_S * 1000:
            origin_seek_ms = end_ms  # clamp past end → formula will hold at MAX
    _fade_origin_seek_ms = origin_seek_ms
    _last_contrast_update_perf = 0  # force next _update_track_brightness to run
    log.info("brightness fade reset (origin=%dms, reason=%s)", origin_seek_ms, reason)


def _update_track_brightness():
    """Drive the SSD1322 contrast register based on playback position within
    the current fade segment (origin → end-of-track). Brightness is MAX at the
    origin and MIN at end-of-track; for seek positions before the origin, also
    MAX. Throttled to 1 Hz."""
    global _last_contrast_set, _last_contrast_update_perf
    if not TRACK_FADE_ENABLED:
        return
    now = time.perf_counter()
    if now - _last_contrast_update_perf < _CONTRAST_UPDATE_INTERVAL_S:
        return
    _last_contrast_update_perf = now

    # Only manage brightness while in play/pause; other states keep whatever
    # contrast we last set (no harm done while panel is hidden, anyway).
    if last_status not in ("play", "pause"):
        return
    # Webradio / unknown-duration: keep at max.
    if last_duration <= 0:
        target = TRACK_FADE_MAX
    else:
        end_ms = last_duration * 1000
        if last_seek <= _fade_origin_seek_ms or end_ms <= _fade_origin_seek_ms:
            target = TRACK_FADE_MAX
        elif last_seek >= end_ms:
            target = TRACK_FADE_MIN
        else:
            progress = (last_seek - _fade_origin_seek_ms) / (end_ms - _fade_origin_seek_ms)
            target = int(TRACK_FADE_MAX - (TRACK_FADE_MAX - TRACK_FADE_MIN) * progress)
    target = max(0, min(255, target))
    if target == _last_contrast_set:
        return
    try:
        device.contrast(target)
        log.info("brightness: contrast=%d (seek=%dms origin=%dms duration=%ds status=%s)",
                 target, last_seek, _fade_origin_seek_ms, last_duration, last_status)
        _last_contrast_set = target
    except Exception as e:
        log.warning("device.contrast(%d) failed: %s", target, e)


# In-memory trace ring buffer for diagnosing Heisenbugs that don't reproduce
# under DEBUG file logging (the I/O latency masks timing-sensitive races).
# `_trace()` is microsecond-cheap; SIGUSR1 dumps the buffer to /tmp/vfd-trace.log.
_TRACE_MAX = 4000
_trace_buffer = collections.deque(maxlen=_TRACE_MAX)
_trace_lock = threading.Lock()


def _trace(msg):
    ts = time.perf_counter()
    with _trace_lock:
        _trace_buffer.append((ts, msg))
SLOW_PAINT_MS = 30
SLOW_HANDLER_MS = 20
last_screen_change_perf = 0.0

# Watchdog: render thread updates this on every successful tick. The watchdog
# thread checks that the gap stays under WATCHDOG_TIMEOUT_S; if it doesn't,
# the process exits non-zero so systemd (or whatever supervisor) restarts us.
last_render_tick_wall = time.time()
WATCHDOG_TIMEOUT_S = 60
WATCHDOG_CHECK_INTERVAL_S = 15

# --- GIF frame cache ---
_frame_cache = {}


def _load_frames(path, resize_to_screen):
    """Decode every frame of a GIF into PIL Images in the device's native mode
    (preserves grayscale). Resize uses LANCZOS for high-quality downsampling."""
    if path in _frame_cache:
        return _frame_cache[path]
    if not os.path.exists(path):
        log.warning("GIF not found: %s", path)
        _frame_cache[path] = []
        return []
    try:
        with Image.open(path) as gif:
            frames = []
            for f in ImageSequence.Iterator(gif):
                # Convert in the GIF's own palette space first to compose any
                # transparency cleanly, THEN to the device's mode so paste()
                # later is a direct copy (no per-paint conversion cost).
                frame = f.convert(device.mode)
                if resize_to_screen and frame.size != (device.width, device.height):
                    frame = frame.resize((device.width, device.height), Image.LANCZOS)
                frames.append(frame)
    except Exception as e:
        log.error("failed to load GIF %s: %s", path, e)
        _frame_cache[path] = []
        return []
    _frame_cache[path] = frames
    log.info("loaded %d frames from %s (mode=%s, size=%dx%d)",
             len(frames), os.path.basename(path),
             frames[0].mode if frames else "?",
             frames[0].size[0] if frames else 0,
             frames[0].size[1] if frames else 0)
    return frames


def _prewarm_gifs():
    """Decode all animated GIFs into memory at startup, so first-use paints don't stall.
    Cache key is path-only, so the resize_to_screen flag here MUST match the painters'."""
    _load_frames(GIF_PATH_LOADING, resize_to_screen=True)
    _load_frames(GIF_PATH_IDLE, resize_to_screen=True)
    _load_frames(GIF_PATH_PLAY_TO_PAUSE, resize_to_screen=True)
    _load_frames(GIF_PATH_PAUSE_TO_PLAY, resize_to_screen=True)


def _coerce_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# --- Painters (each writes ONE frame; called only from render thread) ---

def _paint_volume(volume):
    with canvas(device) as draw:
        draw.rectangle((0, 0, device.width, device.height), fill="black")
        text = f"Volume: {volume}"
        tw = font_volume.getbbox(text)[2]
        tx = (device.width - tw) // 2
        ty = (device.height - font_volume.size) // 2
        draw.text((tx, ty), text, font=font_volume, fill=text_color)
        indicator = None
        if volume >= VOLUME_MAX:
            indicator = "MAX"
        elif volume <= 0:
            indicator = "MIN"
        if indicator:
            iw = font_title.getbbox(indicator)[2]
            ix = (device.width - iw) // 2
            draw.text((ix, device.height - 14), indicator, font=font_title, fill=text_color)


def _paint_loading(idx, error=""):
    frames = _load_frames(GIF_PATH_LOADING, resize_to_screen=True)
    if not frames:
        return
    f = frames[idx % len(frames)]
    # Build the image directly in device mode so we can paste the GIF frame
    # at full intensity (preserving grayscale), then draw text overlays on top.
    img = Image.new(device.mode, (device.width, device.height), "black")
    img.paste(f, (0, 0))
    draw = ImageDraw.Draw(img)
    text = "LOADING"
    tw = font_title.getbbox(text)[2]
    tx = (device.width - tw) // 2
    draw.text((tx, 0), text, font=font_title, fill="white")
    if error:
        ew = font_artist.getbbox(error)[2]
        ex = (device.width - ew) // 2
        draw.text((ex, device.height - 12), error, font=font_artist, fill="white")
    device.display(img)


def _paint_idle(idx):
    """Universal screensaver — used for both pause and idle states.
    Dispatches based on [screensaver] mode."""
    if SCREENSAVER_MODE == "replay":
        screensaver_replay.paint(device)
        return
    if SCREENSAVER_MODE == "procedural":
        screensaver.paint(
            device,
            target_population=SCREENSAVER_TARGET_POPULATION,
            spawn_rate=SCREENSAVER_SPAWN_RATE,
            drift_x=SCREENSAVER_DRIFT_X,
            drift_y=SCREENSAVER_DRIFT_Y,
            median_speed=SCREENSAVER_MEDIAN_SPEED,
        )
        return
    # Fallback: looping GIF
    frames = _load_frames(GIF_PATH_IDLE, resize_to_screen=True)
    if not frames:
        return
    f = frames[idx % len(frames)]
    img = Image.new(device.mode, (device.width, device.height), "black")
    img.paste(f, (0, 0))
    device.display(img)


def _paint_transition_overlay(symbol_frame, behind, ahead, alpha, symbol_env):
    """Composite a transition GIF frame OVER a primary-screen crossfade.

    Layers:
      - background: blend(behind, ahead, alpha)  — primary screens crossfading
      - overlay:    symbol_frame * symbol_env    — lighter-blended on top, but
                                                   intensity is scaled by an
                                                   envelope so the symbol fades
                                                   IN at start and OUT at end
                                                   (no sharp pop in/out)
    """
    if behind is None and ahead is None:
        bg = Image.new(device.mode, (device.width, device.height), "black")
    elif behind is None:
        bg = ahead
    elif ahead is None:
        dim = max(0, int(255 * (1.0 - alpha)))
        bg = behind.point(lambda v, m=dim: (v * m) // 255)
    else:
        bg = Image.blend(behind, ahead, alpha)
    if symbol_env >= 0.99:
        symbol = symbol_frame
    else:
        scale = max(0, int(255 * symbol_env))
        symbol = symbol_frame.point(lambda v, m=scale: (v * m) // 255)
    composite = ImageChops.lighter(bg, symbol)
    device.display(composite)


def _render_playback_image():
    """Render one playback frame to an Image without calling device.display.
    Used as the 'ahead' target during transition_to_play."""
    from screens.playback import draw_static_info
    img = Image.new(device.mode, (device.width, device.height), "black")
    draw = ImageDraw.Draw(img)
    title = last_title if last_title is not None else ""
    artist = last_artist if last_artist is not None else ""
    seek = max(0, last_seek)
    duration = max(1, last_duration)
    seek_seconds = seek / 1000
    progress_percent = min(100, int((seek_seconds / duration) * 100)) if duration > 0 else 0
    formatted_artist = f"-{artist}-" if artist else "-Unknown Artist-"
    draw_static_info(draw, device, title, formatted_artist, progress_percent)
    return img


# --- Render thread ---

def render_loop():
    """Outer wrapper that catches any unhandled exception so we don't silently
    leak a dead thread. On crash, log + exit non-zero for service-manager restart."""
    try:
        _render_loop_inner()
    except BaseException as e:
        log.critical("render loop crashed: %s", e, exc_info=True)
        os._exit(3)


def _render_loop_inner():
    global last_render_tick_wall, _idle_entered_perf, _screen_off_entered_perf
    global _screen_off_panel_hidden, _transition_behind_img, _transition_ahead_img
    global _transition_start_perf, _was_in_quiet_hours
    log.info("render loop started (tick=%.2fs, gif=%dfps)", RENDER_TICK_SECONDS, int(1.0 / GIF_FRAME_PERIOD))
    idle_idx = 0
    loading_idx = 0
    transition_idx = 0
    last_playback_paint = 0.0
    last_idle_paint = 0.0
    last_loading_paint = 0.0
    last_transition_paint = 0.0
    last_painted_screen = None
    last_painted_volume = None
    last_logged_change_perf = 0.0

    while not shutdown_event.is_set():
        # During a fade, tick faster so the blend advances over multiple frames
        # instead of one long jump. ~33ms = ~30Hz during fade.
        wait_timeout = 0.033 if _fade_state is not None else RENDER_TICK_SECONDS
        render_wake.wait(timeout=wait_timeout)
        render_wake.clear()
        if shutdown_event.is_set():
            break
        wake_perf = time.perf_counter()
        fade_active = _fade_state is not None

        with state_lock:
            change_perf = last_screen_change_perf
            screen = current_screen
            volume = last_volume
            title = last_title
            artist = last_artist
            seek = last_seek
            duration = last_duration
            status = last_status
            volume_event_wall = last_volume_event_wall
            stop_wall = last_stop_wall
            event_wall = last_event_wall

        now = time.time()

        # --- Auto-transitions ---
        if screen == "volume" and now - volume_event_wall > VOLUME_HOLD_SECONDS:
            with state_lock:
                if current_screen == "volume":
                    # pause/stop both map to 'idle' (same screensaver content).
                    # We don't fire transition_to_* GIFs from here — coming back
                    # from a brief volume tap shouldn't dramatize as sleep/wake.
                    if last_status == "pause":
                        target = "idle"
                    elif last_status == "stop":
                        if last_stop_wall and (time.time() - last_stop_wall) > IDLE_AFTER_STOP_SECONDS:
                            target = "idle"
                        else:
                            target = "loading"
                    else:
                        target = "playback"
                    log.info("volume hold expired (>%.1fs), -> %s", VOLUME_HOLD_SECONDS, target)
                    _set_screen_unsafe(target)
            continue

        if screen == "loading" and status == "stop" and stop_wall and now - stop_wall > IDLE_AFTER_STOP_SECONDS:
            with state_lock:
                if current_screen == "loading" and last_status == "stop":
                    log.info("stop persisted >%.1fs, -> idle", IDLE_AFTER_STOP_SECONDS)
                    _set_screen_unsafe("idle")
            continue

        # Auto-transition: menu auto-closes after MENU_TIMEOUT_SECONDS of no input
        if (screen == "menu" and _menu_last_input_perf > 0
                and time.perf_counter() - _menu_last_input_perf > MENU_TIMEOUT_SECONDS):
            with state_lock:
                if current_screen == "menu":
                    log.info("menu auto-close (>%.1fs idle), -> %s",
                             MENU_TIMEOUT_SECONDS, _menu_pre_screen)
                    _set_screen_unsafe(_menu_pre_screen or "playback")
            continue

        # Burn-in mitigation: drive contrast based on track progress (no-op
        # outside play/pause; throttled internally to 1 Hz).
        _update_track_brightness()

        # Burn-in mitigation: quiet hours force the panel into screen_off.
        # On exit we wake to whatever screen makes sense for current state.
        in_quiet = _in_quiet_hours()
        if in_quiet and current_screen != "screen_off":
            with state_lock:
                if current_screen != "screen_off":
                    log.info("entering quiet hours (%02d:00–%02d:00), -> screen_off",
                             QUIET_HOURS_START, QUIET_HOURS_END)
                    _set_screen_unsafe("screen_off")
            _was_in_quiet_hours = True
            continue
        if _was_in_quiet_hours and not in_quiet:
            wake_target = "playback" if last_status == "play" else "idle"
            woke = False
            with state_lock:
                if current_screen == "screen_off":
                    log.info("quiet hours ended, -> %s", wake_target)
                    _set_screen_unsafe(wake_target)
                    woke = True
            _was_in_quiet_hours = False
            if woke:
                # Skip the rest of this tick — `screen` is still the stale
                # "screen_off" value and would re-hide the panel we just woke.
                continue
        else:
            _was_in_quiet_hours = in_quiet

        # Auto-transition: idle has been showing for too long → fade to black + sleep panel.
        # The `last_painted_screen == "idle"` gate prevents this from firing on the FIRST
        # tick after entering idle, before the entry block below has refreshed
        # `_idle_entered_perf`. Without it, a stale timestamp from a prior idle session
        # would immediately trip screen_off the moment we enter idle from a transition.
        if (screen == "idle" and last_painted_screen == "idle"
                and _idle_entered_perf > 0
                and SCREEN_OFF_AFTER_IDLE_SECONDS > 0
                and time.perf_counter() - _idle_entered_perf > SCREEN_OFF_AFTER_IDLE_SECONDS):
            with state_lock:
                if current_screen == "idle":
                    log.info("idle persisted >%.0fs, -> screen_off (panel sleep)",
                             SCREEN_OFF_AFTER_IDLE_SECONDS)
                    _set_screen_unsafe("screen_off")
            continue

        # Reset animation indices when entering an animated screen freshly
        if screen != last_painted_screen:
            if screen == "idle":
                idle_idx = 0
                last_idle_paint = 0
                _idle_entered_perf = time.perf_counter()
                # Don't reset the screensaver when we're flowing in from
                # transition_to_pause — its particle state was already animated
                # in during the transition. Resetting would pop the screen back
                # to empty and re-spawn from scratch.
                if last_painted_screen != "transition_to_pause":
                    screensaver.reset()
                    screensaver_replay.reset()
            elif screen == "screen_off":
                _screen_off_entered_perf = time.perf_counter()
            elif screen == "menu":
                # _menu_entered_perf is set in _open_menu(); nothing to reset here
                pass
            elif screen == "loading":
                loading_idx = 0
                last_loading_paint = 0
            elif screen in ("transition_to_pause", "transition_to_play"):
                transition_idx = 0
                last_transition_paint = 0
                _transition_behind_img = _last_displayed.copy() if _last_displayed is not None else None
                _transition_ahead_img = None
                _transition_start_perf = time.perf_counter()
                if screen == "transition_to_pause":
                    screensaver.reset()
                    screensaver_replay.reset()
            last_painted_screen = screen

        if change_perf and change_perf != last_logged_change_perf:
            wake_lag_ms = (wake_perf - change_perf) * 1000
            log.info("latency: state-change -> render wake = %.1fms (screen=%s)", wake_lag_ms, screen)
            last_logged_change_perf = change_perf

        # --- Paint ---
        try:
            if screen == "startup":
                pass

            elif screen == "volume":
                v = volume if volume is not None else 0
                # Force repaint during fade so blend advances tick-by-tick.
                if fade_active or v != last_painted_volume or screen != last_painted_screen:
                    _timed_paint("volume", _paint_volume, v)
                    last_painted_volume = v

            elif screen == "loading":
                if now - last_loading_paint >= GIF_FRAME_PERIOD:
                    _timed_paint("loading", _paint_loading, loading_idx)
                    loading_idx += 1
                    last_loading_paint = now

            elif screen == "idle":
                # Use the faster screensaver-specific period so we match the
                # GIF's authored framerate (25fps) instead of the generic 10fps.
                if now - last_idle_paint >= SCREENSAVER_FRAME_PERIOD:
                    _timed_paint("idle", _paint_idle, idle_idx)
                    idle_idx += 1
                    last_idle_paint = now

            elif screen == "transition_to_pause":
                frames = _load_frames(GIF_PATH_PLAY_TO_PAUSE, resize_to_screen=True)
                if frames:
                    elapsed = time.perf_counter() - _transition_start_perf
                    gif_duration = len(frames) * GIF_FRAME_PERIOD
                    total_duration = gif_duration + TRANSITION_HOLD_AT_END_S
                    progress = elapsed / total_duration
                    if progress >= 1.0:
                        with state_lock:
                            if current_screen == "transition_to_pause":
                                _set_screen_unsafe("idle")
                    else:
                        # GIF frames advance at GIF rate; cap at last frame
                        # during the hold-at-end period.
                        sym_idx = min(int(elapsed / GIF_FRAME_PERIOD), len(frames) - 1)
                        # Trapezoidal envelope on symbol intensity: ramp up over
                        # first FADE_PORTION, hold at full through middle, ramp
                        # down over last FADE_PORTION. Combined with the
                        # extended total_duration, the LAST frame stays bright
                        # for a moment before fading out.
                        if progress < TRANSITION_FADE_PORTION:
                            symbol_env = progress / TRANSITION_FADE_PORTION
                        elif progress > 1.0 - TRANSITION_FADE_PORTION:
                            symbol_env = (1.0 - progress) / TRANSITION_FADE_PORTION
                        else:
                            symbol_env = 1.0
                        if SCREENSAVER_MODE == "procedural":
                            ahead = screensaver.paint_to_image(
                                device,
                                target_population=SCREENSAVER_TARGET_POPULATION,
                                spawn_rate=SCREENSAVER_SPAWN_RATE,
                                drift_x=SCREENSAVER_DRIFT_X,
                                drift_y=SCREENSAVER_DRIFT_Y,
                                median_speed=SCREENSAVER_MEDIAN_SPEED,
                            )
                        else:
                            ahead = None
                        _timed_paint("trans-to-pause", _paint_transition_overlay,
                                     frames[sym_idx], _transition_behind_img, ahead,
                                     progress, symbol_env)
                        transition_idx = sym_idx

            elif screen == "transition_to_play":
                frames = _load_frames(GIF_PATH_PAUSE_TO_PLAY, resize_to_screen=True)
                if frames:
                    elapsed = time.perf_counter() - _transition_start_perf
                    gif_duration = len(frames) * GIF_FRAME_PERIOD
                    total_duration = gif_duration + TRANSITION_HOLD_AT_END_S
                    progress = elapsed / total_duration
                    if progress >= 1.0:
                        with state_lock:
                            if current_screen == "transition_to_play":
                                _set_screen_unsafe("playback")
                    else:
                        sym_idx = min(int(elapsed / GIF_FRAME_PERIOD), len(frames) - 1)
                        if progress < TRANSITION_FADE_PORTION:
                            symbol_env = progress / TRANSITION_FADE_PORTION
                        elif progress > 1.0 - TRANSITION_FADE_PORTION:
                            symbol_env = (1.0 - progress) / TRANSITION_FADE_PORTION
                        else:
                            symbol_env = 1.0
                        # behind = LIVE screensaver so it keeps animating as it
                        # fades out (instead of freezing on the snapshot taken
                        # at transition start).
                        if SCREENSAVER_MODE == "procedural":
                            behind = screensaver.paint_to_image(
                                device,
                                target_population=SCREENSAVER_TARGET_POPULATION,
                                spawn_rate=SCREENSAVER_SPAWN_RATE,
                                drift_x=SCREENSAVER_DRIFT_X,
                                drift_y=SCREENSAVER_DRIFT_Y,
                                median_speed=SCREENSAVER_MEDIAN_SPEED,
                            )
                        else:
                            behind = _transition_behind_img
                        if _transition_ahead_img is None:
                            _transition_ahead_img = _render_playback_image()
                        _timed_paint("trans-to-play", _paint_transition_overlay,
                                     frames[sym_idx], behind,
                                     _transition_ahead_img, progress, symbol_env)
                        transition_idx = sym_idx

            elif screen == "menu":
                page = _menu_current_page()
                if page is not None:
                    items = [_label_for(it) for it in page.items]
                    paint_menu(device, items=items, selected_idx=page.selected_idx,
                               title=page.title)

            elif screen == "screen_off":
                # Render an all-black frame for the fade duration so the
                # crossfade can fade idle → black, then put the OLED to sleep
                # so it draws no power and accumulates no burn-in.
                if not _screen_off_panel_hidden:
                    sleep_after = FADE_SECONDS + 0.5
                    if time.perf_counter() - _screen_off_entered_perf > sleep_after:
                        try:
                            device.hide()
                            log.info("screen_off: panel hidden")
                        except Exception as e:
                            log.warning("device.hide() failed: %s", e)
                        _screen_off_panel_hidden = True
                    else:
                        # Paint black so the fade has a target
                        img = Image.new(device.mode, (device.width, device.height), "black")
                        device.display(img)

            elif screen == "playback":
                if status != "play" or title is None:
                    continue
                # Refresh rate: marquee or streaming chasers need animation;
                # otherwise the slow 1Hz refresh is plenty (clock + progress).
                is_stream = (last_service == "webradio")
                if is_stream:
                    paint_period = 0.08          # ~12fps for chasers
                elif needs_marquee(title):
                    paint_period = 0.083         # ~12fps for scrolling text — slightly chunkier than 17fps but ~30% less work
                else:
                    paint_period = PLAYBACK_REFRESH_SECONDS
                if not fade_active and now - last_playback_paint < paint_period:
                    continue
                # Cold-start guard: if no play event seen yet, don't extrapolate from epoch
                if event_wall > 0:
                    elapsed_ms = (now - event_wall) * 1000.0
                    seek_now = min(seek + elapsed_ms, duration * 1000.0)
                else:
                    seek_now = seek
                _timed_paint("playback", display_playback_screen, device, title, artist,
                             int(seek_now), duration, is_stream)
                last_playback_paint = now

        except Exception as e:
            log.error("paint failed (screen=%s): %s", screen, e)

        last_render_tick_wall = time.time()  # heartbeat for watchdog

    log.info("render loop exiting")
    try:
        with canvas(device) as draw:
            draw.rectangle((0, 0, device.width, device.height), fill="black")
    except Exception:
        pass


def _set_screen_unsafe(name):
    """Caller must hold state_lock."""
    global current_screen, last_screen_change_perf, _screen_off_panel_hidden
    if current_screen != name:
        old = current_screen
        log.info("screen: %s -> %s", current_screen, name)
        _trace("screen %s -> %s" % (old, name))
        current_screen = name
        last_screen_change_perf = time.perf_counter()
        # Wake the OLED panel if we're leaving screen_off, before any paint.
        if old == "screen_off" and _screen_off_panel_hidden:
            try:
                device.show()
            except Exception as e:
                log.warning("device.show() failed: %s", e)
            _screen_off_panel_hidden = False
        # Skip the standard crossfade if EITHER side is a transition screen —
        # transitions render their own primary-screen crossfade with the symbol
        # GIF superimposed, so the post-transition handoff is already smooth.
        skip = (name in ("transition_to_pause", "transition_to_play")
                or old in ("transition_to_pause", "transition_to_play"))
        if not skip:
            _start_fade()
        render_wake.set()


def _timed_paint(name, fn, *args):
    """Run a painter; warn on slow paints. (No per-paint debug log — too spammy.)"""
    t0 = time.perf_counter()
    fn(*args)
    ms = (time.perf_counter() - t0) * 1000
    if ms > SLOW_PAINT_MS:
        log.warning("paint %s SLOW: %.1fms", name, ms)
    return ms


# --- Socket.IO handlers (state-only, no SPI writes) ---

# Construct with explicit reconnection + silenced loggers. Auto-reconnect with
# backoff is handled by the library; do NOT call sio.connect() from disconnect()
# (creates duplicate background threads over months of flapping).
sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,    # 0 = infinite
    reconnection_delay=2,
    reconnection_delay_max=30,
    logger=False,
    engineio_logger=False,
)


@sio.event
def connect():
    log.info("ws connect")
    sio.emit("subscribe", {})
    sio.emit("getState", {})


@sio.event
def disconnect():
    # python-socketio's auto-reconnect handles re-establishing the connection.
    # We just flip the screen so the user sees activity.
    log.warning("ws disconnect — auto-reconnect in progress")
    with state_lock:
        _set_screen_unsafe("loading")


@sio.event
def connect_error(data):
    log.warning("ws connect_error: %s", data)


@sio.on("pushState")
def on_message(data):
    t0 = time.perf_counter()
    try:
        _handle_pushstate(data)
    except Exception as e:
        log.exception("on_message: %s", e)
    finally:
        ms = (time.perf_counter() - t0) * 1000
        if ms > SLOW_HANDLER_MS:
            log.warning("on_message SLOW: %.1fms", ms)


def _handle_pushstate(data):
    global volume_initialized, last_volume, last_title, last_artist, last_seek, last_duration
    global last_status, last_event_wall, last_stop_wall, last_volume_event_wall, last_status_change_wall
    global last_random, last_repeat, last_repeat_single, last_service

    state = data.get("status", "")
    title = data.get("title", "Unknown")
    artist = data.get("artist", "Unknown")
    raw_volume = data.get("volume")
    volume = _coerce_int(raw_volume, None) if raw_volume is not None else None
    seek = _coerce_int(data.get("seek"), 0)
    duration = _coerce_int(data.get("duration"), 1) or 1
    # Mirror Volumio's shuffle/repeat/service state
    last_random = bool(data.get("random", False))
    last_repeat = bool(data.get("repeat", False))
    last_repeat_single = bool(data.get("repeatSingle", False))
    last_service = str(data.get("service", "") or "")

    # Raw dump only when DEBUG, and outside the lock — json.dumps is expensive
    # and used to hold the state_lock unnecessarily.
    if log.isEnabledFor(logging.DEBUG):
        log.debug("pushState raw: %s", json.dumps(data))

    with state_lock:
        log.info(
            "pushState: status=%s title=%r vol=%s seek=%.1fs/%ss screen=%s",
            state, title, volume, seek / 1000.0, duration, current_screen,
        )
        _trace("pushState in: state=%s prev_state=%s vol=%s(raw=%r) prev_vol=%s "
               "title=%r prev_title=%r seek=%s prev_seek=%s screen=%s service=%s"
               % (state, last_status, volume, raw_volume, last_volume,
                  title, last_title, seek, last_seek, current_screen, last_service))

        if current_screen == "startup":
            _trace("pushState skip: startup screen")
            return

        now = time.time()
        prev_status = last_status
        prev_title = last_title
        prev_seek = last_seek
        prev_volume = last_volume
        seek_decreased = (prev_seek >= 0 and seek < prev_seek - 5000)
        title_changed = (title != prev_title)

        # --- Volume detection ---
        volume_event = False
        if volume is not None:
            if not volume_initialized:
                last_volume = volume
                volume_initialized = True
                log.info("volume initialized: %s", volume)
                _trace("vol init -> %s (no flash)" % volume)
            elif volume != prev_volume:
                last_volume = volume
                last_volume_event_wall = now
                volume_event = True
                _trace("VOL_EVENT real-diff: %s -> %s (title_changed=%s seek_dec=%s state=%s prev_state=%s)"
                       % (prev_volume, volume, title_changed, seek_decreased, state, prev_status))
            elif (state == "play" and prev_status == "play"
                  and not title_changed and not seek_decreased
                  and last_volume_event_wall
                  and (now - last_volume_event_wall) < VOLUME_BUTTON_RECENT_WINDOW):
                last_volume_event_wall = now
                volume_event = True
                _trace("VOL_EVENT heuristic: vol=%s prev=%s within=%.2fs"
                       % (volume, prev_volume, now - last_volume_event_wall))

        # IR-bounce debounce: ignore play events within PAUSE_TO_PLAY_DEBOUNCE of a pause
        ignore_state_change = False
        if (state == "play" and prev_status == "pause"
                and last_status_change_wall
                and (now - last_status_change_wall) < PAUSE_TO_PLAY_DEBOUNCE):
            log.info("ignoring play event %.2fs after pause (IR bounce)",
                     now - last_status_change_wall)
            ignore_state_change = True

        if not ignore_state_change:
            if state != prev_status:
                last_status_change_wall = now
            last_status = state
            if state == "play":
                last_title = title
                last_artist = artist
                last_seek = seek
                last_duration = duration
                last_event_wall = now
                last_stop_wall = 0.0
                if title_changed:
                    _reset_brightness_fade(0, "new_track")
            elif state == "pause":
                last_stop_wall = 0.0
                if prev_status == "play":
                    _reset_brightness_fade(last_seek, "pause")
            elif state == "stop":
                if prev_status != "stop":
                    last_stop_wall = now

        # --- Pick target screen ---
        # State machine:
        #   playback -[pause]-> transition_to_pause -[done]-> idle
        #   idle     -[play]--> transition_to_play  -[done]-> playback
        #   playback -[stop]--> loading -[stop persists]-> idle
        #   loading  -[play]--> playback (no transition; track-skip in progress)
        # Menu owns the screen while open — don't auto-switch on incidental
        # state changes (e.g. track auto-advance during menu interaction).
        if current_screen == "menu":
            return
        # During quiet hours, all state updates above still apply (so we wake to
        # the right thing at 6am), but DO NOT touch the screen — leave it in
        # screen_off. Without this gate, every incoming pushState would flap
        # us through transition_to_play -> screen_off in a tight loop.
        if _in_quiet_hours():
            return
        if volume_event:
            if current_screen != "volume":
                _set_screen_unsafe("volume")
            else:
                render_wake.set()
        elif current_screen == "volume":
            pass  # let render loop transition out when hold expires
        elif ignore_state_change:
            pass
        elif state == "play":
            if current_screen in ("idle", "screen_off", "transition_to_pause"):
                _set_screen_unsafe("transition_to_play")
            elif current_screen not in ("playback", "transition_to_play"):
                _set_screen_unsafe("playback")
        elif state == "pause":
            if current_screen in ("playback", "transition_to_play"):
                _set_screen_unsafe("transition_to_pause")
            elif current_screen not in ("idle", "transition_to_pause"):
                _set_screen_unsafe("idle")
        elif state == "stop":
            if current_screen not in ("loading", "idle", "screen_off",
                                      "transition_to_pause", "transition_to_play"):
                _set_screen_unsafe("loading")


def startup_screen():
    """Runs the startup animation (blocks up to ~15s, interruptible via shutdown_event).
    Returns True on full completion, False if shutdown was requested mid-startup."""
    if not display_startup(device, shutdown_event):
        return False
    log.info("startup finished, requesting state")
    try:
        sio.emit("getState", {})
    except Exception as e:
        log.warning("getState emit failed during startup: %s", e)
    with state_lock:
        _set_screen_unsafe("playback")
    return True


def _systemd_notify(msg):
    """Send a state message to systemd via NOTIFY_SOCKET. No-op if not under systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode(), addr)
    except Exception as e:
        log.debug("sd_notify failed: %s", e)


def watchdog_thread():
    """Detect a stuck/dead render thread and force-exit so the service manager restarts us.
    Also pets the systemd watchdog if we're running under one."""
    log.info("watchdog started (timeout=%ds, check=%ds)", WATCHDOG_TIMEOUT_S, WATCHDOG_CHECK_INTERVAL_S)
    while not shutdown_event.is_set():
        if shutdown_event.wait(timeout=WATCHDOG_CHECK_INTERVAL_S):
            return
        gap = time.time() - last_render_tick_wall
        if gap > WATCHDOG_TIMEOUT_S:
            log.critical("render thread silent for %.1fs (>%ds); forcing exit", gap, WATCHDOG_TIMEOUT_S)
            os._exit(4)
        _systemd_notify("WATCHDOG=1")


def _connect_with_retry(retry_delay=2.0):
    """Block until first successful connection to Volumio. Survives the case where
    Volumio is not yet up at boot (common when both start together)."""
    while not shutdown_event.is_set():
        try:
            sio.connect(VOLUMIO_WS_URL)
            return
        except Exception as e:
            log.warning("waiting for Volumio (%s); retry in %.1fs", e, retry_delay)
            time.sleep(retry_delay)


def _shutdown(signum, _frame):
    """Handle SIGTERM/SIGINT: signal threads to stop, disconnect cleanly."""
    if shutdown_event.is_set():
        return  # idempotent
    log.info("received signal %d, shutting down", signum)
    shutdown_event.set()
    render_wake.set()
    try:
        if sio.connected:
            sio.disconnect()
    except Exception as e:
        log.warning("sio.disconnect during shutdown: %s", e)


def _toggle_log_level(_signum, _frame):
    """SIGUSR2: flip log level between DEBUG and INFO live (no restart).
    Lets us crank verbosity up to capture an in-the-act bug, then drop it
    back without losing the running process state."""
    import logging
    new_level = logging.INFO if log.level == logging.DEBUG else logging.DEBUG
    log.setLevel(new_level)
    log.warning("log level toggled to %s via SIGUSR2", logging.getLevelName(new_level))


def _dump_trace(_signum=None, _frame=None):
    """SIGUSR1: snapshot the in-memory trace ring buffer to /tmp/vfd-trace.log.
    Appends to the file so multiple dumps over a session accumulate."""
    path = "/tmp/vfd-trace.log"
    with _trace_lock:
        snapshot = list(_trace_buffer)
    try:
        with open(path, "a") as f:
            f.write("---- trace dump %s (%d events) ----\n" %
                    (time.strftime("%H:%M:%S"), len(snapshot)))
            for ts, msg in snapshot:
                f.write("%.6f %s\n" % (ts, msg))
        log.warning("trace buffer (%d events) dumped to %s", len(snapshot), path)
    except Exception as e:
        log.error("trace dump failed: %s", e)


def _open_menu():
    """Open the top-level menu and switch screen state."""
    global _menu_pre_screen, _menu_entered_perf, _menu_last_input_perf
    with state_lock:
        if current_screen != "menu":
            _menu_pre_screen = current_screen
    with _menu_stack_lock:
        _menu_stack.clear()
        _menu_stack.append(_MenuPage(_build_top_level_menu(), "Menu"))
    _menu_entered_perf = time.perf_counter()
    _menu_last_input_perf = _menu_entered_perf
    log.info("menu: opening (return to %s on exit)", _menu_pre_screen)
    # User just interacted — make sure the screen is bright, restart the fade
    # over the remaining track time.
    _reset_brightness_fade(last_seek, "menu_open")
    _menu_set_screen("menu")


def _menu_button(button):
    """Handle a button while the menu screen is showing.
    Does NOT hold state_lock when invoking actions — actions may do HTTP."""
    global _menu_last_input_perf
    page = _menu_current_page()
    if page is None:
        # Menu somehow lost its stack; close.
        _menu_close()
        return
    if button == "KEY_MENU":
        _menu_pop()      # back, or close if at top
        return
    if button == "KEY_UP":
        with _menu_stack_lock:
            page.selected_idx = (page.selected_idx - 1) % len(page.items)
    elif button == "KEY_DOWN":
        with _menu_stack_lock:
            page.selected_idx = (page.selected_idx + 1) % len(page.items)
    elif button == "KEY_PLAY":
        with _menu_stack_lock:
            item = page.items[page.selected_idx] if page.items else None
        if item:
            log.info("menu select: %s", _label_for(item))
            action = item[1]
            if action is not None:
                action()
        return
    else:
        return
    _menu_last_input_perf = time.perf_counter()
    render_wake.set()


def _forward_to_volumio(button):
    """When the menu is closed, replicate the original lircrc behavior."""
    if button == "KEY_MENU":
        # _open_menu() acquires state_lock internally — DO NOT wrap here
        # (threading.Lock is non-reentrant; re-acquire would deadlock).
        _open_menu()
        return
    cmds = {
        "KEY_PLAY": ["toggle"],
        "KEY_RIGHT": ["next"],
        "KEY_LEFT": ["previous"],
        "KEY_UP": ["volume", "plus"],
        "KEY_DOWN": ["volume", "minus"],
    }
    cmd = cmds.get(button)
    if not cmd:
        return
    try:
        subprocess.Popen(["/usr/local/bin/volumio"] + cmd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.warning("forward_to_volumio %s: %s", button, e)


def _ir_dispatcher_thread():
    """Consume IR button events from the queue and route them based on screen state."""
    log.info("ir dispatcher started")
    while not shutdown_event.is_set():
        try:
            button = _ir_queue.get(timeout=0.5)
        except _queue.Empty:
            continue
        log.debug("ir button: %s", button)
        with state_lock:
            in_menu = (current_screen == "menu")
        if in_menu:
            # Don't hold state_lock — menu actions may do HTTP fetches.
            _menu_button(button)
        else:
            _forward_to_volumio(button)


def _ir_udp_listener_thread():
    """Receive IR button names over UDP from tools/ir_dispatch.sh."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", MENU_IR_UDP_PORT))
    except OSError as e:
        log.error("ir UDP bind failed on %d: %s", MENU_IR_UDP_PORT, e)
        return
    sock.settimeout(0.5)
    log.info("ir UDP listener bound on 127.0.0.1:%d", MENU_IR_UDP_PORT)
    while not shutdown_event.is_set():
        try:
            data, _ = sock.recvfrom(64)
            button = data.decode("utf-8", errors="replace").strip()
            if button:
                _ir_queue.put(button)
        except socket.timeout:
            continue
        except Exception as e:
            log.warning("ir UDP listener: %s", e)
    try:
        sock.close()
    except Exception:
        pass


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGUSR2, _toggle_log_level)
    signal.signal(signal.SIGUSR1, _dump_trace)

    log.info("=== vfd starting ===")
    log.info("device: ssd1322 mode=%s size=%dx%d", device.mode, device.width, device.height)
    _prewarm_gifs()
    _connect_with_retry()
    if shutdown_event.is_set():
        sys.exit(0)

    # Tell systemd we're ready BEFORE the 15s startup hold so Type=notify
    # services don't hit TimeoutStartSec.
    _systemd_notify("READY=1\nSTATUS=connected to Volumio, playing startup screen")

    if not startup_screen():
        log.info("startup interrupted by shutdown")
        _systemd_notify("STOPPING=1")
        sys.exit(0)

    _systemd_notify("STATUS=running")

    # Render thread is NOT a daemon — we want a clean drain on shutdown,
    # and we want the process to NOT silently exit if the main thread returns.
    render_thread = threading.Thread(target=render_loop, daemon=False)
    render_thread.start()
    threading.Thread(target=watchdog_thread, daemon=True).start()
    # IR remote routing: UDP listener + dispatcher. tools/ir_dispatch.sh sends
    # button names here; dispatcher routes them to menu nav or to Volumio.
    threading.Thread(target=_ir_udp_listener_thread, daemon=True).start()
    threading.Thread(target=_ir_dispatcher_thread, daemon=True).start()

    # Self-healing wait loop. python-socketio's auto-reconnect handles flaps,
    # but if sio.wait() ever returns (library exhausted, fatal error), don't
    # let the process die silently — try again, then escalate to a non-zero
    # exit so a service manager can restart us.
    consecutive_failures = 0
    while not shutdown_event.is_set():
        try:
            sio.wait()
        except Exception as e:
            log.error("sio.wait raised: %s", e)
        if shutdown_event.is_set():
            break
        consecutive_failures += 1
        log.warning("sio.wait returned unexpectedly (failure #%d); reconnecting in 5s", consecutive_failures)
        time.sleep(5)
        try:
            sio.connect(VOLUMIO_WS_URL)
            consecutive_failures = 0
        except Exception as e:
            log.error("reconnect failed: %s", e)
            if consecutive_failures >= 5:
                log.error("giving up after %d failures; exiting non-zero for service manager", consecutive_failures)
                shutdown_event.set()
                render_wake.set()
                render_thread.join(timeout=2.0)
                sys.exit(1)

    _systemd_notify("STOPPING=1")
    log.info("waiting for render thread to drain")
    render_thread.join(timeout=2.0)
    log.info("=== vfd exit ===")

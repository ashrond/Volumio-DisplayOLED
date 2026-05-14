"""Configuration loader.

Three tiers, loaded in order:

1. `config/settings.toml`  — program defaults, never written by WebUI
2. `config/runtime.toml`   — WebUI-managed; behavior, hardware, theme selection
3. `themes/<active>/<active>.toml`  — theme identity, overlaid on default theme

Every constant the rest of the program imports is resolved here so callers
don't have to know which tier a value came from. See masterplan.md for the
full taxonomy of which knob lives in which tier.
"""

import os
import sys
import logging
import toml
from logging.handlers import RotatingFileHandler
from PIL import ImageFont


# ── Paths ──────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))     # config/
_PROJECT_ROOT = os.path.dirname(_HERE)                 # project root


# ── Tier 1: settings.toml (program defaults) ───────────────────────────────
_settings = toml.load(os.path.join(_HERE, "settings.toml"))
_paths_cfg     = _settings.get("paths", {})
_defaults_cfg  = _settings.get("defaults", {})
_volumio_cfg_s = _settings.get("volumio", {})
_anim_cfg      = _settings.get("animation", {})
_internal_cfg  = _settings.get("internal", {})

FONT_PATH           = _paths_cfg.get("font_path",
                                     "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")
_themes_dir         = os.path.join(_PROJECT_ROOT, _paths_cfg.get("themes_dir", "themes"))
DEFAULT_THEME_NAME  = str(_defaults_cfg.get("default_theme", "default"))

VOLUMIO_WS_URL              = str(_volumio_cfg_s.get("ws_url", "http://localhost:3000"))

GIF_FRAME_PERIOD            = float(_anim_cfg.get("gif_frame_period_s", 0.1))
SCREENSAVER_FRAME_PERIOD    = float(_anim_cfg.get("screensaver_frame_period_s", 0.04))
CONTRAST_UPDATE_INTERVAL_S  = float(_anim_cfg.get("contrast_update_interval_s", 1.0))

TRACE_BUFFER_SIZE           = int(_internal_cfg.get("trace_buffer_size", 4000))
SLOW_PAINT_MS               = int(_internal_cfg.get("slow_paint_ms", 30))
SLOW_HANDLER_MS             = int(_internal_cfg.get("slow_handler_ms", 20))
WATCHDOG_TIMEOUT_S          = int(_internal_cfg.get("watchdog_timeout_s", 60))
WATCHDOG_CHECK_INTERVAL_S   = int(_internal_cfg.get("watchdog_check_interval_s", 15))


# ── Tier 2: runtime.toml (WebUI-managed) ───────────────────────────────────
_runtime = toml.load(os.path.join(_HERE, "runtime.toml"))

# Display hardware
_display_cfg = _runtime.get("display", {})
DEVICE_TYPE   = str(_display_cfg.get("type", "ssd1322"))
DEVICE_WIDTH  = int(_display_cfg.get("width", 256))
DEVICE_HEIGHT = int(_display_cfg.get("height", 64))
SPI_BUS       = int(_display_cfg.get("spi_bus", 0))
SPI_CS        = int(_display_cfg.get("spi_cs", 0))
SPI_SPEED_HZ  = int(_display_cfg.get("spi_speed_hz", 8000000))

# Logging — set up first so log is available for theme load
_log_cfg = _runtime.get("logging", {})
_log_enabled      = bool(_log_cfg.get("enabled", False))
_log_level        = getattr(logging, str(_log_cfg.get("level", "INFO")).upper(), logging.INFO)
_log_file         = _log_cfg.get("file", "")
_log_max_bytes    = int(_log_cfg.get("max_bytes", 2 * 1024 * 1024))
_log_backup_count = int(_log_cfg.get("backup_count", 3))

log = logging.getLogger("vfd")
log.handlers = []
log.propagate = False
# Tools that import config.config purely to read settings (e.g. tools/admin.py)
# set VFD_NO_LOG to suppress side effects — no stream handler, no file handler,
# no "loaded theme …" line in /tmp/vfd.log per CLI invocation.
if os.environ.get("VFD_NO_LOG"):
    log.setLevel(logging.CRITICAL + 1)
    log.addHandler(logging.NullHandler())
elif _log_enabled:
    log.setLevel(_log_level)
    _fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s",
                             datefmt="%H:%M:%S")
    _sh = logging.StreamHandler(sys.stderr)
    _sh.setFormatter(_fmt)
    log.addHandler(_sh)
    if _log_file:
        try:
            _fh = RotatingFileHandler(_log_file, maxBytes=_log_max_bytes,
                                      backupCount=_log_backup_count)
            _fh.setFormatter(_fmt)
            log.addHandler(_fh)
        except OSError as e:
            log.warning("could not open log file %s: %s", _log_file, e)
else:
    log.setLevel(logging.CRITICAL + 1)
    log.addHandler(logging.NullHandler())

# Silence noisy third-party loggers (engine.io chatter would flood the file).
for _noisy in ("engineio", "engineio.client", "socketio", "socketio.client",
               "urllib3", "requests"):
    _l = logging.getLogger(_noisy)
    _l.setLevel(logging.WARNING)
    _l.propagate = False
    if not _l.handlers:
        _l.addHandler(logging.NullHandler())

# Volume. Note: the actual "max volume" is owned by Volumio (alsa_controller
# config) and read at runtime in main.py — we don't want a second source of
# truth. The runtime knob below is purely the heuristic-window timer.
_volume_cfg = _runtime.get("volume", {})
VOLUME_BUTTON_RECENT_WINDOW = float(_volume_cfg.get("button_recent_window", 3.0))

# Menu
_menu_cfg = _runtime.get("menu", {})
MENU_TIMEOUT_SECONDS = float(_menu_cfg.get("timeout_seconds", 15.0))
MENU_IR_UDP_PORT     = int(_menu_cfg.get("ir_udp_port", 9876))

# Timing
_timing = _runtime.get("timing", {})
PLAYBACK_REFRESH_SECONDS        = float(_timing.get("playback_refresh_seconds", 1.0))
VOLUME_HOLD_SECONDS             = float(_timing.get("volume_hold_seconds", 1.5))
IDLE_AFTER_STOP_SECONDS         = float(_timing.get("idle_after_stop_seconds", 300.0))
SCREEN_OFF_AFTER_IDLE_SECONDS   = float(_timing.get("screen_off_after_idle_seconds", 3600.0))
RENDER_TICK_SECONDS             = float(_timing.get("render_tick_seconds", 0.05))
PAUSE_TO_PLAY_DEBOUNCE_SECONDS  = float(_timing.get("pause_to_play_debounce_seconds", 1.5))

# Burnin
_burnin = _runtime.get("burnin", {})
QUIET_HOURS_START          = int(_burnin.get("quiet_hours_start", 0))
QUIET_HOURS_END            = int(_burnin.get("quiet_hours_end", 0))
PIXEL_SHIFT_ENABLED        = bool(_burnin.get("pixel_shift_enabled", True))
PIXEL_SHIFT_INTERVAL_S     = float(_burnin.get("pixel_shift_interval_s", 180.0))
SCANLINE_ALT_ENABLED       = bool(_burnin.get("scanline_alternation_enabled", True))
SCANLINE_DIM_FACTOR        = max(0.0, min(1.0, float(_burnin.get("scanline_dim_factor", 0.7))))
SCANLINE_ALT_INTERVAL_S    = float(_burnin.get("scanline_alternation_interval_s", 45.0))
STARTUP_CONTRAST           = max(0, min(255, int(_burnin.get("startup_contrast", 200))))
TRACK_FADE_ENABLED         = bool(_burnin.get("track_fade_enabled", True))
TRACK_FADE_MAX             = max(0, min(255, int(_burnin.get("track_fade_max", 255))))
TRACK_FADE_MIN             = max(0, min(255, int(_burnin.get("track_fade_min", 0))))
TRACK_FADE_MIN_REMAINING_S = float(_burnin.get("track_fade_min_remaining_s", 60.0))

# IR
_ir_cfg = _runtime.get("ir", {})
SKIP_ANIM_COOLDOWN_S = float(_ir_cfg.get("skip_anim_cooldown_s", 3.0))
BUTTON_DEBOUNCE_S    = {str(k): float(v) for k, v in (_ir_cfg.get("debounce", {})).items()}

# Theme selection
_theme_sel    = _runtime.get("theme", {})
ACTIVE_THEME  = str(_theme_sel.get("active", DEFAULT_THEME_NAME))


# ── Tier 3: theme overlay ──────────────────────────────────────────────────
def _load_theme(name):
    path = os.path.join(_themes_dir, name, name + ".toml")
    return toml.load(path)


def _deep_merge(base, overlay):
    """Recursively overlay `overlay` on `base`. Dicts merge; other types replace."""
    result = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# Default theme is mandatory — it provides the fallback for everything.
try:
    _default_theme = _load_theme(DEFAULT_THEME_NAME)
except Exception as e:
    raise RuntimeError(
        "Failed to load default theme %r from %s — themes/default/default.toml is required: %s"
        % (DEFAULT_THEME_NAME, _themes_dir, e)
    )

if ACTIVE_THEME != DEFAULT_THEME_NAME:
    try:
        _active_data = _load_theme(ACTIVE_THEME)
        _theme = _deep_merge(_default_theme, _active_data)
        log.info("loaded theme %r (overlay on default)", ACTIVE_THEME)
    except Exception as e:
        log.warning("active theme %r load failed (%s); falling back to default",
                    ACTIVE_THEME, e)
        _theme = _default_theme
        ACTIVE_THEME = DEFAULT_THEME_NAME
else:
    _theme = _default_theme
    log.info("loaded theme 'default'")

# Warn (don't refuse) if the active theme wasn't designed for the panel
# currently configured. Lets users experiment but flags the layout mismatch.
# A theme can opt in to multi-screen support by listing all panels it supports;
# absence of `supported_screens` means "any" and disables the check.
_supported = _theme.get("meta", {}).get("supported_screens")
if _supported:
    _supported_lc = [str(s).lower() for s in _supported]
    if DEVICE_TYPE.lower() not in _supported_lc:
        log.warning("theme %r supports %s but [display] type is %r — layout may not fit",
                    ACTIVE_THEME, _supported, DEVICE_TYPE)


def _asset_path(canonical_filename):
    """Resolve an asset filename to a full path. Looks in the active theme's
    assets/ first; falls through to default theme if missing. Supports
    [assets] block in theme.toml to override the canonical filename."""
    overrides = _theme.get("assets", {})
    key = canonical_filename.replace(".gif", "").replace("-", "_")
    actual = overrides.get(key, canonical_filename)

    active_path = os.path.join(_themes_dir, ACTIVE_THEME, "assets", actual)
    if os.path.exists(active_path):
        return active_path
    # Fall through to the default theme. We don't warn here — many assets
    # are optional (idle.gif when in procedural mode, etc.). Callers that
    # actually try to load a missing file get a clear "GIF not found"
    # message from _load_frames, and _paint_idle gracefully degrades.
    return os.path.join(_themes_dir, DEFAULT_THEME_NAME, "assets", canonical_filename)


# Fonts (theme-defined point sizes, system-defined path)
_fonts = _theme["fonts"]
font_time   = ImageFont.truetype(FONT_PATH, int(_fonts["time"]))
font_title  = ImageFont.truetype(FONT_PATH, int(_fonts["title"]))
font_artist = ImageFont.truetype(FONT_PATH, int(_fonts["artist"]))
font_volume = ImageFont.truetype(FONT_PATH, int(_fonts["volume"]))

# Colors
_colors = _theme["colors"]
text_color       = _colors["text_color"]
background_color = _colors["background_color"]

# Playback layout
_playback = _theme["layout"]["playback"]
PLAYBACK_TIME_Y                  = int(_playback["time_y"])
PLAYBACK_TITLE_Y                 = int(_playback["title_y"])
PLAYBACK_PROGRESS_BAR_Y          = int(_playback["progress_bar_y"])
PLAYBACK_PROGRESS_BAR_HEIGHT     = int(_playback["progress_bar_height"])
PLAYBACK_PROGRESS_BAR_WIDTH      = int(_playback["progress_bar_width"])
PLAYBACK_ARTIST_Y                = int(_playback["artist_y"])
PLAYBACK_MARQUEE_SPEED_PX_PER_S  = int(_playback["marquee_speed_px_per_s"])
PLAYBACK_MARQUEE_GAP_PX          = int(_playback["marquee_gap_px"])

# Back-compat aliases (some callers still use the lowercase names)
progress_bar_height = PLAYBACK_PROGRESS_BAR_HEIGHT
progress_bar_width  = PLAYBACK_PROGRESS_BAR_WIDTH

# Loading layout
_loading = _theme["layout"]["loading"]
LOADING_TEXT_Y     = int(_loading["text_y"])
LOADING_GIF_Y      = int(_loading["gif_y"])
LOADING_GIF_HEIGHT = int(_loading["gif_height"])

# Transitions
_trans = _theme["transitions"]
FADE_SECONDS                    = float(_trans["fade_seconds"])
TRANSITION_HOLD_AT_END_SECONDS  = float(_trans["transition_hold_at_end_seconds"])
TRANSITION_FADE_PORTION_CFG     = float(_trans["transition_fade_portion"])
SKIP_GIF_FRAME_PERIOD_S         = float(_trans["skip_gif_frame_period_s"])
SKIP_SLIDE_TRIGGER_FRAC         = float(_trans["skip_slide_trigger_frac"])
SKIP_SLIDE_DURATION_S           = float(_trans["skip_slide_duration_s"])
SKIP_GIF_BRIGHTNESS_BOOST       = float(_trans["skip_gif_brightness_boost"])

# Screensaver
_ss = _theme["screensaver"]
SCREENSAVER_MODE              = str(_ss["mode"]).lower()
SCREENSAVER_TARGET_POPULATION = int(_ss["target_population"])
SCREENSAVER_SPAWN_RATE        = float(_ss["spawn_rate"])
SCREENSAVER_MEDIAN_SPEED      = float(_ss["median_speed"])
SCREENSAVER_DRIFT_X           = float(_ss["drift_x"])
SCREENSAVER_DRIFT_Y           = float(_ss["drift_y"])

# Asset paths — resolved with fallback to default theme
GIF_PATH_STARTUP        = _asset_path("startup.gif")
GIF_PATH_IDLE           = _asset_path("idle.gif")
GIF_PATH_LOADING        = _asset_path("loading.gif")
GIF_PATH_PLAY_TO_PAUSE  = _asset_path("play-to-pause.gif")
GIF_PATH_PAUSE_TO_PLAY  = _asset_path("pause-to-play.gif")
GIF_PATH_SKIP_FORWARD   = _asset_path("skip-forward.gif")
GIF_PATH_SKIP_BACKWARD  = _asset_path("skip-backward.gif")

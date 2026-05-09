import toml
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from PIL import ImageFont

# Resolve theme.toml from this file's location, not from the cwd —
# safe regardless of how the process is launched (systemd, manual, etc.)
_HERE = os.path.dirname(os.path.abspath(__file__))
config = toml.load(os.path.join(_HERE, "theme.toml"))

# Font settings
font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
font_time = ImageFont.truetype(font_path, config["fonts"]["time"])
font_title = ImageFont.truetype(font_path, config["fonts"]["title"])
font_artist = ImageFont.truetype(font_path, config["fonts"]["artist"])
font_volume = ImageFont.truetype(font_path, config["fonts"]["volume"])

# Layout settings
progress_bar_height = config["layout"]["progress_bar_height"]
progress_bar_width = config["layout"]["progress_bar_width"]

# Colors
text_color = config["colors"]["text_color"]
background_color = config["colors"]["background_color"]

# Define the path to the GIFs
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  
GIF_PATH = os.path.join(BASE_DIR, "assets", "playback.gif")  
GIF_PATH_STARTUP = os.path.join(BASE_DIR, "assets", "startup.gif")
GIF_PATH_LOADING = os.path.join(BASE_DIR, "assets", "loading.gif")  


# Ensure paths exist
if not os.path.exists(GIF_PATH):
    print(f"Warning: Playback GIF not found at {GIF_PATH}")
if not os.path.exists(GIF_PATH_STARTUP):
    print(f"Warning: Startup GIF not found at {GIF_PATH_STARTUP}")

# --- Logging ---
# Toggle via [logging] section in theme.toml.
#   enabled = true|false       # master switch
#   level   = "DEBUG"|"INFO"|"WARNING"|"ERROR"
#   file    = "/path/to/log"   # empty/missing => stderr only
_log_cfg = config.get("logging", {})
_log_enabled = bool(_log_cfg.get("enabled", False))
_log_level = getattr(logging, str(_log_cfg.get("level", "INFO")).upper(), logging.INFO)
_log_file = _log_cfg.get("file", "")
_log_max_bytes = int(_log_cfg.get("max_bytes", 2 * 1024 * 1024))
_log_backup_count = int(_log_cfg.get("backup_count", 3))

log = logging.getLogger("vfd")
log.handlers = []
log.propagate = False
if _log_enabled:
    log.setLevel(_log_level)
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if _log_file:
        try:
            fh = RotatingFileHandler(_log_file, maxBytes=_log_max_bytes, backupCount=_log_backup_count)
            fh.setFormatter(fmt)
            log.addHandler(fh)
        except OSError as e:
            log.warning("could not open log file %s: %s", _log_file, e)
else:
    log.setLevel(logging.CRITICAL + 1)
    log.addHandler(logging.NullHandler())

# Silence noisy third-party loggers (engine.io chatter floods over months)
for noisy in ("engineio", "engineio.client", "socketio", "socketio.client", "urllib3", "requests"):
    _l = logging.getLogger(noisy)
    _l.setLevel(logging.WARNING)
    _l.propagate = False
    if not _l.handlers:
        _l.addHandler(logging.NullHandler())

# --- Timing knobs ---
_timing = config.get("timing", {})
PLAYBACK_REFRESH_SECONDS = float(_timing.get("playback_refresh_seconds", 1.0))
VOLUME_HOLD_SECONDS = float(_timing.get("volume_hold_seconds", 1.5))
IDLE_AFTER_STOP_SECONDS = float(_timing.get("idle_after_stop_seconds", 8.0))
RENDER_TICK_SECONDS = float(_timing.get("render_tick_seconds", 0.05))
# legacy
PAUSE_ANIM_SECONDS = float(_timing.get("pause_anim_seconds", 0.6))
IDLE_ANIM_SECONDS = float(_timing.get("idle_anim_seconds", 0.6))
LOADING_ANIM_SECONDS = float(_timing.get("loading_anim_seconds", 3.0))
VOLUME_IDLE_SECONDS = float(_timing.get("volume_idle_seconds", 0.6))
VOLUME_EXIT_SECONDS = float(_timing.get("volume_exit_seconds", 0.3))

# --- Volume display ---
_volume_cfg = config.get("volume", {})
try:
    VOLUME_MAX = int(_volume_cfg.get("max", 100))
except (ValueError, TypeError):
    VOLUME_MAX = 100
VOLUME_BUTTON_RECENT_WINDOW = float(_volume_cfg.get("button_recent_window", 3.0))

# --- Screensaver ---
_ss_cfg = config.get("screensaver", {})
SCREENSAVER_MODE = str(_ss_cfg.get("mode", "procedural")).lower()
SCREENSAVER_TARGET_POPULATION = int(_ss_cfg.get("target_population", 53))
SCREENSAVER_SPAWN_RATE = float(_ss_cfg.get("spawn_rate", 17.6))
SCREENSAVER_MEDIAN_SPEED = float(_ss_cfg.get("median_speed", 2.4))
SCREENSAVER_DRIFT_X = float(_ss_cfg.get("drift_x", -0.4))
SCREENSAVER_DRIFT_Y = float(_ss_cfg.get("drift_y", 0.0))

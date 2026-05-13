#!/usr/bin/env python3
"""tools/admin.py — CLI bridge between the Volumio plugin (Node.js) and the
display Python program. Designed for `child_process.spawn()` from Node.

Outputs JSON on stdout; errors as JSON on stderr + non-zero exit. All
operations are stateless — no daemon, no IPC. Spawning fresh per call is
acceptable because plugin actions are rare and CLI startup is < 100ms.

Commands:
  list-themes                       Themes in themes/, with metadata + active flag
  set-theme <name>                  Set [theme] active = <name> in runtime.toml
  validate-theme <zip>              Inspect a zip without installing
  upload-theme <zip>                Extract + validate + install a theme zip
  delete-theme <name>               Remove a theme folder (refuses default and active)
  list-devices                      Supported display drivers + categories
  get-runtime                       Dump runtime.toml as JSON
  set-runtime <sec> <key> <val>     Set a runtime.toml value (auto-typed)
  status                            Service status, active theme, recent errors
  restart                           Restart the volumio-display systemd unit

Examples:
  tools/admin.py list-themes
  tools/admin.py set-theme vaporwave
  tools/admin.py set-runtime burnin quiet_hours_start 1

Why this exists: the Volumio plugin is Node.js and shouldn't reimplement
config-file parsing, theme validation, or systemd integration. It shells out
to this script for anything that touches our Python side. The contract is
JSON-in (argv) / JSON-out (stdout) — language-agnostic.
"""

import os
# Signal config.config that we're an admin CLI invocation — it should skip
# installing log handlers so each `admin.py …` call doesn't write a
# "loaded theme …" line to /tmp/vfd.log. MUST be set BEFORE any import that
# transitively pulls config.config.
os.environ["VFD_NO_LOG"] = "1"

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import toml

# tomlkit preserves comments and structure on write. We use it for reads we
# plan to modify (so user-edited annotations in runtime.toml survive WebUI
# round-trips) and fall back to plain `toml` if unavailable.
try:
    import tomlkit
    _HAVE_TOMLKIT = True
except ImportError:
    _HAVE_TOMLKIT = False


PROJECT_ROOT  = Path(__file__).resolve().parent.parent
CONFIG_DIR    = PROJECT_ROOT / "config"
THEMES_DIR    = PROJECT_ROOT / "themes"
RUNTIME_TOML  = CONFIG_DIR / "runtime.toml"
SETTINGS_TOML = CONFIG_DIR / "settings.toml"
SYSTEMD_UNIT  = "volumio-display.service"
LOG_FILE      = Path("/tmp/vfd.log")

# Canonical asset filenames every theme MAY ship. None are strictly required —
# missing assets fall through to default theme's. (idle.gif intentionally
# omitted from default; only used in `gif` screensaver mode.)
CANONICAL_ASSETS = {
    "startup.gif",
    "loading.gif",
    "play-to-pause.gif",
    "pause-to-play.gif",
    "skip-forward.gif",
    "skip-backward.gif",
    "idle.gif",
}

# Exit codes — chosen so a Node caller can distinguish failure modes without
# parsing the JSON body. Match Linux conventions where possible.
EXIT_OK             = 0
EXIT_INVALID_ARGS   = 2
EXIT_NOT_FOUND      = 3
EXIT_PERMISSION     = 4
EXIT_INVALID_THEME  = 5
EXIT_INTERNAL       = 99


# ── helpers ────────────────────────────────────────────────────────────────

def emit_ok(payload=None):
    """Write a success JSON envelope to stdout and exit 0."""
    out = {"status": "ok"}
    if isinstance(payload, dict):
        out.update(payload)
    elif payload is not None:
        out["data"] = payload
    print(json.dumps(out))
    sys.exit(EXIT_OK)


def emit_err(message, code=EXIT_INTERNAL, **extra):
    """Write an error JSON envelope to stderr and exit non-zero."""
    out = {"status": "error", "message": str(message)}
    out.update(extra)
    print(json.dumps(out), file=sys.stderr)
    sys.exit(code)


def load_runtime_for_write():
    """Read runtime.toml in a comment-preserving form so we can round-trip."""
    if not RUNTIME_TOML.exists():
        emit_err("runtime.toml not found at %s" % RUNTIME_TOML, EXIT_NOT_FOUND)
    text = RUNTIME_TOML.read_text(encoding="utf-8")
    if _HAVE_TOMLKIT:
        return tomlkit.loads(text)
    return toml.loads(text)


def save_runtime(doc):
    """Atomic write of runtime.toml (temp file + rename) so a crash mid-write
    can't leave the config truncated."""
    tmp = RUNTIME_TOML.with_suffix(".toml.tmp")
    if _HAVE_TOMLKIT:
        tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    else:
        tmp.write_text(toml.dumps(doc), encoding="utf-8")
    tmp.replace(RUNTIME_TOML)


def parse_value(s):
    """Coerce a CLI-supplied string to its TOML-native type (int / float /
    bool / string). Used by `set-runtime`. The WebUI knows what type each
    field expects and could pass typed JSON via stdin in the future — for
    now this heuristic covers the cases we care about."""
    sl = s.strip().lower()
    if sl in ("true", "yes", "on"):
        return True
    if sl in ("false", "no", "off"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s  # string as-is


def _theme_name_valid(name):
    """Themes must have a filesystem-safe name we can use in URLs and TOML
    paths. Alphanumeric plus dash and underscore. Refuses hidden / parent /
    absolute path attempts."""
    return bool(name) and all(c.isalnum() or c in "-_" for c in name)


# ── commands ───────────────────────────────────────────────────────────────

def cmd_list_themes(args):
    if not THEMES_DIR.is_dir():
        emit_err("themes dir not found at %s" % THEMES_DIR, EXIT_NOT_FOUND)
    runtime = load_runtime_for_write()
    active = str(runtime.get("theme", {}).get("active", "default"))
    themes = []
    for entry in sorted(THEMES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        toml_path = entry / (entry.name + ".toml")
        if not toml_path.exists():
            continue
        record = {
            "name": entry.name,
            "is_active": entry.name == active,
            "is_default": entry.name == "default",
        }
        try:
            data = toml.loads(toml_path.read_text(encoding="utf-8"))
            meta = data.get("meta", {})
            record.update({
                "display_name": meta.get("name", entry.name),
                "version": meta.get("version", ""),
                "author": meta.get("author", ""),
                "description": meta.get("description", ""),
                "supported_screens": list(meta.get("supported_screens", [])),
                "schema_version": meta.get("schema_version", 0),
            })
        except Exception as e:
            record["error"] = "toml parse failed: %s" % e
        themes.append(record)
    emit_ok({"themes": themes, "active": active})


def cmd_set_theme(args):
    if len(args) < 1:
        emit_err("usage: set-theme <name>", EXIT_INVALID_ARGS)
    name = args[0]
    if not _theme_name_valid(name):
        emit_err("invalid theme name %r" % name, EXIT_INVALID_ARGS)
    theme_dir = THEMES_DIR / name
    if not theme_dir.is_dir():
        emit_err("theme %r not found at %s" % (name, theme_dir), EXIT_NOT_FOUND)
    if not (theme_dir / (name + ".toml")).exists():
        emit_err("theme %r missing %s.toml" % (name, name), EXIT_INVALID_THEME)
    doc = load_runtime_for_write()
    if "theme" not in doc:
        doc["theme"] = tomlkit.table() if _HAVE_TOMLKIT else {}
    doc["theme"]["active"] = name
    save_runtime(doc)
    emit_ok({"active": name, "note": "restart required for change to take effect"})


def cmd_list_devices(args):
    """Introspect screens/devices/ for available drivers + categories.
    Imports the device modules to read class attributes — no actual luma
    init happens, so this is safe to call without hardware."""
    sys.path.insert(0, str(PROJECT_ROOT))
    devices = []
    # Hardcoded driver list for now. Could be made discovery-based by walking
    # screens/devices/*.py but the registry approach is clearer.
    driver_modules = [
        ("screens.devices.oled_ssd1322", "SSD1322Device"),
        ("screens.devices.tft_ili9341",  "ILI9341Device"),
    ]
    for mod_name, cls_name in driver_modules:
        try:
            mod = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            devices.append({
                "type_name": cls.type_name,
                "category": cls.category,
                "is_oled": cls.is_oled,
                "is_implemented": cls.is_implemented,
            })
        except Exception as e:
            devices.append({"module": mod_name, "error": str(e)})
    emit_ok({"devices": devices})


def _validate_theme_zip(zip_path):
    """Inspect a zip without extracting. Returns the theme name on success.
    Raises ValueError with a human-readable reason on any failure."""
    if not zip_path.exists():
        raise ValueError("zip not found: %s" % zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError("zip is empty")
        # Every entry must share exactly one top-level folder; no path
        # traversal; no absolute paths.
        top_levels = set()
        for n in names:
            if n.startswith("/") or ".." in n.replace("\\", "/").split("/"):
                raise ValueError("unsafe path in zip: %s" % n)
            parts = n.split("/")
            if parts[0]:
                top_levels.add(parts[0])
        if len(top_levels) != 1:
            raise ValueError("zip must have exactly one top-level folder, found %s"
                             % sorted(top_levels))
        theme_name = top_levels.pop()
        if not _theme_name_valid(theme_name):
            raise ValueError("theme name %r contains invalid characters "
                             "(allowed: alphanumeric, dash, underscore)" % theme_name)
        # Must contain <theme_name>.toml at the top of the folder.
        toml_entry = "%s/%s.toml" % (theme_name, theme_name)
        if toml_entry not in names:
            raise ValueError("zip missing required file %s" % toml_entry)
        # Parse the TOML to validate it.
        with zf.open(toml_entry) as f:
            try:
                toml.loads(f.read().decode("utf-8"))
            except Exception as e:
                raise ValueError("%s.toml parse error: %s" % (theme_name, e))
        return theme_name


def cmd_validate_theme(args):
    if len(args) < 1:
        emit_err("usage: validate-theme <zip>", EXIT_INVALID_ARGS)
    zip_path = Path(args[0]).resolve()
    try:
        theme_name = _validate_theme_zip(zip_path)
    except ValueError as e:
        emit_err(str(e), EXIT_INVALID_THEME)
    target = THEMES_DIR / theme_name
    emit_ok({
        "theme": theme_name,
        "valid": True,
        "already_installed": target.exists(),
    })


def cmd_upload_theme(args):
    if len(args) < 1:
        emit_err("usage: upload-theme <zip>", EXIT_INVALID_ARGS)
    zip_path = Path(args[0]).resolve()
    try:
        theme_name = _validate_theme_zip(zip_path)
    except ValueError as e:
        emit_err(str(e), EXIT_INVALID_THEME)
    target = THEMES_DIR / theme_name
    if target.exists():
        emit_err("theme %r already installed; delete first or rename"
                 % theme_name, EXIT_INVALID_THEME, theme=theme_name)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(THEMES_DIR)
    except Exception as e:
        emit_err("extraction failed: %s" % e, EXIT_INTERNAL)
    emit_ok({"theme": theme_name, "path": str(target)})


def cmd_delete_theme(args):
    if len(args) < 1:
        emit_err("usage: delete-theme <name>", EXIT_INVALID_ARGS)
    name = args[0]
    if not _theme_name_valid(name):
        emit_err("invalid theme name %r" % name, EXIT_INVALID_ARGS)
    if name == "default":
        emit_err("default theme cannot be deleted", EXIT_PERMISSION)
    runtime = load_runtime_for_write()
    active = str(runtime.get("theme", {}).get("active", "default"))
    if name == active:
        emit_err("theme %r is currently active — switch first" % name,
                 EXIT_PERMISSION, active=active)
    target = THEMES_DIR / name
    if not target.is_dir():
        emit_err("theme %r not found" % name, EXIT_NOT_FOUND)
    # Defense in depth: ensure resolved target is inside THEMES_DIR. Guards
    # against symlink escapes if a malicious unzip ever sneaks a link past
    # _validate_theme_zip.
    if THEMES_DIR.resolve() not in target.resolve().parents:
        emit_err("theme path escapes themes dir", EXIT_PERMISSION)
    try:
        shutil.rmtree(target)
    except Exception as e:
        emit_err("delete failed: %s" % e, EXIT_INTERNAL)
    emit_ok({"theme": name})


def cmd_get_runtime(args):
    """Read-only dump of runtime.toml as JSON. Uses plain toml (no comment
    preservation needed for read-only output)."""
    if not RUNTIME_TOML.exists():
        emit_err("runtime.toml not found at %s" % RUNTIME_TOML, EXIT_NOT_FOUND)
    try:
        data = toml.loads(RUNTIME_TOML.read_text(encoding="utf-8"))
    except Exception as e:
        emit_err("runtime.toml parse error: %s" % e, EXIT_INTERNAL)
    emit_ok({"runtime": data})


def cmd_set_runtime(args):
    if len(args) < 3:
        emit_err("usage: set-runtime <section> <key> <value>", EXIT_INVALID_ARGS)
    section, key, raw_value = args[0], args[1], args[2]
    value = parse_value(raw_value)
    doc = load_runtime_for_write()
    if section not in doc:
        doc[section] = tomlkit.table() if _HAVE_TOMLKIT else {}
    doc[section][key] = value
    save_runtime(doc)
    emit_ok({
        "section": section, "key": key, "value": value,
        "type": type(value).__name__,
        "note": "restart required for change to take effect",
    })


def cmd_status(args):
    """Service active state, current active theme, and recent log entries
    matching ERROR/WARNING (last 10). Cheap aggregate for the plugin's
    status panel."""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", SYSTEMD_UNIT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=5, check=False, universal_newlines=True,
        )
        is_active = proc.stdout.strip() == "active"
    except Exception:
        is_active = False
    runtime = {}
    if RUNTIME_TOML.exists():
        try:
            runtime = toml.loads(RUNTIME_TOML.read_text(encoding="utf-8"))
        except Exception:
            pass
    active_theme = str(runtime.get("theme", {}).get("active", "default"))
    recent_errors = []
    if LOG_FILE.exists():
        try:
            tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
            for line in tail:
                if "ERROR" in line or "WARNING" in line:
                    recent_errors.append(line)
            recent_errors = recent_errors[-10:]
        except Exception:
            pass
    emit_ok({
        "service_active": is_active,
        "active_theme": active_theme,
        "recent_errors": recent_errors,
    })


def cmd_restart(args):
    """Restart the systemd unit. Relies on the volumio user having
    passwordless sudo for systemctl (set up by install.sh)."""
    try:
        proc = subprocess.run(
            ["sudo", "systemctl", "restart", SYSTEMD_UNIT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=False, universal_newlines=True,
        )
        if proc.returncode != 0:
            emit_err("restart failed: %s" % proc.stderr.strip(), EXIT_INTERNAL)
    except Exception as e:
        emit_err("restart failed: %s" % e, EXIT_INTERNAL)
    emit_ok({"restarted": SYSTEMD_UNIT})


COMMANDS = {
    "list-themes":     cmd_list_themes,
    "set-theme":       cmd_set_theme,
    "list-devices":    cmd_list_devices,
    "validate-theme":  cmd_validate_theme,
    "upload-theme":    cmd_upload_theme,
    "delete-theme":    cmd_delete_theme,
    "get-runtime":     cmd_get_runtime,
    "set-runtime":     cmd_set_runtime,
    "status":          cmd_status,
    "restart":         cmd_restart,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(EXIT_OK if len(sys.argv) > 1 else EXIT_INVALID_ARGS)
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        emit_err("unknown command %r. Known: %s" % (cmd, sorted(COMMANDS)),
                 EXIT_INVALID_ARGS)
    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()

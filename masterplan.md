# Master Plan — Synthwave-Display

> Living document. Update as decisions firm up.
>
> **Project name (locked 2026-05-13):** `Synthwave-Display`
> **Target GitHub repo (TBC):** `ashrond/Synthwave-Display` (private; user creates when ready to split)
> **Current repo:** `ashrond/Volumio-DisplayOLED` — retains all history, `dev/`, `masterplan.md`, planning artifacts. Becomes archive after split.
> **Split strategy:** option B — finish phases 5–8 here, then push a single clean first commit to the new repo. The new repo will be Volumio-plugin compliant from day one.

## North Star

Out of the box, the program runs with a baked-in default theme and sane runtime defaults. From there, the end user can — **without touching the filesystem or restarting the Pi by hand** — upload a custom theme via the Volumio WebUI, switch themes from a dropdown, delete themes they no longer want, and adjust runtime behavior (quiet hours, brightness, etc.) through a settings page.

Concretely, a user should be able to go from a fresh install to "playing music with a custom-themed display" in under a minute.

## Prerequisites

- **Volumio `ir_controller` plugin** is required for IR remote support. The plugin owns the IR-code → button-name decoding (via `/etc/lirc/lircrc`); our display only listens on a UDP port for button-name strings (`"KEY_PLAY"`, `"KEY_RIGHT"`, …) emitted by `tools/ir_dispatch.sh`. We do not — and will not — duplicate that mapping in our config.

## Three-Tier Configuration

The current single `config/theme.toml` mixes things that the WebUI should tune, things that belong to a theme, and things that are just hardcoded constants we want to keep out of the source code. Splitting into three files makes each boundary explicit and follows a single principle: **no behavior-defining magic numbers live in `.py` files**. Changing how the program behaves should always be a single-file edit, not a code search-and-replace.

### Tier 1 — `config/settings.toml` (program defaults, paths)

Defaults that the **WebUI never touches**. The home for every constant that would otherwise be hardcoded in Python source. Hand-edited rarely, usually by a developer making a low-level change.

- `[paths]` — `font_path`, `themes_dir`
- `[defaults]` — `default_theme` (the privileged fallback)
- `[volumio]` — `ws_url`
- `[animation]` — `gif_frame_period_s` (animated screens), `screensaver_frame_period_s`, `contrast_update_interval_s`
- `[internal]` — `trace_buffer_size`, `slow_paint_ms`, `slow_handler_ms`, `watchdog_timeout_s`, `watchdog_check_interval_s`

### Tier 2 — `config/runtime.toml` (WebUI-managed)

Everything the WebUI writes. Single source of truth for runtime behavior, hardware selection, and active-theme reference.

- `[display]` — screen `type`, `width`, `height`, `spi_bus`, `spi_cs`, `spi_speed_hz`. Future SSD1306/etc. support adds new `type` values.
- `[logging]` — enabled, level, file path, rotation
- `[volume]` — max, button recent window
- `[menu]` — timeout, IR UDP port
- `[timing]` — playback refresh, volume hold, idle-after-stop, screen-off-after-idle, render tick, pause-to-play debounce
- `[burnin]` — quiet hours, pixel shift, scanlines, track fade, startup contrast
- `[ir]` — `skip_anim_cooldown_s` and `[ir.debounce]` per-button auto-repeat suppression
- `[theme]` — `active = "default"` selects which theme folder to load

### Tier 3 — `themes/<name>/<name>.toml` (theme identity)

Visual identity. One file per theme, distributed in the theme zip.

- `[meta]` — `name`, `version`, `author`, `description`, `schema_version`
- `[fonts]` — `time`, `title`, `artist`, `volume` (point sizes)
- `[colors]` — `text_color`, `background_color` *(monochrome panels treat as binary on/off; full RGB plumbing for future color screens is untested)*
- `[layout.playback]` — `time_y`, `title_y`, `progress_bar_y`, `progress_bar_height`, `progress_bar_width`, `artist_y`, `marquee_speed_px_per_s`, `marquee_gap_px`
- `[layout.loading]` — `text_y`, `gif_y`, `gif_height`
- `[transitions]` — `fade_seconds`, `transition_hold_at_end_seconds`, `transition_fade_portion`, `skip_gif_frame_period_s`, `skip_slide_trigger_frac`, `skip_slide_duration_s`, `skip_gif_brightness_boost`
- `[screensaver]` — `mode`, `target_population`, `spawn_rate`, `median_speed`, `drift_x`, `drift_y`
- `[assets]` — *optional* filename overrides; canonical names work without this block

Any key omitted from a theme's `<name>.toml` falls through to the **default theme's** value. A minimal custom theme can declare only what it changes.

## Theme Folder Structure

```
themes/
  default/
    default.toml
    assets/
      startup.gif
      idle.gif
      loading.gif
      play-to-pause.gif
      pause-to-play.gif
      skip-forward.gif
      skip-backward.gif

  vaporwave/
    vaporwave.toml
    assets/
      startup.gif
      idle.gif
      ...
```

Rules:
- Each theme is **fully self-contained in one folder**.
- The TOML file inside a theme folder **must** be named `<folder_name>.toml`. The loader uses this convention to find it without an explicit pointer.
- Assets live in `assets/` and use **canonical filenames** (`startup.gif`, `idle.gif`, …). Folder isolation makes hex-prefixed uniqueness unnecessary.
- A theme can omit any asset; missing assets fall through to `default/assets/`.

## Default Theme Guarantees

`themes/default/` is privileged:
- Cannot be deleted via WebUI.
- Provides the fallback for any asset or config key omitted by another theme.
- Always present in a fresh install — if missing on startup, the program should refuse to run (clear error).

## Migration Path (from current state)

Bundled into a single commit so the repo never lands in a broken in-between state:

1. Create `themes/default/` with `default.toml` carrying the current visual values.
2. Move existing `assets/*.gif` runtime files into `themes/default/assets/`.
3. Create `config/settings.toml` with paths and program defaults.
4. Create `config/runtime.toml` carrying current behavior/timing values.
5. Refactor `config/config.py` to load all three tiers, resolve the active theme by overlaying it on the default's values, and resolve asset paths via the theme dir with fallback to default.
6. Update `main.py` and `screens/playback.py` to source previously-hardcoded constants from `config.config`.
7. Delete `config/theme.toml`. Remove old `assets/*.gif` from the runtime path. Top-level `assets/Origional/`, `assets/Other/` (git-tracked design references that never deploy to the Pi) can stay where they are or move to `design/` in a follow-up.

## WebUI Integration (future phase, separate work)

End state in the Volumio plugin UI:

- **Theme dropdown** — populates from `themes/*/` folder list. Selecting writes `runtime.toml`'s `[theme] active` and triggers a service restart.
- **Upload Theme** — file picker, accepts `.zip`. Server-side handler:
  1. Extract to a temp dir
  2. Validate: exactly one top-level folder, that folder contains `<folder>.toml`, no path traversal in any zip entry
  3. Move to `themes/<folder>/`
  4. Refresh dropdown
- **Download Default Theme** — button that zips up `themes/default/` (including its README.md) and serves it. Lets users grab a working template to author against.
- **Delete Theme** — per-theme button, disabled for `default` and the currently active theme.
- **Runtime settings form** — generated from `runtime.toml` schema. Writes back to `runtime.toml`.
- **Display config section** — screen type dropdown, dimensions, SPI bus/CS/speed. Currently only SSD1322 is wired up; the form lists more options for future expansion.

## Repo Conventions

- **`dev/`** — non-production scratch space: orphan assets that might be useful again later, design references, debug outputs, etc. Excluded from production releases. Anything that gets moved here is on the path to deletion but not deleted yet.
- **`themes/default/README.md`** — the theme-author guide. Lives inside the default theme so it ships with any "Download Default Theme" zip and serves as a working template. Update it whenever the theme schema changes.

## Open Decisions

- **Hot-reload vs restart on theme change.** Restart is simpler (boot is ~3 s) and guarantees a clean state. Hot reload would need to invalidate the sprite cache, the loading-frame cache, the GIF frame cache, and re-resolve all `font_*` references. Suggest **restart** for v1.
- **Theme schema versioning.** When the schema evolves (new layout knob added), missing keys fall through to default. `schema_version` field lets us warn about themes targeting an incompatible version later.
- **Per-screen layout.** Loading screen and playback layouts are theme-able from day one. Menu, volume, idle, and the transition overlay are not yet theme-customizable; revisit when a real third-party theme demands it.
- **Validation of uploaded theme zips.** Strict (reject if any canonical asset missing) vs lenient (warn + fall through to default). Suggest **lenient** — it's friendlier and fallback already exists.

## Phase Status

- [x] Masterplan + architecture
- [x] Phase 1: Theme directory migration (move assets, create default.toml)
- [x] Phase 2: Settings/runtime config split (replace theme.toml)
- [x] Phase 3: Theme loader with default fallback
- [x] Phase 4: Asset path refactor (remove hardcoded `assets/`)
- [x] Phase 5a: Screen abstraction — `screens/devices/{base,oled_ssd1322,tft_ili9341}.py`, capability flags, theme `supported_screens` tag, ILI9341 stub. Done 2026-05-13.
- [x] Phase 5b: `tools/admin.py` — CLI bridge the WebUI plugin shells out to. Done 2026-05-13. Commands: list-themes, set-theme, validate-theme, upload-theme, delete-theme, list-devices, get-runtime, set-runtime, status, restart. Comment-preserving `runtime.toml` writes via tomlkit (added to requirements). Quiet mode via `VFD_NO_LOG=1` keeps admin CLI invocations out of `/tmp/vfd.log`.
- [ ] Phase 5c: Refactor `main.py` to use device factory; gate burn-in mitigations on `device.is_oled`
- [x] Phase 6: Plugin scaffolding (`plugin/` subfolder) — index.js, package.json, UIConfig.json, install.sh + uninstall.sh, i18n. Done 2026-05-13. Scoped sudoers fragment, plugin-rooted systemd ExecStart, runtime.toml preserved across upgrades, full lifecycle + theme dropdown + burn-in subset + restart button. End-to-end test deferred to Phase 8.
- [x] Phase 7: Plugin — theme list/upload/delete/download handlers. Done 2026-05-13. UIConfig section_themes_manage with path-input upload, dynamic per-theme delete buttons (injected by getUIConfig), and download-default button. admin.py gained `download-theme` command. End-to-end zip round-trip verified. **Limitation:** upload is path-based (user puts zip on Pi via SCP/file-manager, then pastes path). True browser file-picker is a future enhancement once we know the right Volumio file-upload pattern.
- [x] Phase 8: Plugin — runtime settings form + display hardware form. Done 2026-05-13. Full UIConfig now exposes Display Hardware, Burn-in (expanded), Timing, Volume, IR Remote, Logging — every key in `runtime.toml` is editable. `_saveSectionFields` helper unifies the save handlers via field-map tuples. `admin.py set-runtime` accepts dotted section paths (e.g. `ir.debounce KEY_RIGHT 0.8`) so nested TOML tables are addressable from the CLI.
- [x] Pre-install prep (2026-05-13): plugin's systemd unit renamed from `volumio-display.service` (collides with dev install) to `synthwave-display.service`. `admin.py` now reads `[systemd] service_name` from `settings.toml` with backward-compatible fallback. `tools/build-plugin-zip.sh` assembles a deployable zip from `plugin/` + the Python source as `display/` and rewrites the bundled settings.toml's service_name to match. `.gitignore` updated to exclude `dist/` and `plugin/node_modules/`. Two installs can now coexist on the same Pi with zero collision: dev at `/home/volumio/Volumio-Display/` driving `volumio-display.service`, plugin at `/data/plugins/user_interface/synthwave_display/` driving `synthwave-display.service`.
- [x] End-to-end install test (2026-05-13). Uninstalled dev, installed plugin zip via `volumio plugin install` (CLI — Volumio 3 has no WebUI upload button). Several install-side fixes shaken out, see Post-install work below.
- [ ] Split: pick name, create new private repo, push fresh-history production tree

## Post-install work (2026-05-13 → 2026-06-05)

A run of fixes / features that landed after the initial install. All in `ashrond/Volumio-DisplayOLED`.

### Plugin install hardening (2026-05-13)

- `install.sh` and `uninstall.sh` re-exec under bash. Volumio invokes plugin install scripts via `/bin/sh` (dash), bypassing the `#!/bin/bash` shebang; the scripts now `exec /bin/bash "$0" "$@"` if `BASH_VERSION` is empty so `set -euo pipefail`, `[[ ]]`, and heredocs work.
- `install.sh` runs `npm install --production` itself. Volumio's plugin pipeline does NOT install Node deps automatically — it expects the zip to ship with `node_modules/` pre-populated, OR for the plugin's own install.sh to do it.
- `package.json` deps pinned to versions that actually exist on npm (`v-conf@^1.4.3`, `fs-extra@^8.1.0`). `^1.5.0` and `^10.0.0` were wishful.
- `index.js` constructor: `this.commandRouter = context.coreCommand` (was `context.coreCommandRouter`, which is undefined). Without this, getUIConfig throws synchronously and Volumio falls back to "No Configuration Available".
- Systemd unit: `StartLimitIntervalSec` and `StartLimitBurst` moved to `[Unit]` to clear the warning on Buster's older systemd.

### IR routing that survives Volumio restart (2026-05-13)

- `ir_controller` rewrites `/etc/lirc/lircrc` from its active profile (A1156_Custom for Apple Remote) on every Volumio start, clobbering our routing.
- New `tools/lircrc.template` is the single source of truth for our action routing; `tools/setup-lircrc.sh` copies it to `/etc/lirc/lircrc` and restarts `irexec.service`.
- `index.js` `onStart()` spawns `sudo setup-lircrc.sh` before starting our systemd unit. `user_interface` plugins load AFTER `system_hardware`, so we win the race naturally.
- `install.sh` primes the routing once; sudoers fragment grants NOPASSWD for `setup-lircrc.sh` so the plugin can re-run it.
- `uninstall.sh` restores `/etc/lirc/lircrc.preplugin` so the original (volumio-direct) routing comes back if the plugin is removed.

### Volume max sourced from Volumio (2026-05-13)

- `volume.max` removed from `runtime.toml`, `config.py`, the WebUI settings form, and `saveVolume` handler.
- `main.py` reads `/data/configuration/audio_interface/alsa_controller/config.json` (`volumemax.value`) before the render thread starts; a daemon thread re-reads every 15 min (no socketio event fires when this changes).
- Single source of truth is Volumio's `alsa_controller` plugin.

### Settings UI restructure (2026-05-13)

- Display Hardware moved to the top of the WebUI settings page.
- Two new toggle sections:
  - **Show Advanced Settings** — gates Burn-in, Timing, IR Remote, Logging.
  - **Custom Themes** — gates Theme (active dropdown) + Manage Themes (upload/download/delete).
- Both off by default; the page renders 5 sections (Display, Volume, Actions, the two toggles). State persists in v-conf; user must close+reopen Settings for the change to take effect (Volumio's UIConfig only fires on form open).
- Volumio 3 has no native section collapse — there's a `// TODO: check if the section can be collapsed` in `/volumio/app/statemachine.js`. The toggle approach is the closest substitute.
- Download-default-theme button carries an explanatory description.

### Onscreen menu (2026-05-13 → 2026-06-05)

- New items:
  - **Set Max Vol** — single-value numeric editor (UP/DN ±1, L/R ±5, PLAY save, MENU cancel). Implemented via a new `_EditorPage` class that piggybacks on `paint_menu` via an `items` property.
  - **Bluetooth: ON/OFF** — top-level label shows current state from `systemctl is-active bluetooth.service`; submenu offers Enable / Disable / Back. Toggles the systemd unit.
  - **Restart System** — replaces the bare Reboot item. Submenu: Restart / Shutdown / Back. Both already NOPASSWD via Volumio's base sudoers (`/sbin/reboot`, `/sbin/poweroff`).
- Menu typography bumped (theme keys `menu_title = 14`, `menu_item = 12`; renderer falls back to existing `title` / `artist` sizes for older themes). 1px underline beneath the title separates it from the item list visually.

### Bug fixes from the menu work

- `_handle_pushstate` now only overwrites cached `random` / `repeat` / `repeatSingle` / `mute` when the key is actually present in the message. Volumio sends partial pushStates (seek-progress ticks) that omit those fields; the previous `data.get(..., False)` clobbered cached state on every tick. Symptom: menu showed Repeat: Off after every program restart even though Volumio itself still had repeat on.
- Set Max Vol "audible spike to 100%" workaround. `saveVolumeOptions` rebuilds the alsa mixer; as a side effect Volumio's internal volume value snaps to 100 for ~3s before being rescaled to the user's value at the new max. Pause alone wasn't enough — the resume itself plays at vol=100 during that window. Fixed sequence: capture pre-save volume, pause MPD, save options, wait 1.5s for the rebuild, force-set volume back to the captured value (clamped to new max), wait 0.25s, resume.

### Health (2026-06-05)

22+ days on a single PID since the May 13 deploy. Zero errors. Memory flat at 17 MB RSS. The only warnings are paint/handler "SLOW" lines clustering on Volumio track-change pushState bursts — track boundary jitter, not a real issue. `slow_paint_ms` raised 30 → 50 and `slow_handler_ms` 20 → 40 in `settings.toml` to stop logging benign jitter while still surfacing genuine outliers.

Phases 5–8 land in **this repo** before the split. When complete, the new plugin repo gets a single clean push of the production tree (display code + plugin scaffolding); `dev/`, `masterplan.md`, and history stay here.

## Plugin Architecture (target after split)

Volumio plugins are Node.js. The plugin wraps our Python display:

```
volumio-display-plugin/
├── index.js                          # Volumio plugin interface (onStart, saveConfig, etc.)
├── package.json                      # plugin metadata, volumio_info block
├── config.json                       # plugin's own defaults (mostly empty)
├── UIConfig.json                     # declarative settings form
├── install.sh                        # apt deps + runs display/install/install.sh
├── uninstall.sh
├── i18n/strings_en.json
├── README.md                         # public-facing
├── LICENSE
└── display/                          # the Python display (current repo)
    ├── main.py
    ├── config/
    ├── themes/default/
    ├── screens/
    │   ├── devices/                  # NEW — screen abstraction
    │   │   ├── base.py
    │   │   ├── oled_ssd1322.py
    │   │   └── tft_ili9341.py
    │   ├── playback.py
    │   ├── screensaver.py
    │   └── …
    ├── tools/
    │   ├── admin.py                  # NEW — CLI bridge
    │   └── ir_dispatch.sh
    └── install/
        ├── volumio-display.service
        ├── install.sh
        └── requirements.txt
```

Node ↔ Python contract: filesystem (TOML files) + service manager (systemctl) + a thin `tools/admin.py` CLI (`list-themes`, `set-theme`, `upload-theme`, `delete-theme`, `set-runtime`, `restart`). No IPC. Plugin shells out via `child_process`.

## Screen Abstraction (Phase 5a)

Currently `main.py` is hardcoded to `ssd1322`. The abstraction lets future screen types drop in without touching the orchestrator.

`screens/devices/base.py` exposes a `Device` interface:
- `device` — underlying luma instance
- `width`, `height`, `mode`
- `is_oled: bool` — gates contrast(), hide()/show(), the `_fast_greyscale_display` override, and burn-in mitigations (scanlines, pixel shift, contrast fade) — none of which apply to a TFT
- standard methods: `display(image)`, `hide()`, `show()`, `contrast(level)`

A factory `create_device(runtime_cfg)` reads `[display] type` from `runtime.toml` and returns the right subclass. Themes carry `[meta] target_screen = "ssd1322"`; loader warns on mismatch but doesn't refuse (lets users experiment).

ILI9341 ships as a stub that raises `NotImplementedError` — schema valid, code path open, theme work deferred to whenever someone (us, or a contributor) authors a 320×240 theme.

## Install-time screen picker

Out of scope for the install script. Plugin installs with `[display] type = "ssd1322"` as default. User picks the screen type via the Volumio WebUI display-plugin settings page; saving writes `runtime.toml` and restarts the service. More Volumio-idiomatic, works for headless/automated installs.

## Better-ways review (process improvements to consider before phase 5a)

A handful of "we'd do this differently if starting from scratch" items, ranked by impact. Not all need to land before the split; flagging now so we don't forget.

1. **Python virtualenv for install.** Current `install.sh` does `pip install --user`, which mingles our deps with anything else running as the `volumio` user. A dedicated venv at `/opt/synthwave-display/venv/` (or equivalent) isolates us cleanly and survives a system pip upgrade. Update systemd unit's `ExecStart` to point at the venv's python. Low-risk, fairly contained change.
2. **Theme schema validator.** `tools/admin.py validate-theme <path>` runs static checks against a candidate theme before the plugin extracts it. Fail-fast on missing required keys, unknown sections, malformed asset filenames, paths that would escape the theme folder when unzipped. Plugin calls this before committing to the install.
3. **JSON Schema for runtime.toml.** Volumio's `UIConfig.json` is its own DSL, but we can derive most of it from a JSON Schema we maintain alongside `runtime.toml`. Single source of truth for "what fields exist, what types, what defaults, what labels for the WebUI." Generator script keeps `UIConfig.json` in sync.
4. **Simulation device for tests.** `screens/devices/simulation.py` writes PIL `Image` frames to disk instead of SPI. Lets us run integration tests on CI without hardware. Cheap to add when we do the device-abstraction refactor anyway.
5. **Idempotent install.sh.** Re-running install should never break the system. Currently the script *probably* is, but worth making it explicit (check-before-write everywhere, refuse to overwrite a user's customized `runtime.toml`).
6. **Plugin upgrade flow.** When the user upgrades the plugin to a new version, their `runtime.toml` shouldn't get clobbered. Plugin install should merge incoming defaults with existing user values, not replace. Schema versioning helps here.
7. **GitHub Actions CI.** Pre-commit hooks plus a CI run that at minimum: lints (`flake8` or `ruff`), runs `python -m py_compile` on every file, validates the default theme TOML, validates `UIConfig.json` matches the runtime schema, runs whatever pytest we have. Pi-targeted ARM tests likely not feasible on GH runners; simulation device covers most logic.
8. **CHANGELOG.md** committed alongside the code. Users browsing the new repo can see what shipped when.
9. **License decision.** What does Volumio require for plugins? Need to check — likely permissive (MIT / Apache-2.0) since they're loaded into a GPL-ish environment. Pick before first push to new repo.
10. **`AUTOSTART.md` retirement.** That file currently has install notes that overlap `install/install.sh`. Either fold those into a README aimed at developers (not end users — end users use the WebUI) or delete. Pre-split cleanup task.

## Decision log

- **2026-05-13 — Name picked: `Synthwave-Display`.** Old repo retains history & dev/.
- **2026-05-13 — Split strategy: option B.** New repo's first commit is Volumio-plugin compliant; we do phases 5–8 here first.
- **2026-05-13 — No original-project attribution.** No code carried forward from Maschine2501/Volumio-OledUI; functionally a clean rewrite.
- **2026-05-13 — ILI9341 support: stub-ready, theme-deferred.** Schema accepts it, code stubs `NotImplementedError`, no 320×240 theme until someone writes one.
- **2026-05-13 — Install-time screen picker: WebUI only, no shell prompt.** Avoids breaking headless installs.
- **2026-05-13 — Display push path: stay with luma.oled + `_fast_greyscale_display_*` override.** Audited alternatives (fbtft, direct spidev, native C extension); none offer meaningful CPU/complexity wins at current load (5.1% lifetime, 11% peak). luma also covers ILI9341 and many other panels out of the box, which makes the screen-abstraction phase easier.
- **2026-05-13 — Node ↔ Python comms: filesystem + systemctl + `tools/admin.py` CLI. No HTTP API.** Plugin writes `runtime.toml`, restarts the service for settings changes; shells out to `admin.py` for theme operations that need Python-side validation. HTTP API can be added later as a layer on top if hot-reload-without-restart becomes important — additive, not a rewrite.
- **2026-05-13 — We call Volumio's APIs (socketio + REST); Volumio does NOT call us.** One-directional. Don't confuse "we use Volumio's API" with "we expose an API."
- **2026-05-13 — Themes declare `[meta] supported_screens = [...]` (list, not single string).** Forward-compatible with multi-screen themes when a second panel ships. Per-screen layout overrides (e.g. `[layout.playback.ili9341]`) deferred — add when a real TFT exists to validate against.
- **2026-05-13 — `Device.category` ("OLED" / "TFT") class attribute.** Lets the future WebUI's two-stage selector (category → specific chip) populate itself from whatever drivers are present, with no separate registry to maintain.
- **2026-05-13 — Install-time screen selector reaffirmed as WebUI-only.** Default install = `ssd1322`; user picks anything else via the plugin settings page.
- **2026-05-13 — `tomlkit` added to requirements** for comment-preserving runtime.toml writes via `tools/admin.py`. Plain `toml` is still used for read-only parsing in `config/config.py` (hot path) where comment preservation doesn't matter.
- **2026-05-13 — `VFD_NO_LOG=1` env-var convention.** Anything that imports `config.config` but isn't the main display program (admin CLI, future test harnesses, simulation device) sets this to suppress log handler installation. Keeps `/tmp/vfd.log` clean of per-invocation noise.
- **2026-05-13 — Plugin path = service path.** Systemd unit (rendered by `plugin/install.sh`) ExecStart points at `<plugin_dir>/display/main.py`. All Python code, themes, and `runtime.toml` live inside the plugin folder Volumio extracts. Eliminates "where does the display code live" ambiguity. Plugin upgrades re-extract everything BUT `runtime.toml` is never overwritten by install.sh, so user settings carry through.
- **2026-05-13 — Scoped passwordless sudo, not blanket.** `install.sh` writes `/etc/sudoers.d/synthwave-display` with NOPASSWD only for `systemctl <start|stop|restart|is-active> volumio-display.service`. Lets `admin.py restart` and the plugin's onStart/onStop work without prompting; no general escalation. Removed cleanly on uninstall.
- **2026-05-13 — Volumio 3 has no WebUI plugin upload.** Stock builds expect `volumio plugin install` from a directory containing `package.json`. The CLI re-zips the source, posts to `/tmp/plugins/<name>.zip`, and emits an `installPlugin` socket event. Documented in `plugin/README.md`.
- **2026-05-13 — Volumio plugin install does NOT run `npm install`.** The pipeline assumes the zip ships with `node_modules/` populated, or the plugin's own install.sh handles it. We do the latter (`sudo -u volumio npm install --production --no-package-lock --no-audit`). Avoids bundling node_modules in the zip.
- **2026-05-13 — `node_modules/` not bundled in plugin zip.** `tools/build-plugin-zip.sh` deliberately excludes it; the plugin's install.sh installs deps on-device. Keeps the zip small and avoids architecture/Node-version pitfalls in pre-built node modules.
- **2026-05-13 — Volumio 3 has no soft plugin reload.** `pluginmanager.js` lacks `delete require.cache`; `disablePlugin` + `enablePlugin` just flip the `enabled` flag without re-requiring the module. To pick up a new `index.js` we restart `volumio.service`. UIConfig.json changes don't require a restart because `i18nJson` re-reads UIConfig.json on every `getUIConfig` call.
- **2026-05-13 — IR routing: plugin manages `/etc/lirc/lircrc`, not lircd.conf.** `ir_controller` owns the IR-code → KEY_NAME decode via `lircd.conf` (driven by the user's chosen profile). We replace the action side (`lircrc`) to forward keys through `tools/ir_dispatch.sh` so the display program sees buttons over UDP first. `ir_controller` rewrites lircrc on every Volumio start; our plugin's `onStart()` re-runs `setup-lircrc.sh` afterwards because `user_interface` plugins load after `system_hardware`. If the user switches ir_controller profile, they re-enable our plugin to put the action routing back. We do not — and will not — duplicate the IR-code mapping.
- **2026-05-13 — Volume max sourced from Volumio, not our config.** `alsa_controller`'s `volumemax` is the single source of truth. main.py reads `/data/configuration/audio_interface/alsa_controller/config.json` at startup, refreshes every 15 min via daemon thread (no socketio event fires when this changes). Removed from `runtime.toml` + WebUI + saveVolume handler.
- **2026-05-13 — Set Max Vol pauses MPD around the save.** `saveVolumeOptions` rebuilds the alsa mixer; Volumio's internal volume value snaps to 100 for ~3s before being rescaled. Pause + wait 1.5s + force-set volume back to pre-save value + wait 0.25s + resume. The audible spike disappears at the cost of a ~1.75s silent gap.
- **2026-05-13 — Settings page UI: Volumio 3 has no native section collapsing.** Confirmed via `// TODO: check if the section can be collapsed` in `/volumio/app/statemachine.js`. The plugin offers two toggle sections — Show Advanced Settings, Custom Themes — that filter sections out of the `getUIConfig` response. User must close + reopen Settings for changes to apply since `getUIConfig` only fires on form open. Closest substitute to a collapsible UI we can build without patching Volumio core.
- **2026-06-05 — `slow_paint_ms` and `slow_handler_ms` thresholds raised** to 50 / 40 (from 30 / 20). The earlier values were caught up in Volumio's track-change pushState burst and logging ~30 WARN/day of benign jitter. 50ms matches `RENDER_TICK_SECONDS = 0.05`; 40ms gives handlers slack for the burst window. Genuine outliers still surface.

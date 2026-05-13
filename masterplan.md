# Master Plan — Volumio Display Theme & Config System

> Living document. Update as decisions firm up.

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
- [ ] Phase 1: Theme directory migration (move assets, create default.toml)
- [ ] Phase 2: Settings/runtime config split (replace theme.toml)
- [ ] Phase 3: Theme loader with default fallback
- [ ] Phase 4: Asset path refactor (remove hardcoded `assets/`)
- [ ] Phase 5: Theme switch CLI/endpoint
- [ ] Phase 6: WebUI upload/extract handler
- [ ] Phase 7: WebUI dropdown + delete
- [ ] Phase 8: WebUI runtime settings form + display hardware form

Phases 1–4 will land in a single commit. Each subsequent phase should leave the display in a fully-working state.

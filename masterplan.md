# Master Plan — Volumio Display Theme & Config System

> Living document. Update as decisions firm up.

## North Star

Out of the box, the program runs with a baked-in default theme and sane runtime defaults. From there, the end user can — **without touching the filesystem or restarting the Pi by hand** — upload a custom theme via the Volumio WebUI, switch themes from a dropdown, delete themes they no longer want, and adjust runtime behavior (quiet hours, brightness, etc.) through a settings page.

Concretely, a user should be able to go from a fresh install to "playing music with a custom-themed display" in under a minute.

## Three-Tier Configuration

The current single `config/theme.toml` mixes things that should never change at runtime (SPI device, screen type), things the WebUI ought to tune (quiet hours, fade durations), and things a theme should own (layout positions, font sizes, colors). Splitting these into three files makes the boundary explicit and lets the WebUI safely write to the runtime config without risk of breaking the install.

### Tier 1 — `config/settings.toml` (system / install-time)

Set once at install, rarely modified by hand, **never written by the WebUI**.

- `[device]` — screen type (`ssd1322`), dimensions, SPI bus/port/speed
- `[paths]` — log file location, themes directory
- `[volumio]` — websocket URL
- `[active]` — `theme = "default"` — selects which theme folder to load
- `[runtime]` — `config = "runtime.toml"` — pointer to tier-2 file

### Tier 2 — `config/runtime.toml` (WebUI-tunable)

Behavior knobs. WebUI writes this file. Display reloads on change (or restarts — see Open Decisions).

- `[logging]` — enabled, level, max bytes, backup count
- `[volume]` — max, button recent window
- `[menu]` — timeout, IR UDP port
- `[timing]` — playback refresh, volume hold, idle-after-stop, screen-off-after-idle, render tick, transition hold/fade
- `[burnin]` — quiet hours, pixel shift, scanlines, track-fade, startup contrast
- `[ir]` — per-button debounce, skip burst cooldown

### Tier 3 — `themes/<name>/<name>.toml` (theme identity)

Visual identity. One file per theme. Distributed in the theme zip.

- `[meta]` — `name`, `version`, `author`, `description`
- `[fonts]` — `time`, `title`, `artist`, `volume` (point sizes)
- `[colors]` — `text_color`, `background_color`
- `[layout.playback]` — `time_y`, `title_y`, `progress_bar_y`, `progress_bar_width`, `artist_y`, `progress_bar_height`
- `[layout.loading]` — `text_y`, `gif_y`, `gif_height`
- `[transitions]` — `fade_seconds`, `skip_slide_trigger_frac`, `skip_slide_duration_s`, `skip_gif_frame_period_s`, `skip_gif_brightness_boost`
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
      pause.gif
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

In order, each step independently shippable:

1. **Create `themes/default/`** — move existing `assets/*.gif` into `themes/default/assets/`. Write `themes/default/default.toml` carrying the current layout/transition values verbatim. Display behavior identical.
2. **Split current `config/theme.toml`** into `config/settings.toml` + `config/runtime.toml` per the schema above. `config/config.py` loads both.
3. **Theme loader** — read `settings.active.theme`, locate `themes/<name>/<name>.toml`, build the effective theme config by overlaying it on the default's values. Provide all currently-used `config.config.X` constants from this resolved view.
4. **Refactor asset paths** in `main.py` so all GIF loading goes through the theme loader rather than hardcoded `assets/*.gif` paths.
5. **Theme switch command** — a small CLI / endpoint that updates `settings.toml`'s `active.theme` and restarts the display service. The WebUI plugin will call this.
6. **WebUI integration** (separate phase — see below).

## WebUI Integration (future phase)

End state in the Volumio plugin UI:

- **Theme dropdown** — populates from `themes/*/` folder list. Selecting a theme writes `settings.toml`'s `active.theme` and triggers a service restart.
- **Upload Theme** — file picker, accepts a `.zip`. Server-side handler:
  1. Extract to a temp dir
  2. Validate: exactly one top-level folder, that folder contains `<folder>.toml`, all referenced assets present, no path traversal in any zip entry
  3. Move to `themes/<folder>/`
  4. Refresh dropdown
- **Delete Theme** — per-theme button, disabled for `default` and the currently active theme.
- **Runtime settings form** — generates form controls from `runtime.toml` schema. Writes back to `runtime.toml`, optionally with a "live preview" mode that doesn't persist.

## Open Decisions

- **Hot-reload vs restart on theme change.** Restart is simpler (boot is ~3s) and guarantees a clean state. Hot reload would need to invalidate the sprite cache, the loading-frame cache, the GIF frame cache, and re-resolve all `font_*` references. Suggest **restart** for v1.
- **Theme schema versioning.** When the schema evolves (new layout knob added), how do old themes behave? Easiest: missing keys fall through to default. Worth adding a `schema_version` field early so we can warn about themes targeting an incompatible version later.
- **Per-screen layout.** Loading screen layout is already in this plan. Should menu, volume, idle also be theme-customizable? Probably yes long-term, but v1 may keep them centralized.
- **Color depth in theme files.** SSD1322 is 4-bit grayscale; theme colors can be `"white"` or 0-255. If we ever support color screens, themes need RGB tuples. Plan for the abstraction now (use string-or-tuple values?) or punt.
- **Validation of uploaded theme zips.** How strict? Reject themes missing any canonical asset, or warn and fall through to default? Suggest **warn + fall through** — it's friendlier and our fallback logic handles it.

## Phase Status

- [ ] Phase 1: Theme directory migration (move assets, create default.toml)
- [ ] Phase 2: Settings/runtime config split
- [ ] Phase 3: Theme loader with default fallback
- [ ] Phase 4: Asset path refactor (remove hardcoded `assets/`)
- [ ] Phase 5: Theme switch CLI/endpoint
- [ ] Phase 6: WebUI upload/extract handler
- [ ] Phase 7: WebUI dropdown + delete
- [ ] Phase 8: WebUI runtime settings form

Each phase should leave the display in a fully-working state.

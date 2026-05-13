# Volumio Display — Theme Author Guide

This folder is the **default theme**. It also serves as the canonical template for any custom theme. Copy this whole folder, rename it, edit the TOML, swap out GIFs, zip it up, and you have a new theme.

## Quick start

1. Make a copy of this folder. Rename it to your theme's name, e.g. `vaporwave/`.
2. Rename `default.toml` inside it to match the folder — `vaporwave/vaporwave.toml`. **The TOML filename must match the folder name** — the loader uses this convention.
3. Edit the renamed TOML. Update `[meta]` and any visual values you want to change.
4. Replace any GIF in `assets/` with your own. Keep the filenames the same — see "Asset filenames" below.
5. Zip the folder (the zip should contain exactly one top-level folder, your theme's folder).
6. Upload via the Volumio WebUI (Themes → Upload Theme). Or `scp` the folder into `themes/` on the Pi if you're working manually.

## Folder structure

```
yourtheme/
├── yourtheme.toml      # required; same basename as the folder
└── assets/             # GIF assets — canonical filenames
    ├── startup.gif
    ├── loading.gif
    ├── play-to-pause.gif
    ├── pause-to-play.gif
    ├── skip-forward.gif
    └── skip-backward.gif
```

You can include `README.md` and other documentation in your theme — they're ignored by the loader.

## Asset filenames

Use **canonical filenames** so the loader picks them up without configuration. These are the assets the program uses:

| Filename             | When it appears                                          | Required? |
|----------------------|----------------------------------------------------------|-----------|
| `startup.gif`        | Once, on program start                                   | No*       |
| `loading.gif`        | Between tracks while the next one is loading             | No*       |
| `play-to-pause.gif`  | Overlay when you press pause                             | No*       |
| `pause-to-play.gif`  | Overlay when you press play from paused                  | No*       |
| `skip-forward.gif`   | Overlay when you press right / next                      | No*       |
| `skip-backward.gif`  | Overlay when you press left / previous                   | No*       |
| `idle.gif`           | Only used if `[screensaver] mode = "gif"`                | No        |

\* Any asset you omit falls through to the default theme's version. You can ship a partial theme that only customizes the startup screen, for instance.

**The default theme uses procedural particles for idle**, so no `idle.gif` is shipped. If your theme sets `[screensaver] mode = "gif"`, you'll need to provide your own `assets/idle.gif`.

### Asset format

- **All GIFs are grayscale** — the SSD1322 is a monochrome panel. Color GIFs work but will be flattened to grayscale at load time, which may surprise you. Author in grayscale or you'll see weird brightness mappings.
- **Fullscreen GIFs should be 256×64**: `startup.gif`, `loading.gif`, `idle.gif`. Other sizes will be resized with LANCZOS to fit.
- **Overlay GIFs** (`play-to-pause`, `pause-to-play`, `skip-forward`, `skip-backward`) should be smaller (~92×60 is the convention) and centered around the action they signal. They composite over the underlying screen.

## Theme TOML

The TOML's structure is in `default.toml`. Every field is documented inline there.

A minimal theme that **only** changes the title font size:

```toml
[meta]
name           = "BigTitle"
version        = "1.0"
author         = "you"
description    = "Bigger title font."
schema_version = 1

[fonts]
title = 16
```

Everything else falls through to the default theme.

## Asset overrides (optional)

If you want to use non-canonical filenames, declare them in the `[assets]` block:

```toml
[assets]
startup       = "my-cool-intro.gif"
skip_forward  = "fast-forward.gif"
```

Key names use the canonical filename without the `.gif` extension and with hyphens turned to underscores (so `skip-forward.gif` → `skip_forward`).

## What you cannot customize (yet)

- The menu screen's layout
- The volume screen's layout
- The number of decimal places in the clock format
- Per-screen color palettes (the panel is monochrome anyway)

These will become theme-customizable as needs arise. File a feature request if you hit one.

## Testing your theme

1. Drop your theme folder into `themes/` on the Pi.
2. Edit `config/runtime.toml` and set `[theme] active = "yourtheme"`.
3. Restart: `sudo systemctl restart volumio-display`.
4. Watch the log: `tail -F /tmp/vfd.log` — the loader logs which theme was loaded and warns about any missing keys.

## Why my theme didn't load

- Filename of the TOML doesn't match the folder name.
- TOML has a syntax error — `python3 -c "import toml; toml.load('yourtheme.toml')"` will show the line.
- Active theme name in `runtime.toml` is misspelled or doesn't match the folder name.
- Permissions: the volumio user must be able to read the theme folder.

# Synthwave Display

OLED/TFT display driver for Volumio — animated playback screen, custom themes, transitions, and burn-in mitigation for always-on installs.

## Install

From the Volumio WebUI: **Plugins → Search → "Synthwave Display" → Install**.

Or from a release zip:

```
volumio plugin install <synthwave-display-x.y.z.zip>
```

## What it does

- Drives an SPI display (currently SSD1322 4-bit grayscale OLED; the schema accepts ILI9341 TFTs but the driver isn't wired up yet — the package supports future expansion).
- Renders the now-playing track with marquee scrolling, animated transitions (play/pause and skip-forward/skip-back), and a procedural particle screensaver during idle.
- Burn-in mitigation for OLED panels: quiet hours, pixel shift, scanline alternation, per-track brightness fade.
- Custom themes via WebUI upload (zip of layout + GIFs).

## Configure

The plugin's settings page (Plugins → Installed → Synthwave Display → ⚙) exposes:

- **Theme:** switch the active theme. Upload new themes as a zip (one folder containing `<name>.toml` + `assets/`).
- **Burn-in Mitigation:** quiet hours window, pixel shift on/off, scanline alternation on/off, per-track brightness fade on/off.
- **Actions:** restart the display service.

Lower-level settings (font sizes, layout coordinates, exact transition timings) live in the per-theme `.toml` — see the theme-author guide at `display/themes/default/README.md`.

## Hardware setup

Connect the SSD1322 to the Pi's SPI bus (default: SPI0, CS0, 8 MHz). Pinout depends on your panel — Adafruit's SSD1322 modules wire 4-pin SPI + DC + RST + CS.

If you have an ILI9341 TFT: open an issue or a PR. The driver stub at `display/screens/devices/tft_ili9341.py` has the steps for fleshing it out.

## Theme authoring

A theme is a folder containing one TOML file and an `assets/` subdir:

```
my-theme/
├── my-theme.toml
└── assets/
    ├── startup.gif
    ├── loading.gif
    └── …
```

See `display/themes/default/README.md` for the full author guide. The "Download Default Theme" button on the plugin settings page (TODO) zips the default theme as a starting template.

## Development

Plugin source lives at <https://github.com/ashrond/Synthwave-Display>. PRs welcome.

## License

MIT. © ashrond.

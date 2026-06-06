# Synthwave Display

OLED/TFT display driver for Volumio — animated playback screen, custom themes, transitions, burn-in mitigation, and an onscreen menu driven by your IR remote.

## Install

Volumio 3 does not ship an "Upload Plugin" button in the WebUI. Sideload from the CLI:

```bash
# unpack the release zip somewhere (the Pi may not have `unzip` — Python's
# zipfile module is fine)
mkdir -p /tmp/synthwave-install && cd /tmp/synthwave-install
python3 -m zipfile -e /path/to/synthwave-display-x.y.z.zip .

# install
volumio plugin install
```

The installer:

1. Writes `/etc/systemd/system/synthwave-display.service` (renders the path to
   the freshly-installed display program into the unit file).
2. Drops a scoped `/etc/sudoers.d/synthwave-display` so the plugin can manage
   its own service and refresh IR routing without password prompts.
3. Primes `/etc/lirc/lircrc` via `tools/setup-lircrc.sh` so IR buttons route
   through the display program (see "IR routing" below).
4. Enables + starts the service.

Once installed, the plugin appears under **Plugins → Installed Plugins → Synthwave Display**. Toggle it on and open Settings.

## What it does

- Drives an SPI display (currently SSD1322 4-bit grayscale OLED; the schema accepts ILI9341 TFTs but the driver isn't wired up yet — the package supports future expansion).
- Renders the now-playing track with marquee scrolling, animated transitions (play/pause and skip-forward/skip-back), and a procedural particle screensaver during idle.
- Burn-in mitigation for OLED panels: quiet hours, pixel shift, scanline alternation, per-track brightness fade.
- Custom themes via WebUI upload (zip of layout + GIFs).
- Onscreen menu navigable from an IR remote: track / playlist / webradio jump, shuffle and repeat toggles, set-max-volume, Bluetooth on/off, Restart / Shutdown.

## Settings page

The plugin's settings page (Plugins → Installed → Synthwave Display → ⚙) is intentionally short by default. Sections always visible:

- **Display Hardware** — screen type, width/height, SPI bus/CS/clock.
- **Volume** — heuristic window for the volume-button-recent detector.
- **Actions** — Restart the display service.
- **Advanced Settings** *(toggle)* — when on, reveals Burn-in Mitigation, Timing, IR Remote, and Logging sections on the next page open.
- **Custom Themes** *(toggle)* — when on, reveals Theme (active-theme dropdown) and Manage Themes (upload-from-path / download-default / delete-non-active) on the next page open.

Both toggles are off by default to keep the page compact. Volumio 3 has no native section collapsing, so toggling either checkbox + clicking Save + reopening Settings is the closest substitute.

Max volume is **not** in the settings page — it's owned by Volumio's `alsa_controller` plugin and read from disk at startup (refreshed every 15 min). To change it from the display itself, use the onscreen menu's "Set Max Vol" item.

Lower-level settings (font sizes, layout coordinates, exact transition timings) live in the per-theme `.toml` — see the theme-author guide at `display/themes/default/README.md`. The "Download default theme" button under Manage Themes zips the default theme as a starting template for new themes.

## Onscreen menu (IR remote)

The MENU button on your IR remote opens a stack-based menu rendered on the display. UP/DOWN navigates, PLAY selects, MENU backs out (and closes from the top level). LEFT/RIGHT are unused by the regular menu but are intercepted by the Set Max Vol editor for ±5 steps.

Top-level items:

- **Tracks** — current queue; pick any to jump to that track.
- **Playlists** — saved Volumio playlists.
- **Webradio** — saved web radios from My Web Radio / Favourites.
- **Shuffle: ON/OFF** — toggle Volumio's random.
- **Repeat: Off / All / Single** — cycles through Volumio's three repeat modes.
- **Set Max Vol** — numeric editor for Volumio's max volume; UP/DOWN ±1, LEFT/RIGHT ±5, PLAY to save. Pauses MPD around the save so the alsa mixer rebuild can't push an audible spike.
- **Bluetooth: ON/OFF** — current state from `systemctl is-active bluetooth.service`; submenu lets you start / stop the service.
- **Restart System** — submenu: Restart, Shutdown, Back.
- **Close** — leave the menu without doing anything.

## IR routing

Volumio's `ir_controller` plugin owns IR-code → KEY_NAME decoding (via `/etc/lirc/lircd.conf` for whichever remote profile you've selected). This plugin only intercepts the *action* side: `/etc/lirc/lircrc` routes every KEY_* to `display/tools/ir_dispatch.sh`, which fires the button name over a localhost UDP datagram. The display program either consumes the button (menu nav) or forwards it to Volumio (play/pause/skip/volume).

`ir_controller` rewrites `lircrc` from its active profile on every Volumio start, clobbering our routing. The plugin's `onStart()` calls `tools/setup-lircrc.sh` afterwards (user_interface plugins load after system_hardware, so we always win the race). If you change ir_controller's profile in the WebUI, re-enable this plugin to put the routing back.

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

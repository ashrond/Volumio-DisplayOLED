# Autostart on boot — how main.py gets launched after Volumio is up

This project uses a **systemd service unit** to start automatically on boot,
ordered after `volumio.service`. The unit lives at `install/volumio-display.service`
and is installed by `install/install.sh`.

## Install / update

```bash
cd ~/Volumio-Display
sudo ./install/install.sh
```

This:
- copies the unit to `/etc/systemd/system/volumio-display.service`
- enables it (`systemctl enable`) so it starts at boot
- starts it now (`systemctl start`)

## Run-time controls

```bash
sudo systemctl status volumio-display
sudo systemctl restart volumio-display
sudo systemctl stop volumio-display
sudo journalctl -u volumio-display -f      # systemd's journal (also captures stderr)
tail -f /tmp/vfd.log                       # the project's own rotating log
```

## How it stays alive

Three layers of resilience, in order of who reacts first:

1. **In-process self-healing.** `main.py` wraps `sio.wait()` in a loop that
   reconnects on unexpected return; up to 5 consecutive reconnect failures
   before the process exits non-zero. Render thread crashes are caught and
   force-exit the process via `os._exit(3)`.

2. **In-process watchdog thread.** Checks every 15s that the render thread
   is still ticking; if the gap exceeds 60s, calls `os._exit(4)`. Also pets
   the systemd watchdog (`WATCHDOG=1`) so systemd's own watchdog doesn't fire
   while we're healthy.

3. **Systemd.** `Restart=always` brings us back after any exit (clean, crashed,
   watchdog-killed). `WatchdogSec=60` plus `Type=notify` means if the in-process
   watchdog itself wedges, systemd kills the process after 60s of silence.
   `StartLimitBurst=10` in `StartLimitIntervalSec=600` caps restart storms so
   a genuinely broken install doesn't peg the CPU.

## Why systemd and not a Volumio plugin?

Volumio does have a plugin system (`/data/plugins/`, with `index.js` + `package.json`
+ `install.sh` + `UIConfig.json` per plugin). That's the proper way to ship
something distributable to other Volumio users — it gets a settings page in
the Volumio web UI, hooks for install/uninstall, etc.

For a private, single-device install, a systemd service is much simpler and
gives the exact same autostart behavior. To turn this into a real Volumio
plugin later, you'd:

- Wrap the project as a node module under `/data/plugins/system_hardware/oled_display/`
  (or `accessory/`, depending on the plugin category)
- Add `index.js` that wraps starting/stopping the python process
- Add `package.json` with the manifest
- Add `UIConfig.json` to expose tunables (volume.max, timing.*, etc.) in the
  Volumio web UI
- Add `install.sh` to set up python deps
- Use `volumio-plugins-sources` repo to package + publish

That's a substantial bit of work and only worth it if you plan to share the
plugin. For now the systemd service does the job.

## Other autostart mechanisms (not used here)

- `/etc/rc.local` — works but no restart-on-failure, no journald integration,
  no dependency ordering. Avoid.
- `crontab @reboot` — same drawbacks as rc.local.
- Volumio's `startup-script` plugin — basically a UI wrapper around
  `rc.local`; no advantage over our systemd unit.

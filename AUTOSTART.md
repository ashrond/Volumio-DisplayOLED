# Autostart on boot — how main.py gets launched after Volumio is up

> **Status (2026-06-05):** the canonical deploy is now the Volumio plugin under
> `plugin/`, installed via `volumio plugin install` — see `plugin/README.md`.
> That installer writes a `synthwave-display.service` systemd unit using the
> same self-healing design described below; only the path and unit name
> differ. This document remains for the **legacy direct-systemd install**
> (`install/install.sh`, `install/volumio-display.service`) used during early
> development, and as a reference for the self-healing design.

This project uses a **systemd service unit** to start automatically on boot,
ordered after `volumio.service`. In the dev install the unit lives at
`install/volumio-display.service` and is installed by `install/install.sh`.
In the plugin install the unit is rendered by `plugin/install.sh` at install
time and named `synthwave-display.service`.

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

## Plugin vs. direct systemd install

The plugin under `plugin/` is the canonical install path now (see
`plugin/README.md`). It still uses systemd under the hood — the plugin's
`install.sh` renders `synthwave-display.service` at install time, drops a
scoped sudoers fragment, primes `/etc/lirc/lircrc`, then enables + starts
the unit. The autostart, watchdog, and resilience properties documented
above are identical between the two installs; the plugin install adds a
WebUI settings page, theme upload/download, and Volumio's standard
install/enable/disable lifecycle.

The legacy direct install (`install/install.sh`) is kept around for
dev iterations against `~/Volumio-Display/`. Don't run both at once on
the same Pi — the unit names differ (`volumio-display.service` vs
`synthwave-display.service`) so they don't collide, but they'd race for
SPI access.

## Other autostart mechanisms (not used here)

- `/etc/rc.local` — works but no restart-on-failure, no journald integration,
  no dependency ordering. Avoid.
- `crontab @reboot` — same drawbacks as rc.local.
- Volumio's `startup-script` plugin — basically a UI wrapper around
  `rc.local`; no advantage over our systemd unit.

#!/bin/bash
# uninstall.sh — Volumio calls this before removing the plugin folder.
#
# Stops + disables the systemd service, removes the unit file and sudoers
# fragment. The plugin folder (and therefore the display Python code,
# themes, and config files) gets removed by Volumio after this returns —
# we don't have to clean that up ourselves.

set -uo pipefail   # NOT -e: every step is best-effort. We never want to
                   # block plugin removal because of a cleanup failure.

SERVICE_NAME="synthwave-display.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
SUDOERS_FILE="/etc/sudoers.d/synthwave-display"

echo ":: Synthwave Display plugin uninstall"

sudo systemctl stop "$SERVICE_NAME"     || true
sudo systemctl disable "$SERVICE_NAME"  || true
sudo rm -f "$SERVICE_DST"
sudo rm -f "$SUDOERS_FILE"
sudo systemctl daemon-reload            || true

echo ":: uninstall complete"
exit 0

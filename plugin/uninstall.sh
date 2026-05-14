#!/bin/bash
# uninstall.sh — Volumio calls this before removing the plugin folder.
#
# Stops + disables the systemd service, removes the unit file and sudoers
# fragment. The plugin folder (and therefore the display Python code,
# themes, and config files) gets removed by Volumio after this returns —
# we don't have to clean that up ourselves.

# Volumio invokes plugin uninstall scripts via /bin/sh (dash), ignoring the
# shebang. Re-exec under bash so we can use pipefail, etc.
[ -z "${BASH_VERSION:-}" ] && exec /bin/bash "$0" "$@"

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

# Restore the pre-plugin lircrc if we have a backup; otherwise leave whatever
# is there. Then bounce irexec so the original IR routing takes effect.
if [[ -f /etc/lirc/lircrc.preplugin ]]; then
    sudo mv /etc/lirc/lircrc.preplugin /etc/lirc/lircrc || true
    sudo systemctl restart irexec.service               || true
fi

echo ":: uninstall complete"
exit 0

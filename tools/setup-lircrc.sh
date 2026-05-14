#!/bin/bash
# setup-lircrc.sh — install our lircrc and restart irexec.
#
# Volumio's ir_controller plugin re-writes /etc/lirc/lircrc from its active
# profile on every Volumio start, clobbering our routing. This script puts
# our routing back; the plugin's onStart() spawns it via NOPASSWD sudo so
# the rewrite happens after ir_controller has already done its thing.
#
# Idempotent.

set -eu

PLUGIN_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="$(cd "$(dirname "$0")" && pwd)/lircrc.template"
LIRCRC="/etc/lirc/lircrc"

if [[ ! -f "$TEMPLATE" ]]; then
    echo "!! missing lircrc.template at $TEMPLATE" >&2
    exit 1
fi

# Preserve the original (volumio-direct) routing the first time, so uninstall
# can restore it. -n means don't clobber an existing backup.
cp -n "$LIRCRC" "$LIRCRC.preplugin" 2>/dev/null || true

cp "$TEMPLATE" "$LIRCRC"
chmod 666 "$LIRCRC"

systemctl restart irexec.service

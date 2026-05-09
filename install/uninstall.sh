#!/bin/bash
# Uninstall the Volumio-Display systemd service.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo "This script needs sudo. Run: sudo ./install/uninstall.sh"
    exit 1
fi
systemctl stop volumio-display.service 2>/dev/null || true
systemctl disable volumio-display.service 2>/dev/null || true
rm -f /etc/systemd/system/volumio-display.service
systemctl daemon-reload
echo "Removed."

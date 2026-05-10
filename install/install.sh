#!/bin/bash
# Install / update the Volumio-Display systemd service.
# Run from the project root: sudo ./install/install.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "This script needs sudo. Run: sudo ./install/install.sh"
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_SRC="$PROJECT_DIR/install/volumio-display.service"
SERVICE_DST="/etc/systemd/system/volumio-display.service"
REQUIREMENTS="$PROJECT_DIR/install/requirements.txt"

if [[ ! -f "$SERVICE_SRC" ]]; then
    echo "Missing $SERVICE_SRC"
    exit 1
fi

# Install Python deps for the volumio user (the service runs as volumio).
if [[ -f "$REQUIREMENTS" ]]; then
    echo ":: installing python deps from $REQUIREMENTS (as volumio)"
    sudo -u volumio pip3 install --user --upgrade -r "$REQUIREMENTS"
fi

# Stop the running version (if any) before swapping.
if systemctl is-active --quiet volumio-display.service; then
    echo ":: stopping existing volumio-display.service"
    systemctl stop volumio-display.service
fi

# Also kill any manually-launched python main.py so they don't fight for SPI.
pkill -TERM -f "python3.*main\.py$" 2>/dev/null || true
sleep 1

echo ":: installing $SERVICE_DST"
install -m 644 "$SERVICE_SRC" "$SERVICE_DST"

echo ":: systemctl daemon-reload"
systemctl daemon-reload

echo ":: enabling on boot"
systemctl enable volumio-display.service

echo ":: starting"
systemctl start volumio-display.service

sleep 2
systemctl --no-pager status volumio-display.service || true

echo
echo "Done. Useful commands:"
echo "  sudo systemctl status volumio-display"
echo "  sudo systemctl restart volumio-display"
echo "  sudo systemctl stop volumio-display"
echo "  sudo journalctl -u volumio-display -f"
echo "  tail -f /tmp/vfd.log"

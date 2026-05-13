#!/bin/bash
# install.sh — runs once when Volumio installs the Synthwave Display plugin.
#
# When this script runs the plugin folder already lives at the final path —
# typically /data/plugins/user_interface/synthwave_display/. We:
#   1. Install Python deps (pip3 --user, as the volumio user)
#   2. Render the systemd unit with the right paths and install it
#   3. Grant passwordless sudo for `systemctl <action> synthwave-display.service`
#      so tools/admin.py can restart from within the plugin
#   4. Enable + start the service
#
# Idempotent: re-running is safe. Existing runtime.toml is never overwritten.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
DISPLAY_DIR="$PLUGIN_DIR/display"
SERVICE_NAME="synthwave-display.service"
SERVICE_DST="/etc/systemd/system/$SERVICE_NAME"
SUDOERS_FILE="/etc/sudoers.d/synthwave-display"

echo ":: Synthwave Display plugin install — plugin dir: $PLUGIN_DIR"

if [[ ! -d "$DISPLAY_DIR" ]]; then
    echo "!! display/ subfolder missing — plugin layout is broken"
    exit 1
fi

# 1. Python dependencies (as volumio user, --user install)
echo ":: installing Python deps"
sudo -u volumio pip3 install --user --upgrade -r "$DISPLAY_DIR/install/requirements.txt"

# 2. Systemd unit. Rendered with the plugin's actual path baked in so the
# unit survives plugin relocation if Volumio ever changes plugin paths.
echo ":: writing systemd unit to $SERVICE_DST"
cat > /tmp/$SERVICE_NAME <<EOF
[Unit]
Description=Synthwave Display (Volumio plugin)
After=volumio.service
Wants=volumio.service

[Service]
Type=notify
WatchdogSec=60
NotifyAccess=all
ExecStart=/usr/bin/python3 -u $DISPLAY_DIR/main.py
WorkingDirectory=$DISPLAY_DIR
User=volumio
Group=volumio
Restart=always
RestartSec=5
StartLimitBurst=10
StartLimitIntervalSec=600

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 644 /tmp/$SERVICE_NAME "$SERVICE_DST"
rm /tmp/$SERVICE_NAME

# 3. Passwordless sudo for the specific service-control commands we need.
# Scoped to the exact unit so this isn't a general escalation.
echo ":: writing sudoers fragment to $SUDOERS_FILE"
cat > /tmp/sudoers-synthwave <<EOF
volumio ALL=(ALL) NOPASSWD: /bin/systemctl start $SERVICE_NAME
volumio ALL=(ALL) NOPASSWD: /bin/systemctl stop $SERVICE_NAME
volumio ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME
volumio ALL=(ALL) NOPASSWD: /bin/systemctl is-active $SERVICE_NAME
volumio ALL=(ALL) NOPASSWD: /usr/bin/systemctl start $SERVICE_NAME
volumio ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop $SERVICE_NAME
volumio ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart $SERVICE_NAME
volumio ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active $SERVICE_NAME
EOF
sudo install -m 440 /tmp/sudoers-synthwave "$SUDOERS_FILE"
rm /tmp/sudoers-synthwave

# 4. Daemon reload + enable + start
echo ":: systemctl daemon-reload"
sudo systemctl daemon-reload

echo ":: systemctl enable"
sudo systemctl enable "$SERVICE_NAME"

echo ":: systemctl start (or restart if already running)"
sudo systemctl restart "$SERVICE_NAME" || true

echo ":: install complete"
exit 0

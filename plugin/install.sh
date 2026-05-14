#!/bin/bash
# install.sh — runs once when Volumio installs the Synthwave Display plugin.
#
# When this script runs the plugin folder already lives at the final path —
# typically /data/plugins/user_interface/synthwave_display/. We:
#   1. Install Node deps (npm install — Volumio's pipeline does NOT do this)
#   2. Install Python deps (pip3 --user, as the volumio user)
#   3. Render the systemd unit with the right paths and install it
#   4. Grant passwordless sudo for `systemctl <action> synthwave-display.service`
#      so tools/admin.py can restart from within the plugin
#   5. Install /etc/lirc/lircrc routing IR buttons to ir_dispatch.sh
#   6. Enable + start the service
#
# Idempotent: re-running is safe. Existing runtime.toml is never overwritten.

# Volumio invokes plugin install scripts via /bin/sh (dash), ignoring the
# shebang. Re-exec under bash so we can use pipefail, [[ ]], etc.
[ -z "${BASH_VERSION:-}" ] && exec /bin/bash "$0" "$@"

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

# 1. Node deps. Volumio's plugin install pipeline does NOT run npm install
# itself — it expects the zip to ship with node_modules/ pre-populated, or
# the plugin's own install.sh to handle it. We do the latter.
echo ":: installing Node deps (npm install --production)"
cd "$PLUGIN_DIR"
sudo -u volumio npm install --production --no-package-lock --no-audit

# 2. Python dependencies (as volumio user, --user install)
echo ":: installing Python deps"
sudo -u volumio pip3 install --user --upgrade -r "$DISPLAY_DIR/install/requirements.txt"

# 3. Systemd unit. Rendered with the plugin's actual path baked in so the
# unit survives plugin relocation if Volumio ever changes plugin paths.
echo ":: writing systemd unit to $SERVICE_DST"
cat > /tmp/$SERVICE_NAME <<EOF
[Unit]
Description=Synthwave Display (Volumio plugin)
After=volumio.service
Wants=volumio.service
StartLimitBurst=10
StartLimitIntervalSec=600

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

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 644 /tmp/$SERVICE_NAME "$SERVICE_DST"
rm /tmp/$SERVICE_NAME

# 4. Passwordless sudo for the specific commands we need.
# Scoped narrowly so this isn't a general escalation:
#  - service control for our unit (so the WebUI restart button + admin.py work)
#  - setup-lircrc.sh (so onStart can rewrite lircrc after ir_controller does)
#  - irexec restart (called by setup-lircrc.sh itself, as root)
SETUP_LIRCRC="$DISPLAY_DIR/tools/setup-lircrc.sh"
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
volumio ALL=(ALL) NOPASSWD: $SETUP_LIRCRC
EOF
sudo install -m 440 /tmp/sudoers-synthwave "$SUDOERS_FILE"
rm /tmp/sudoers-synthwave

# 5. IR routing. The ir_controller plugin owns IR-code → KEY_NAME decoding via
# /etc/lirc/lircd.conf, but the action side (/etc/lirc/lircrc) needs to point at
# our ir_dispatch.sh so the display program receives buttons over UDP and can
# decide whether to consume them (menu nav, skip animation feedback) or forward
# to Volumio. Without this, every key goes straight to `volumio <cmd>` and the
# display is bypassed — no menu, no skip animations.
#
# ir_controller re-writes lircrc from its active profile on every Volumio
# start, so we don't fight it here — instead, the plugin's onStart() (in
# index.js) calls setup-lircrc.sh on every plugin start. We just need to
# install the script (already in place), make ir_dispatch.sh executable,
# and seed lircrc once for the current session.
echo ":: priming IR routing via setup-lircrc.sh"
sudo chmod +x "$DISPLAY_DIR/tools/ir_dispatch.sh" "$SETUP_LIRCRC"
sudo "$SETUP_LIRCRC"

# 6. Daemon reload + enable + start
echo ":: systemctl daemon-reload"
sudo systemctl daemon-reload

echo ":: systemctl enable"
sudo systemctl enable "$SERVICE_NAME"

echo ":: systemctl start (or restart if already running)"
sudo systemctl restart "$SERVICE_NAME" || true

echo ":: install complete"
exit 0

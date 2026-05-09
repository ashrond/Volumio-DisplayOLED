#!/bin/bash
# Triggered by lircrc on every IR button press. Forwards the button name
# to the display program over a localhost UDP datagram.
#
# Bash's /dev/udp/host/port redirection has no external deps and ~zero overhead.
# The display owns the routing logic: when the menu is open it consumes the
# button as menu nav; otherwise it forwards to Volumio.
#
# Falls back silently if the display isn't listening (e.g. service down).
echo -n "${1:-unknown}" > /dev/udp/127.0.0.1/9876 2>/dev/null || true

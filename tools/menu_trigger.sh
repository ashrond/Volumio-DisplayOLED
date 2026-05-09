#!/bin/bash
# Triggered by lircrc when the MENU button is pressed.
# Sends SIGUSR1 to the running display program so it can toggle/enter the menu screen.
pkill -USR1 -f "python3 -u main\.py$" 2>/dev/null || true

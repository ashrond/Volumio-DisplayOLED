#!/usr/bin/env python3
"""Preview a GIF on the OLED. Usage: python3 tools/preview_gif.py <name.gif> [seconds]
Run with main.py stopped (pkill -f 'python3 -u main.py')."""
import sys
import os
import time
from PIL import Image, ImageSequence
from luma.core.interface.serial import spi
from luma.oled.device import ssd1322
from luma.core.render import canvas

if len(sys.argv) < 2:
    print("usage: preview_gif.py <name.gif> [seconds]")
    sys.exit(1)

name = sys.argv[1]
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

asset_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
path = os.path.join(asset_dir, name)
if not os.path.exists(path):
    print(f"not found: {path}")
    sys.exit(2)

device = ssd1322(spi(device=0, port=0, bus_speed_hz=8000000))
gif = Image.open(path)
frames = [f.convert("1").resize((device.width, device.height)) for f in ImageSequence.Iterator(gif)]
print(f"playing {name} ({len(frames)} frames) for {seconds}s — Ctrl+C to stop")

end = time.time() + seconds
i = 0
while time.time() < end:
    with canvas(device) as draw:
        draw.bitmap((0, 0), frames[i % len(frames)], fill="white")
    time.sleep(0.1)
    i += 1

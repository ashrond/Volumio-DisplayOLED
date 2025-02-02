from luma.core.render import canvas
from PIL import Image, ImageSequence
import time
import os
from config.config import text_color, GIF_PATH_STARTUP  

def display_startup(device):
    """Displays the startup GIF once and holds on the last frame for 15 seconds."""
    if not os.path.exists(GIF_PATH_STARTUP):
        print(f"?? Startup GIF not found: {GIF_PATH_STARTUP}")
        return False  # Return failure

    gif = Image.open(GIF_PATH_STARTUP)
    frames = [frame.convert("1").resize((device.width, device.height)) for frame in ImageSequence.Iterator(gif)]

    for frame in frames:
        with canvas(device) as draw:
            draw.bitmap((0, 0), frame, fill="white")
        time.sleep(0.1)  # Adjust GIF speed

    # Hold last frame
    with canvas(device) as draw:
        draw.bitmap((0, 0), frames[-1], fill="white")

    print("? Startup screen displayed successfully.")
    time.sleep(15)  # Ensure screen remains visible

    print("? Startup screen finished.")
    return True  # Signal completion

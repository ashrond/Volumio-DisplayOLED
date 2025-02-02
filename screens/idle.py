from luma.core.render import canvas
from PIL import Image, ImageSequence
import time
import os
from config.config import text_color

# Define the path to the idle GIF
GIF_PATH_IDLE = os.path.join(os.path.dirname(__file__), "../assets/idle.gif")

def display_idle_screen(device):
    """
    Displays the idle screen with an animated GIF as background.
    """
    if not os.path.exists(GIF_PATH_IDLE):
        print(f"?? Idle GIF not found: {GIF_PATH_IDLE}")
        return

    gif = Image.open(GIF_PATH_IDLE)
    frames = [frame.convert("1") for frame in ImageSequence.Iterator(gif)]

    for frame in frames:
        with canvas(device) as draw:
            draw.bitmap((0, 0), frame, fill="white")
        time.sleep(0.1)  # Adjust GIF speed

    # Hold on the last frame
    with canvas(device) as draw:
        draw.bitmap((0, 0), frames[-1], fill="white")

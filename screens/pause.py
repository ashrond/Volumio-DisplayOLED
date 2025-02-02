from luma.core.render import canvas
from PIL import Image, ImageSequence
import time
import os
from config.config import text_color

# Define the path to the pause GIF
GIF_PATH_PAUSE = os.path.join(os.path.dirname(__file__), "../assets/pause.gif")

def display_pause_screen(device):
    """
    Displays a pause animation that stops on the last frame.
    """
    if not os.path.exists(GIF_PATH_PAUSE):
        print(f"?? Pause GIF not found: {GIF_PATH_PAUSE}")
        return

    gif = Image.open(GIF_PATH_PAUSE)
    frames = [frame.convert("1") for frame in ImageSequence.Iterator(gif)]

    for frame in frames:
        with canvas(device) as draw:
            img_width, img_height = frame.size
            x_pos = (device.width - img_width) // 2
            y_pos = (device.height - img_height) // 2
            draw.bitmap((x_pos, y_pos), frame, fill="white")
        time.sleep(0.1)  # Adjust animation speed if necessary

    # Hold the last frame
    with canvas(device) as draw:
        draw.bitmap((x_pos, y_pos), frames[-1], fill="white")

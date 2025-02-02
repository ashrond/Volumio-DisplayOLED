from luma.core.render import canvas
from PIL import Image, ImageSequence
import time
import os
from config.config import text_color, GIF_PATH_LOADING, font_title, font_artist  # Import fonts

def display_loading_screen(device, error_message=""):
    """Displays the loading GIF in a loop with a centered 'LOADING' message and optional error messages."""
    if not os.path.exists(GIF_PATH_LOADING):
        print(f"?? Loading GIF not found: {GIF_PATH_LOADING}")
        return

    gif = Image.open(GIF_PATH_LOADING)
    frames = [frame.convert("1").resize((device.width, device.height)) for frame in ImageSequence.Iterator(gif)]
    
    frame_index = 0
    while True:
        with canvas(device) as draw:
            # Draw GIF Frame
            draw.bitmap((0, 0), frames[frame_index], fill="white")

            # Display "LOADING" at the top center
            text = "LOADING"
            text_width = font_title.getbbox(text)[2]
            text_x = (device.width - text_width) // 2
            draw.text((text_x, 0), text, font=font_title, fill=text_color)

            # Display error message at the bottom (if any)
            if error_message:
                error_width = font_artist.getbbox(error_message)[2]
                error_x = (device.width - error_width) // 2
                draw.text((error_x, device.height - 12), error_message, font=font_artist, fill=text_color)

        time.sleep(0.1)  # Adjust GIF speed
        frame_index = (frame_index + 1) % len(frames)  # Loop GIF properly

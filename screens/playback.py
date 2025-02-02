from luma.core.render import canvas
from PIL import Image, ImageSequence
import time
import os
from config.config import (
    font_time, font_title, font_artist, text_color, background_color, progress_bar_width
)
from utils.display import scroll_text, animate_gif
from utils.volumio_api import get_volumio_state as fetch_playback_data

# Load the playback GIF
GIF_PATH = os.path.join(os.path.dirname(__file__), "../assets/playback.gif")

def display_playback_screen(device):
    """
    Displays the playback screen with scrolling title, progress bar, and artist.
    The GIF animation is used as a background.
    """

    data = fetch_playback_data()
    if not data:
        return

    title = data.get("title", "Unknown")
    artist = data.get("artist", "Unknown")
    seek = data.get("seek", 0)
    duration = data.get("duration", 1)
    progress_percent = min(100, int((seek / duration) * 100)) if duration > 0 else 0
    formatted_artist = f"-{artist}-"

    def draw_static_info(draw):
        """Draws time, progress bar, and artist over the GIF background."""
        screen_width = device.width

        # Calculate text widths for centering
        time_width = font_time.getbbox("12:00 AM")[2]  # Placeholder for centering
        artist_width = font_artist.getbbox(formatted_artist)[2]
        title_width = font_title.getbbox(title)[2]

        # Draw elements
        draw.text(((screen_width - time_width) // 2, 0), time.strftime("%I:%M %p"), font=font_time, fill=text_color)
        
        # Scrollable Title
        title_x = (screen_width - title_width) // 2
        draw.text((title_x, 22), title, font=font_title, fill=text_color)

        # Progress Bar
        bar_x = (screen_width - progress_bar_width) // 2
        draw.rectangle((bar_x, 42, bar_x + progress_bar_width, 45), outline=text_color, fill=background_color)
        draw.rectangle((bar_x, 42, bar_x + (progress_bar_width * progress_percent // 100), 45), outline=text_color, fill=text_color)

        # Artist text
        draw.text(((screen_width - artist_width) // 2, 50), formatted_artist, font=font_artist, fill=text_color)

    # Animate GIF with overlay elements
    animate_gif(device, draw_static_info)

    # Ensure Title Scrolling Works Correctly
    if font_title.getbbox(title)[2] > device.width:
        scroll_text(device, title, speed=2)  # Adjust the speed as needed


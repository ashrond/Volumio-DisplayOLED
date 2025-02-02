from luma.core.render import canvas
from PIL import Image, ImageSequence
from config.config import font_title, text_color, GIF_PATH
import time
import os

def animate_gif(device, overlay_callback):
    """Animates a GIF as a background and overlays custom elements."""
    if not os.path.exists(GIF_PATH):
        print(f"?? Playback GIF not found: {GIF_PATH}")
        return

    gif = Image.open(GIF_PATH)
    frames = [frame.convert("1").resize((device.width, device.height)) for frame in ImageSequence.Iterator(gif)]
    
    for frame in frames:
        with canvas(device) as draw:
            draw.bitmap((0, 0), frame, fill="white")  # Draw GIF frame
            overlay_callback(draw)  # Draw overlay elements (title, progress, artist)
        time.sleep(0.1)  # Adjust frame rate

def scroll_text(device, title, speed=2):
    """Scrolls the track title smoothly if it's too long for the display."""
    title_width = font_title.getbbox(title)[2]
    screen_width = device.width

    if title_width <= screen_width:
        print("? Title fits, displaying statically.")
        with canvas(device) as draw:
            draw_static_info(draw, title, "", 0, title_offset=0)
        return  # Skip scrolling if the title fits

    scroll_range = title_width + screen_width

    for offset in range(scroll_range):
        with canvas(device) as draw:
            draw_static_info(draw, title, "", 0, title_offset=offset)
        time.sleep(0.01 / speed)  # Smooth scrolling speed

def draw_static_info(draw, title, artist, progress_percent, title_offset=0):
    """Draws overlay text, progress bar, and artist over the GIF background."""
    screen_width = draw.im.width  # Get width from image

    formatted_artist = f"-{artist}-" if artist else "-Unknown Artist-"
    
    # Calculate text widths for centering
    time_width = font_title.getbbox(get_current_time())[2]
    title_width = font_title.getbbox(title)[2]
    artist_width = font_title.getbbox(formatted_artist)[2]

    # Draw elements centered
    draw.text(((screen_width - time_width) // 2, 0), get_current_time(), font=font_title, fill=text_color)

    # **Scroll the title text smoothly**
    title_x = (screen_width - title_width) // 2 - title_offset
    draw.text((title_x, 22), title, font=font_title, fill=text_color)

    # Draw the artist text
    draw.text(((screen_width - artist_width) // 2, 50), formatted_artist, font=font_title, fill=text_color)

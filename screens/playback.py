import time
from luma.core.render import canvas
from config.config import (
    font_time, font_title, font_artist, text_color, background_color, progress_bar_width
)


def draw_static_info(draw, device, title, formatted_artist, progress_percent):
    """Draws time, progress bar, and artist for one playback frame."""
    screen_width = device.width
    current_time = time.strftime("%I:%M %p")

    time_width = font_time.getbbox(current_time)[2]
    artist_width = font_artist.getbbox(formatted_artist)[2]
    title_width = font_title.getbbox(title)[2]

    draw.text(((screen_width - time_width) // 2, 0), current_time, font=font_time, fill=text_color)

    title_x = (screen_width - title_width) // 2
    draw.text((title_x, 22), title, font=font_title, fill=text_color)

    bar_x = (screen_width - progress_bar_width) // 2
    bar_length = max(1, int(progress_bar_width * progress_percent / 100))
    draw.rectangle((bar_x, 42, bar_x + progress_bar_width, 45), outline=text_color, fill=background_color)
    draw.rectangle((bar_x, 42, bar_x + bar_length, 45), outline=text_color, fill=text_color)

    draw.text(((screen_width - artist_width) // 2, 50), formatted_artist, font=font_artist, fill=text_color)


def display_playback_screen(device, title, artist, seek, duration):
    """Paints ONE frame of the playback screen. Must return quickly — called from render loop hot path."""
    seek_seconds = seek / 1000
    progress_percent = min(100, int((seek_seconds / duration) * 100)) if duration > 0 else 0
    formatted_artist = f"-{artist}-" if artist else "-Unknown Artist-"
    with canvas(device) as draw:
        draw_static_info(draw, device, title, formatted_artist, progress_percent)

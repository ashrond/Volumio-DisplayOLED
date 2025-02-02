import requests
import time
from luma.core.render import canvas
from config.config import font_volume, text_color

VOLUMIO_API_URL = "http://localhost:3000/api/v1/getState"

def fetch_volume():
    """Fetch the latest volume level directly from Volumio API."""
    try:
        response = requests.get(VOLUMIO_API_URL, timeout=0.5)
        if response.status_code == 200:
            return response.json().get("volume", None)
    except requests.exceptions.RequestException:
        return None
    return None

def display_volume_screen(device):
    """Displays the volume screen with the current volume percentage and signals when done."""
    volume = fetch_volume()

    if volume is None:
        print("?? Failed to fetch volume from API")
        return False  # Indicate failure (this prevents the playback screen from updating incorrectly)

    with canvas(device) as draw:
        # Clear screen with a black rectangle
        draw.rectangle((0, 0, device.width, device.height), fill="black")
        
        # Format the volume text
        volume_text = f"Volume: {volume}%"
        text_width = font_volume.getbbox(volume_text)[2]
        text_x = (device.width - text_width) // 2
        text_y = (device.height - font_volume.size) // 2
        
        # Draw the text centered on the screen
        draw.text((text_x, text_y), volume_text, font=font_volume, fill=text_color)

    print(f"?? Volume updated via API: {volume}%")

    time.sleep(2)  # Keep volume screen active for 2 seconds

    return True  # Signal to main.py that volume screen is done and playback should resume

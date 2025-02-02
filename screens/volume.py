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
    """Displays the volume screen and manages its own timeout logic."""
    last_volume = fetch_volume()
    if last_volume is None:
        print("?? Failed to fetch volume from API")
        return False  # Prevents playback screen from updating incorrectly

    last_input_time = time.time()
    while True:
        volume = fetch_volume()
        if volume is None:
            continue  # Skip iteration if API fails temporarily

        if volume != last_volume:
            last_volume = volume
            last_input_time = time.time()  # Reset input timeout

            with canvas(device) as draw:
                draw.rectangle((0, 0, device.width, device.height), fill="black")
                volume_text = f"Volume: {volume}%"
                text_width = font_volume.getbbox(volume_text)[2]
                text_x = (device.width - text_width) // 2
                text_y = (device.height - font_volume.size) // 2
                draw.text((text_x, text_y), volume_text, font=font_volume, fill=text_color)

            print(f"?? Volume updated via API: {volume}%")

        # If no new input for 1.5s, begin exit countdown
        if time.time() - last_input_time > 1.5:
            print("?? Waiting 4s before exiting volume screen...")
            time.sleep(4)
            return True  # Signal main.py to return to playback screen

        time.sleep(0.2)  # Poll every 200ms

import toml
import os
from PIL import ImageFont

# Load theme settings
config = toml.load("config/theme.toml")

# Font settings
font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
font_time = ImageFont.truetype(font_path, config["fonts"]["time"])
font_title = ImageFont.truetype(font_path, config["fonts"]["title"])
font_artist = ImageFont.truetype(font_path, config["fonts"]["artist"])
font_volume = ImageFont.truetype(font_path, config["fonts"]["volume"])

# Layout settings
progress_bar_height = config["layout"]["progress_bar_height"]
progress_bar_width = config["layout"]["progress_bar_width"]

# Colors
text_color = config["colors"]["text_color"]
background_color = config["colors"]["background_color"]

# Define the path to the GIFs
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))  
GIF_PATH = os.path.join(BASE_DIR, "assets", "playback.gif")  
GIF_PATH_STARTUP = os.path.join(BASE_DIR, "assets", "startup.gif")
GIF_PATH_LOADING = os.path.join(BASE_DIR, "assets", "loading.gif")  


# Ensure paths exist
if not os.path.exists(GIF_PATH):
    print(f"Warning: Playback GIF not found at {GIF_PATH}")
if not os.path.exists(GIF_PATH_STARTUP):
    print(f"Warning: Startup GIF not found at {GIF_PATH_STARTUP}")

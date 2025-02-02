import time
import threading
import socketio
import json
from luma.core.interface.serial import spi
from luma.oled.device import ssd1322
from screens.startup import display_startup
from screens.playback import display_playback_screen
from screens.pause import display_pause_screen
from screens.idle import display_idle_screen
from screens.volume import display_volume_screen
from screens.loading import display_loading_screen

VOLUMIO_WS_URL = "http://localhost:3000"

# Initialize SPI connection
serial = spi(device=0, port=0, bus_speed_hz=8000000)
device = ssd1322(serial)

# Global state
current_screen = "startup"
last_volume = None  # Track last volume level
volume_timer = None  # Timer for volume screen transition
sio = socketio.Client()
screen_lock = threading.Lock()  # Prevents screen overwrites

def safe_display(func, *args):
    """Ensures only one screen update runs at a time."""
    with screen_lock:
        func(*args)

@sio.event
def connect():
    print("?? WebSocket Connected to Volumio")
    sio.emit("subscribe", {})
    sio.emit("getState", {})

@sio.event
def disconnect():
    print("?? WebSocket Disconnected. Attempting to reconnect...")
    safe_display(display_loading_screen, device, "Reconnecting...")
    time.sleep(5)
    try:
        sio.connect(VOLUMIO_WS_URL)
    except Exception as e:
        print(f"?? WebSocket Reconnection Failed: {e}")

@sio.on("pushState")
def on_message(data):
    global current_screen, last_volume, volume_timer

    print(f"?? WebSocket Raw Data:\n{json.dumps(data, indent=2)}")

    state = data.get("status", "")
    title = data.get("title", "Unknown")
    artist = data.get("artist", "Unknown")
    volume = data.get("volume", None)

    print(f"?? Extracted - Title: {title}, Artist: {artist}, Volume: {volume}")

    if current_screen == "startup":
        return  # Ensure startup completes before handling updates

    # Handle volume changes - Instant update, no delay
    if volume is not None and volume != last_volume:
        last_volume = volume
        success = display_volume_screen(device)
        print(f"?? Volume screen displayed: {volume}%")

        if success:
            print("?? Volume screen complete. Returning to playback.")
            safe_display(display_playback_screen, device)
        return  # Prevents playback state updates from interfering

    # Handle playback state changes only when volume isn't changing
    if state == "play":
        safe_display(display_playback_screen, device, title, artist)
    elif state == "pause":
        safe_display(display_pause_screen, device)
    elif state == "stop":
        safe_display(display_idle_screen, device)

    print(f"?? Current screen: {current_screen}")

def startup_screen():
    """Runs the startup animation before switching to playback."""
    global current_screen
    safe_display(display_startup, device)
    print("?? Startup screen finished. Switching to playback.")
    current_screen = "playback"
    safe_display(display_playback_screen, device)

def return_to_playback():
    """Ensures that playback screen is restored after volume adjustments."""
    global current_screen
    print("?? Volume screen complete. Returning to playback.")
    safe_display(display_playback_screen, device)  # No extra arguments
    current_screen = "playback"

if __name__ == "__main__":
    sio.connect(VOLUMIO_WS_URL)

    # Start startup screen synchronously before handling any WebSocket events
    startup_screen()

    # Keep WebSocket active
    sio.wait()

import time
import os
from luma.core.render import canvas
from PIL import Image, ImageSequence
from config.config import GIF_PATH_STARTUP, log


def display_startup(device, shutdown_event=None):
    """Display startup GIF then hold last frame 15s. If shutdown_event is provided,
    sleeps are interruptible and the function returns False on early exit."""
    if not os.path.exists(GIF_PATH_STARTUP):
        log.warning("startup GIF not found: %s", GIF_PATH_STARTUP)
        return False

    def _wait(secs):
        if shutdown_event is not None:
            shutdown_event.wait(timeout=secs)
            return not shutdown_event.is_set()
        time.sleep(secs)
        return True

    with Image.open(GIF_PATH_STARTUP) as gif:
        frames = [f.convert("1").resize((device.width, device.height)) for f in ImageSequence.Iterator(gif)]

    for frame in frames:
        with canvas(device) as draw:
            draw.bitmap((0, 0), frame, fill="white")
        if not _wait(0.1):
            return False

    with canvas(device) as draw:
        draw.bitmap((0, 0), frames[-1], fill="white")

    log.info("startup screen displayed, holding 15s")
    if not _wait(15):
        return False
    return True

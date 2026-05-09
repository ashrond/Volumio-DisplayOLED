import time
import os
from PIL import Image, ImageSequence
from config.config import GIF_PATH_STARTUP, log


def display_startup(device, shutdown_event=None):
    """Display startup GIF then hold last frame 15s. Frames decoded in the
    device's native mode (grayscale-preserving) and resized with LANCZOS."""
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
        frames = []
        for f in ImageSequence.Iterator(gif):
            frame = f.convert(device.mode)
            if frame.size != (device.width, device.height):
                frame = frame.resize((device.width, device.height), Image.LANCZOS)
            frames.append(frame)

    for frame in frames:
        device.display(frame)
        if not _wait(0.1):
            return False

    device.display(frames[-1])

    log.info("startup screen displayed, holding 15s")
    if not _wait(15):
        return False
    return True

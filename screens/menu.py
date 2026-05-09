"""Menu screen — placeholder for a future simple menu.

NOT WIRED INTO main.py YET. To use:
  1. Add a 'menu' branch in main.py's render_loop that calls a paint helper here
     (so SPI writes stay on the render thread).
  2. Add an event source (e.g. a remote button mapped via lircrc, or an HTTP
     endpoint, or a signal) that sets current_screen='menu' and provides input
     handling for navigation.
  3. Don't call render functions from outside the render thread — that breaks
     the single-writer SPI invariant and causes garbled output.

The function below paints one frame; safe for the render thread to call.
"""
from luma.core.render import canvas
from config.config import font_title, text_color


def paint_menu_placeholder(device, message="Menu not implemented yet"):
    """Paint one frame of the menu placeholder. Safe for render thread."""
    tw = font_title.getbbox(message)[2]
    tx = (device.width - tw) // 2
    ty = (device.height - font_title.size) // 2
    with canvas(device) as draw:
        draw.text((tx, ty), message, font=font_title, fill=text_color)

"""Menu screen — placeholder for the in-progress menu system.

Wired to the MENU button on the remote via /etc/lirc/lircrc + tools/menu_trigger.sh
(sends SIGUSR1 to the display process). main.py's signal handler toggles the
'menu' screen state; render loop calls paint_menu() each tick.

Future work: real navigation (probably by repurposing PLAY for select and
VOL+/- for up/down while menu is open), real menu items, transitions out.
"""
from PIL import Image, ImageDraw
from config.config import font_title, font_artist, text_color


def paint_menu(device, items=None, selected_idx=0, title="Menu"):
    """Paint one frame of the menu screen.

    Items can be more than fit on screen — the visible window scrolls so the
    selected item stays in view. Up/down arrows hint when more items exist
    above/below the viewport.
    """
    img = Image.new(device.mode, (device.width, device.height), "black")
    draw = ImageDraw.Draw(img)

    items = items or ["(menu items go here)"]
    n = len(items)

    # Title bar
    tw = font_title.getbbox(title)[2]
    tx = (device.width - tw) // 2
    draw.text((tx, 0), title, font=font_title, fill=text_color)

    # Compute visible window
    line_h = font_artist.size + 2
    start_y = font_title.size + 3
    avail_h = device.height - start_y
    visible_count = max(1, avail_h // line_h)
    # Center selection in viewport when possible
    viewport_start = max(0, min(selected_idx - visible_count // 2, n - visible_count))
    viewport_end = min(viewport_start + visible_count, n)

    # Right edge reserved for scroll arrows
    text_x = 4
    arrow_x = device.width - 8

    for i in range(viewport_start, viewport_end):
        y = start_y + (i - viewport_start) * line_h
        if i == selected_idx:
            # Highlight bar across the full width
            draw.rectangle((0, y - 1, device.width, y + line_h - 1),
                           fill=(60, 60, 60) if device.mode == "RGB" else 60)
            prefix = "> "
        else:
            prefix = "  "
        draw.text((text_x, y), prefix + items[i], font=font_artist, fill=text_color)

    # Scroll indicators
    if viewport_start > 0:
        draw.text((arrow_x, start_y - 2), "^", font=font_artist, fill=text_color)
    if viewport_end < n:
        draw.text((arrow_x, device.height - line_h), "v", font=font_artist, fill=text_color)

    device.display(img)


# Backwards-compat alias for any old callers
paint_menu_placeholder = paint_menu

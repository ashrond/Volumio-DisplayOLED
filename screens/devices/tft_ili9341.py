"""ILI9341 320×240 color TFT driver — stub.

Schema accepts `[display] type = "ili9341"` in runtime.toml, but the driver
itself is not yet implemented. To complete this:

1. Add `luma.lcd` to install/requirements.txt.
2. In `_init_luma_device` below, replace the NotImplementedError with the
   actual ILI9341 init. Reference:
   https://luma-lcd.readthedocs.io/en/latest/api-documentation.html#luma.lcd.device.ili9341
3. Decide on PIL mode — likely "RGB" for color. Update any code that
   assumes greyscale.
4. Author a theme with 320×240 dimensions. The default theme is laid out
   for 256×64 and will look wrong on a TFT.
5. Skip the OLED-only optimizations — leave `is_oled = False` so main.py
   doesn't try to apply scanline alternation, pixel shift, or the contrast
   fade, none of which apply to a TFT.

Keeping this stub in place lets the schema and factory be complete
without committing to half-written ILI9341 support."""

from .base import Device


class ILI9341Device(Device):
    type_name = "ili9341"
    category = "TFT"
    is_oled = False
    is_implemented = False  # stub — flesh out per the docstring above

    def _init_luma_device(self):
        raise NotImplementedError(
            "ILI9341 support is stubbed but not implemented. See the comment "
            "block at the top of screens/devices/tft_ili9341.py for the steps."
        )

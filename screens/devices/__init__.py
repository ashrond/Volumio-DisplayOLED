"""Device abstraction package.

Exposes a `create_device(device_type, **kwargs)` factory that returns a
`Device` instance wrapping the appropriate luma.* driver. The wrapper
provides a uniform API plus capability flags (`is_oled`) so the rest of
the program can stay panel-agnostic.

Add a new screen by:
1. Writing a new module `screens/devices/<kind>_<chip>.py` that subclasses
   `screens.devices.base.Device`.
2. Wiring its type name into the `create_device()` factory below.
3. (Optional) Authoring a theme dimensioned for the new screen.
"""

from .base import Device, create_device

__all__ = ["Device", "create_device"]

"""Abstract Device base class + factory.

A `Device` wraps an underlying luma.* driver and presents a uniform API to
the rest of the program. Subclasses implement panel-specific initialization
(SPI parameters, command sequences, fast-path optimizations) and set
capability flags so the orchestrator can gate OLED-specific behavior like
burn-in mitigations and contrast control.

Why this abstraction:
- main.py and the screen painters should not have to know which panel is
  in use. They paint PIL Images, call `device.display(img)`, and trust the
  wrapper to do the right thing.
- Per-panel optimizations (e.g. the fast greyscale converter for SSD1322)
  live next to the panel they apply to, not scattered through main.py.
- Adding a new panel is a self-contained drop-in: write one subclass, wire
  one factory branch, optionally author a theme.
"""


class Device:
    """Abstract display device. Subclasses should set the class attributes
    below, implement `_init_luma_device`, and may override the methods that
    don't fit the luma default semantics."""

    # Type name as written in runtime.toml [display] type. Subclasses set this.
    type_name = "base"

    # Category groups drivers in the future WebUI screen-selector (two-stage
    # dropdown: pick "OLED" or "TFT" → pick a specific chip from that family).
    # Subclasses override.
    category = "Other"

    # Capability flags. The orchestrator gates OLED-only optimizations
    # (contrast fade, scanline alternation, pixel shift, hide()/show()
    # hardware sleep) on `is_oled`. TFT subclasses leave it False.
    is_oled = False

    # Whether the driver is actually wired up. Stubs (placeholders for
    # future support) set this to False so the WebUI can grey them out
    # in the screen-selector dropdown without hiding them entirely.
    is_implemented = True

    def __init__(self, width, height, spi_bus, spi_cs, spi_speed_hz):
        self.width = int(width)
        self.height = int(height)
        self.spi_bus = int(spi_bus)
        self.spi_cs = int(spi_cs)
        self.spi_speed_hz = int(spi_speed_hz)
        self._luma_device = None
        self._init_luma_device()

    def _init_luma_device(self):
        """Subclass: build self._luma_device. Install any per-panel fast-path
        overrides (e.g. monkey-patch device.display) at the end of this method.
        Called once from __init__."""
        raise NotImplementedError(
            "Device subclasses must implement _init_luma_device()"
        )

    # ── Delegate attributes the rest of the program reads from luma's API.
    # Kept as @property so a future subclass that doesn't wrap luma still has
    # the option to override.

    @property
    def device(self):
        """The underlying luma instance, for code that needs framebuffer
        access (e.g. the fast display override's redraw() call)."""
        return self._luma_device

    @property
    def mode(self):
        return self._luma_device.mode

    @property
    def size(self):
        return self._luma_device.size

    # ── Uniform output API. All methods are safe to call regardless of
    # capabilities — they no-op (or use a fallback) when the underlying
    # panel doesn't support the operation.

    def display(self, image):
        """Push a PIL Image. Panel-specific optimizations live inside the
        subclass's `_init_luma_device` (it may monkey-patch the luma device's
        display method to install a fast-path)."""
        self._luma_device.display(image)

    def hide(self):
        """Power down / blank the panel. No-op if the panel doesn't support it."""
        hide_fn = getattr(self._luma_device, "hide", None)
        if hide_fn is not None:
            hide_fn()

    def show(self):
        """Bring the panel back online after hide(). No-op if unsupported."""
        show_fn = getattr(self._luma_device, "show", None)
        if show_fn is not None:
            show_fn()

    def contrast(self, level):
        """Set brightness/contrast register. Range 0-255. No-op for panels
        that don't expose contrast control (most TFTs)."""
        contrast_fn = getattr(self._luma_device, "contrast", None)
        if contrast_fn is not None:
            contrast_fn(int(max(0, min(255, level))))


def create_device(device_type, width, height, spi_bus, spi_cs, spi_speed_hz):
    """Factory: return the right Device subclass for the configured panel.
    Reads no globals — all parameters explicit so tests can construct
    devices freely."""
    device_type = str(device_type).lower()
    if device_type == "ssd1322":
        from .oled_ssd1322 import SSD1322Device
        return SSD1322Device(width, height, spi_bus, spi_cs, spi_speed_hz)
    if device_type == "ili9341":
        from .tft_ili9341 import ILI9341Device
        return ILI9341Device(width, height, spi_bus, spi_cs, spi_speed_hz)
    raise RuntimeError(
        "Unsupported display.type=%r. Edit runtime.toml [display] type, or add a driver "
        "module in screens/devices/ and wire it into create_device()." % device_type
    )

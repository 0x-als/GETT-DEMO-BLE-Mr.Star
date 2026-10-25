import colorsys
from dataclasses import dataclass

from config import Config


class ColorUtils:
    @staticmethod
    def hex_to_rgb(value):
        value = value.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def rgb_to_hex(rgb):
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)

    @classmethod
    def mix(cls, first, second, t):
        a, b = cls.hex_to_rgb(first), cls.hex_to_rgb(second)
        return cls.rgb_to_hex(tuple(x + (y - x) * t for x, y in zip(a, b)))

    @classmethod
    def hsv_hex(cls, hue, sat, val):
        r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360, sat / 100, val / 100)
        return cls.rgb_to_hex((r * 255, g * 255, b * 255))


@dataclass
class LedState:
    hue: float = 0.0
    sat: float = 100.0
    bright: float = 50.0
    on: bool = False

    @property
    def accent(self):
        return ColorUtils.hsv_hex(self.hue, self.sat, 100)


class LedProtocol:
    POWER_ON = bytes.fromhex("BC 01 01 01 55")
    POWER_OFF = bytes.fromhex("BC 01 01 00 55")

    @staticmethod
    def color(hue_deg, sat_pct):
        hue = round((hue_deg % 360) / 360 * Config.HUE_SCALE)
        sat = round(max(0, min(100, sat_pct)) * 10)
        return (
            bytes([0xBC, 0x04, 0x06])
            + hue.to_bytes(2, "big")
            + sat.to_bytes(2, "big")
            + b"\x00\x00\x55"
        )

    @staticmethod
    def brightness(percent):
        percent = max(0, min(100, percent))
        value = min(Config.BRIGHT_MAX, round(percent * Config.BRIGHT_MAX / 100))
        return bytes([0xBC, 0x05, 0x06]) + value.to_bytes(2, "big") + b"\x00\x00\x00\x00\x55"


class ColorController:
    def __init__(self, connection, state):
        self.connection = connection
        self.state = state
        self.suspended = False
        self._dirty_color = False
        self._dirty_bright = False

    def set_color(self, hue, sat):
        self.state.hue, self.state.sat = hue, sat
        self._dirty_color = True

    def set_brightness(self, value):
        self.state.bright = value
        self._dirty_bright = True

    def set_power(self, on):
        self.state.on = on
        if on:
            self.send_all()
        else:
            self.send(LedProtocol.POWER_OFF)

    def send(self, *packets):
        if self.suspended or not packets:
            return
        self.connection.send(*packets)

    def send_all(self):
        self.send(
            LedProtocol.POWER_ON,
            self._color_packet(),
            self._brightness_packet(),
        )

    def apply_saved(self, configured):
        if not configured or self.suspended:
            return
        if self.state.on:
            self.send_all()
        else:
            self.send(LedProtocol.POWER_OFF)

    def flush(self):
        dirty_color, dirty_bright = self._dirty_color, self._dirty_bright
        self._dirty_color = self._dirty_bright = False
        if not (dirty_color or dirty_bright):
            return

        packets = []
        if not self.state.on:
            self.state.on = True
            packets.append(LedProtocol.POWER_ON)
            dirty_color = dirty_bright = True

        if dirty_color:
            packets += [self._color_packet(), self._brightness_packet()]
        elif dirty_bright:
            packets.append(self._brightness_packet())
            if Config.COLOR_AFTER_BRIGHTNESS:
                packets.append(self._color_packet())
        self.send(*packets)

    def _color_packet(self):
        return LedProtocol.color(self.state.hue, self.state.sat)

    def _brightness_packet(self):
        return LedProtocol.brightness(self.state.bright)

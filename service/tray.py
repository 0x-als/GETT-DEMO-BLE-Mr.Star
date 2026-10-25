import logging

from PIL import Image, ImageDraw

from config import Config

try:
    import pystray
except Exception:
    pystray = None


class TrayService:
    available = pystray is not None

    def __init__(self, events, is_on):
        self.events = events
        self.is_on = is_on
        self.icon = None

    @property
    def active(self):
        return self.icon is not None

    @staticmethod
    def render_icon(color, on):
        scale = 4
        size = 64 * scale
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        main = color if on else "#6b7080"
        center = size / 2
        for radius, fill in ((28, main), (19, "#101116"), (10, main)):
            r = radius * scale
            draw.ellipse([center - r, center - r, center + r, center + r], fill=fill)
        resample = getattr(Image, "Resampling", Image).LANCZOS
        return image.resize((64, 64), resample)

    def _action(self, *payload):
        return lambda icon, item: self.events.emit("tray", payload)

    def _build_menu(self):
        colors = pystray.Menu(
            *[
                pystray.MenuItem(name, self._action("preset", hue, sat))
                for name, hue, sat in Config.PRESETS
            ]
        )
        return pystray.Menu(
            pystray.MenuItem("Открыть Lumen", self._action("show"), default=True),
            pystray.MenuItem(
                lambda item: "Выключить" if self.is_on() else "Включить",
                self._action("toggle"),
            ),
            pystray.MenuItem("Цвета", colors),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._action("quit")),
        )

    def start(self, color, on):
        if not self.available:
            return False
        try:
            self.icon = pystray.Icon(
                Config.APP_NAME,
                self.render_icon(color, on),
                Config.APP_NAME,
                self._build_menu(),
            )
            self.icon.run_detached()
            return True
        except Exception:
            logging.exception("Не удалось создать иконку в трее")
            self.icon = None
            return False

    def update(self, color, on):
        if not self.icon:
            return
        try:
            self.icon.icon = self.render_icon(color, on)
            self.icon.title = f"{Config.APP_NAME} — {'включено' if on else 'выключено'}"
            self.icon.update_menu()
        except Exception:
            logging.exception("Не удалось обновить иконку в трее")

    def stop(self):
        if not self.icon:
            return
        try:
            self.icon.stop()
        except Exception:
            pass

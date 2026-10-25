import json
import logging
import os
from dataclasses import asdict, dataclass, fields

from config import Config


@dataclass
class Settings:
    address: str = ""
    name: str = ""
    hue: float = 0.0
    sat: float = 100.0
    bright: float = 50.0
    on: bool = False
    configured: bool = False
    close_to_tray: bool = True
    off_on_shutdown: bool = True

    @classmethod
    def load(cls):
        settings = cls()
        try:
            data = json.loads(Config.CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return settings
        if isinstance(data, dict):
            for field in fields(cls):
                if field.name in data:
                    setattr(settings, field.name, data[field.name])
        settings._normalize()
        return settings

    def _normalize(self):
        try:
            self.hue = float(self.hue) % 360
            self.sat = max(0.0, min(100.0, float(self.sat)))
            self.bright = max(1.0, min(100.0, float(self.bright)))
        except (TypeError, ValueError):
            self.hue, self.sat, self.bright = 0.0, 100.0, 50.0
        self.address = str(self.address or "")
        self.name = str(self.name or "")
        for key in ("on", "configured", "close_to_tray", "off_on_shutdown"):
            setattr(self, key, bool(getattr(self, key)))

    def store_state(self, led):
        self.hue = round(led.hue, 1)
        self.sat = round(led.sat, 1)
        self.bright = round(led.bright, 1)
        self.on = led.on
        self.save()

    def save(self):
        try:
            Config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = Config.CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, Config.CONFIG_FILE)
        except Exception:
            logging.exception("Не удалось сохранить настройки")

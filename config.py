import os
import sys
from pathlib import Path


class Config:
    APP_NAME = "Lumen"
    IS_WIN = sys.platform == "win32"

    WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
    HUE_SCALE = 360
    BRIGHT_MAX = 1000
    COLOR_AFTER_BRIGHTNESS = False

    CONNECT_TIMEOUT = 15
    SCAN_TIMEOUT = 6.0
    RETRY_DELAYS = (3, 5, 8, 15, 30)

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    INSTANCE_PORT = 47653

    WINDOW_WIDTH = 400

    CONFIG_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / APP_NAME
    CONFIG_FILE = CONFIG_DIR / "config.json"
    LOG_FILE = CONFIG_DIR / "lumen.log"

    PRESETS = (
        ("Красный", 0, 100),
        ("Оранжевый", 24, 100),
        ("Жёлтый", 52, 100),
        ("Зелёный", 130, 100),
        ("Голубой", 185, 100),
        ("Синий", 222, 100),
        ("Фиолетовый", 272, 100),
        ("Розовый", 320, 100),
        ("Белый", 0, 0),
    )

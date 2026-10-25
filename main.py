import logging
import os
import sys

from config import Config
from service.autostart import AutostartManager
from service.color import ColorController, LedState
from service.connect import AsyncRunner, BleConnection, DeviceScanner
from service.events import EventBus
from service.instance import InstanceGuard
from service.settings import Settings
from service.tray import TrayService
from ui.app import App


class Application:
    def __init__(self, argv):
        self.minimized = "--minimized" in argv
        self.events = EventBus()
        self.guard = InstanceGuard()

    def _prepare_environment(self):
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        Config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=Config.LOG_FILE,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )
        sys.excepthook = lambda kind, value, trace: logging.error(
            "Необработанная ошибка", exc_info=(kind, value, trace)
        )

    def _build_window(self):
        settings = Settings.load()
        runner = AsyncRunner()
        connection = BleConnection(runner, self.events)
        scanner = DeviceScanner(runner, self.events)
        led = LedState(settings.hue, settings.sat, settings.bright, settings.on)
        controller = ColorController(connection, led)
        autostart = AutostartManager(os.path.abspath(__file__))
        tray = TrayService(self.events, lambda: led.on)
        return App(
            settings, self.events, connection, scanner, controller, autostart, tray,
            minimized=self.minimized,
        )

    def run(self):
        self._prepare_environment()
        if not self.guard.acquire():
            self.guard.ping()
            return
        window = self._build_window()
        self.guard.serve(lambda: self.events.emit("tray", ("show",)))
        window.mainloop()


if __name__ == "__main__":
    Application(sys.argv).run()

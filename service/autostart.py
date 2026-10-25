import logging
import os
import sys

from config import Config

if Config.IS_WIN:
    import winreg


class AutostartManager:
    def __init__(self, script_path):
        self.script_path = os.path.abspath(script_path)

    def command(self):
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" --minimized'
        executable = sys.executable
        windowless = os.path.join(os.path.dirname(executable), "pythonw.exe")
        if os.path.exists(windowless):
            executable = windowless
        return f'"{executable}" "{self.script_path}" --minimized'

    def current(self):
        if not Config.IS_WIN:
            return None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, Config.RUN_KEY) as key:
                return winreg.QueryValueEx(key, Config.APP_NAME)[0]
        except OSError:
            return None

    def is_enabled(self):
        return self.current() is not None

    def set_enabled(self, enabled):
        if not Config.IS_WIN:
            return False
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, Config.RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                if enabled:
                    winreg.SetValueEx(key, Config.APP_NAME, 0, winreg.REG_SZ, self.command())
                else:
                    try:
                        winreg.DeleteValue(key, Config.APP_NAME)
                    except FileNotFoundError:
                        pass
            logging.info("Автозапуск: %s", "включён" if enabled else "выключен")
            return True
        except OSError:
            logging.exception("Не удалось изменить автозапуск")
            return False

    def refresh(self):
        current = self.current()
        if current is not None and current != self.command():
            self.set_enabled(True)

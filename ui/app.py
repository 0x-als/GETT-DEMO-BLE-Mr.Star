import logging
import tkinter as tk
from collections import deque

import customtkinter as ctk

from config import Config
from service.connect import ReconnectPolicy
from service.shutdown import ShutdownHandler
from ui.panels import BrightnessPanel, ColorPanel, HeaderPanel, SettingsPanel
from ui.picker import DevicePicker
from ui.theme import Theme


class App(ctk.CTk):
    def __init__(
        self, settings, events, connection, scanner, controller, autostart, tray,
        minimized=False,
    ):
        super().__init__()
        if minimized:
            self.withdraw()
        ctk.set_appearance_mode("dark")
        self.title(Config.APP_NAME)
        self.resizable(False, False)
        self.configure(fg_color=Theme.BG)

        self.settings = settings
        self.events = events
        self.connection = connection
        self.scanner = scanner
        self.controller = controller
        self.led = controller.state
        self.autostart = autostart
        self.tray = tray
        self.reconnect = ReconnectPolicy(Config.RETRY_DELAYS)
        self.shutdown = ShutdownHandler(settings, controller, connection, events)
        self.sent_log = deque(maxlen=3)
        self.connected = False
        self.picker = None
        self._retry_job = None
        self._flush_job = None
        self._save_job = None
        self.handlers = {
            "connecting": self._on_connecting,
            "connected": self._on_connected,
            "disconnected": self._on_disconnected,
            "connect_error": self._on_connect_error,
            "write_error": self._on_write_error,
            "sent": self._on_sent,
            "scan_done": self._on_scan_done,
            "scan_error": self._on_scan_error,
            "restore": self._on_restore,
            "tray": self._on_tray,
        }

        self._build_ui()
        self._refresh_ui()
        self._resize()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.autostart.refresh()
        self.tray.start(self.led.accent, self.led.on)
        self.shutdown.start()
        self.after(80, self._poll_events)
        self._start_connection(minimized)

    def report_callback_exception(self, exc, val, tb):
        logging.error("Ошибка в интерфейсе", exc_info=(exc, val, tb))

    @staticmethod
    def _short(text, limit=14):
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _build_ui(self):
        self.header = HeaderPanel(
            self,
            on_status=lambda: self._connect_now(manual=True),
            on_settings=self._toggle_settings,
            on_power=self._on_power,
        )
        self.header.pack(fill="x", padx=24, pady=(22, 10))

        self.settings_panel = SettingsPanel(
            self,
            settings=self.settings,
            autostart_enabled=self.autostart.is_enabled(),
            tray_available=self.tray.available,
            on_pick_device=self._open_picker,
            on_autostart=self.autostart.set_enabled,
            on_tray=self._on_tray_option,
            on_shutdown=self._on_shutdown_option,
        )

        self.color_panel = ColorPanel(self, self._on_wheel, self._on_preset)
        self.color_panel.pack(fill="x", padx=20, pady=8)

        self.brightness_panel = BrightnessPanel(self, self.led.bright, self._on_brightness)
        self.brightness_panel.pack(fill="x", padx=20, pady=(8, 20))

        self.header.set_power(self.led.on)

    def _resize(self):
        self.update_idletasks()
        self.geometry(f"{Config.WINDOW_WIDTH}x{self.winfo_reqheight()}")

    def _toggle_settings(self):
        if self.settings_panel.winfo_ismapped():
            self.settings_panel.pack_forget()
        else:
            self.settings_panel.pack(fill="x", padx=20, pady=(0, 6), after=self.header)
        self._resize()

    def _refresh_ui(self):
        accent = self.led.accent
        self.color_panel.update_color(self.led.hue, self.led.sat, accent)
        self.brightness_panel.update_value(self.led.bright, accent)
        self.header.set_accent(accent)

    def show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after(250, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _on_close(self):
        if self.tray.active and self.settings.close_to_tray:
            self.withdraw()
        else:
            self._exit_app()

    def _exit_app(self):
        self._save_now()
        self.tray.stop()
        self.destroy()

    def destroy(self):
        self.connection.disconnect_now()
        super().destroy()

    def _start_connection(self, minimized):
        if self.settings.address:
            self._connect_now()
            return
        self.header.set_status("Выбрать устройство", Theme.WARN)
        if not minimized:
            self.after(400, lambda: self._open_picker(first_run=True))

    def _connect_now(self, manual=False):
        if not self.settings.address:
            self._open_picker()
            return
        self._cancel_retry()
        if manual:
            self.reconnect.reset()
        self.connection.connect_to(self.settings.address)

    def _cancel_retry(self):
        if self._retry_job:
            self.after_cancel(self._retry_job)
            self._retry_job = None

    def _schedule_retry(self, prefix, error):
        if self._retry_job or not self.settings.address:
            return
        delay = self.reconnect.next_delay()
        self.header.set_status(
            f"{prefix} · повтор {delay} с", Theme.ERR if error else Theme.MUTED
        )
        self._retry_job = self.after(delay * 1000, self._retry)

    def _retry(self):
        self._retry_job = None
        if not self.connected and not self.controller.suspended:
            self.connection.connect_to(self.settings.address)

    def _open_picker(self, first_run=False):
        if self._picker_alive():
            self.picker.lift()
            return
        self.picker = DevicePicker(
            self, self.scanner, self.settings.address,
            on_select=self._select_device, on_close=self._clear_picker, first_run=first_run,
        )

    def _picker_alive(self):
        if self.picker is None:
            return False
        try:
            return bool(self.picker.winfo_exists())
        except tk.TclError:
            return False

    def _clear_picker(self):
        self.picker = None

    def _select_device(self, address, name):
        self.settings.address, self.settings.name = address, name
        self.settings.save()
        logging.info("Выбрано устройство %s (%s)", name, address)
        self.settings_panel.set_device(name, address)
        self._cancel_retry()
        self.reconnect.reset()
        self.connected = False
        self.connection.connect_to(address)

    def _poll_events(self):
        for kind, data in self.events.drain():
            handler = self.handlers.get(kind)
            if handler:
                handler(data)
        self.after(80, self._poll_events)

    def _on_connecting(self, _data):
        self.header.set_status("Подключение…", Theme.WARN)

    def _on_connected(self, _data):
        self.connected = True
        self.reconnect.reset()
        self._cancel_retry()
        name = self.settings.name or self.settings.address
        self.header.set_status(f"Подключено · {self._short(name)}", Theme.OK)
        logging.info("Подключено к %s", self.settings.address)
        self.controller.apply_saved(self.settings.configured)

    def _on_disconnected(self, _data):
        self.connected = False
        logging.info("Соединение потеряно")
        self._schedule_retry("Отключено", error=False)

    def _on_connect_error(self, data):
        self.connected = False
        logging.info("Не удалось подключиться: %s", data)
        self.settings_panel.show_debug(data, error=True)
        self._schedule_retry("Нет связи", error=True)

    def _on_write_error(self, data):
        logging.info("Ошибка отправки: %s", data)
        self.settings_panel.show_debug(data, error=True)

    def _on_sent(self, data):
        self.sent_log.append(data)
        self.settings_panel.show_debug("\n".join(self.sent_log))

    def _on_scan_done(self, devices):
        if self._picker_alive():
            self.picker.show_results(devices)

    def _on_scan_error(self, data):
        logging.info("Ошибка поиска: %s", data)
        if self._picker_alive():
            self.picker.show_error(data)

    def _on_restore(self, _data):
        if self.connected:
            self.controller.apply_saved(self.settings.configured)

    def _on_tray(self, data):
        action = data[0]
        if action == "show":
            self.show_window()
        elif action == "toggle":
            self._toggle_power()
        elif action == "preset":
            self._on_preset(data[1], data[2])
        elif action == "quit":
            self._exit_app()

    def _toggle_power(self):
        on = not self.led.on
        self.header.set_power(on)
        self._on_power(on)

    def _on_power(self, on):
        self.settings.configured = True
        self.controller.set_power(on)
        self._schedule_save()

    def _on_wheel(self, hue, sat):
        self.controller.set_color(hue, sat)
        self._after_change()

    def _on_preset(self, hue, sat):
        self.controller.set_color(hue, sat)
        self._after_change()

    def _on_brightness(self, value):
        self.controller.set_brightness(value)
        self._after_change()

    def _after_change(self):
        self._refresh_ui()
        self.settings.configured = True
        self._schedule_save()
        if self._flush_job is None:
            self._flush_job = self.after(80, self._flush)

    def _flush(self):
        self._flush_job = None
        self.controller.flush()
        self.header.set_power(self.led.on)

    def _on_tray_option(self, enabled):
        self.settings.close_to_tray = enabled
        self.settings.save()

    def _on_shutdown_option(self, enabled):
        self.settings.off_on_shutdown = enabled
        self.settings.save()

    def _schedule_save(self):
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, self._save_now)

    def _save_now(self):
        self._save_job = None
        self.settings.store_state(self.led)
        self.tray.update(self.led.accent, self.led.on)

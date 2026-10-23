"""
Lumen — приложение для управления Bluetooth LED-лентой.

Установка:   pip install -U customtkinter bleak pystray
Запуск:      python led_control.py
Автозапуск:  включается переключателем в настройках (⚙), запускает приложение
             свёрнутым в трей и само подключается к сохранённой ленте.
Выключение:  при завершении работы Windows лента выключается (настройка в ⚙);
             сохранённый цвет при этом не меняется и вернётся после запуска.
"""

import asyncio
import colorsys
import json
import logging
import math
import os
import queue
import socket
import sys
import threading
import tkinter as tk
from collections import deque
from pathlib import Path

import customtkinter as ctk
from bleak import BleakClient, BleakScanner
from PIL import Image, ImageDraw, ImageTk

try:
    import pystray

    HAS_TRAY = True
except Exception:  # pystray не установлен или нет трея
    pystray = None
    HAS_TRAY = False

IS_WIN = sys.platform == "win32"
if IS_WIN:
    import winreg

# ============================ НАСТРОЙКИ ============================
WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"

POWER_ON = bytes.fromhex("BC 01 01 01 55")
POWER_OFF = bytes.fromhex("BC 01 01 00 55")

# Шкала оттенка в пакете цвета: 360 (градусы) или 1000.
HUE_SCALE = 360
# Максимум яркости в пакете (шкала контроллера 0..1000).
BRIGHT_MAX = 1000
# Если после смены яркости цвет «плывёт» — поставь True.
COLOR_AFTER_BRIGHTNESS = False

APP_NAME = "Lumen"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INSTANCE_PORT = 47653  # локальный порт для защиты от двойного запуска
RETRY_DELAYS = (3, 5, 8, 15, 30)  # паузы между попытками переподключения, сек

CONFIG_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "lumen.log"
# ===================================================================

# ------------------------------ Тема ------------------------------
BG = "#0b0c10"
CARD = "#14151b"
CARD_2 = "#1c1d26"
BORDER = "#252734"
TEXT = "#f4f5f9"
MUTED = "#7d8195"
OK = "#34d399"
WARN = "#fbbf24"
ERR = "#f87171"
BLUE = "#3b82f6"

FAMILY = {"win32": "Segoe UI", "darwin": "Helvetica Neue"}.get(sys.platform, "Noto Sans")
MONO = {"win32": "Consolas", "darwin": "Menlo"}.get(sys.platform, "DejaVu Sans Mono")


def font(size, weight="normal", family=None):
    return ctk.CTkFont(family=family or FAMILY, size=size, weight=weight)


# --------------------------- Цвет-утилиты --------------------------
def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a, b, t):
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def hsv_hex(h, s, v):
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, s / 100, v / 100)
    return rgb_to_hex((r * 255, g * 255, b * 255))


def short(text, n=14):
    return text if len(text) <= n else text[: n - 1] + "…"


# --------------------------- Пакеты команд -------------------------
def make_color(hue_deg: float, sat_pct: float) -> bytes:
    """BC 04 06 [HUE:2] [SAT:2] 00 00 55 — насыщенность 0..1000."""
    hue = round((hue_deg % 360) / 360 * HUE_SCALE)
    sat = round(max(0, min(100, sat_pct)) * 10)
    return (
        bytes([0xBC, 0x04, 0x06])
        + hue.to_bytes(2, "big")
        + sat.to_bytes(2, "big")
        + b"\x00\x00\x55"
    )


def make_brightness(percent: float) -> bytes:
    """BC 05 06 [VALUE:2] 00 00 00 00 55 — яркость 0..BRIGHT_MAX."""
    percent = max(0, min(100, percent))
    value = min(BRIGHT_MAX, round(percent * BRIGHT_MAX / 100))
    return bytes([0xBC, 0x05, 0x06]) + value.to_bytes(2, "big") + b"\x00\x00\x00\x00\x55"


# ============================ Настройки ===========================
DEFAULT_CONFIG = {
    "address": "",          # MAC выбранной ленты
    "name": "",             # её имя
    "hue": 0.0,
    "sat": 100.0,
    "bright": 50.0,
    "on": False,
    "configured": False,    # пользователь уже задавал состояние ленты
    "close_to_tray": True,  # крестик сворачивает в трей
    "off_on_shutdown": True,  # выключать ленту при завершении работы ПК
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
    except Exception:
        pass
    try:
        cfg["hue"] = float(cfg["hue"]) % 360
        cfg["sat"] = max(0.0, min(100.0, float(cfg["sat"])))
        cfg["bright"] = max(1.0, min(100.0, float(cfg["bright"])))
    except (TypeError, ValueError):
        cfg.update(hue=0.0, sat=100.0, bright=50.0)
    return cfg


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)
    except Exception:
        logging.exception("Не удалось сохранить настройки")


# ============================ Автозапуск ==========================
def launch_command():
    if getattr(sys, "frozen", False):  # собрано в .exe
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")  # без чёрного окна консоли
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}" --minimized'


def autostart_value():
    if not IS_WIN:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0]
    except OSError:
        return None


def set_autostart(enabled: bool) -> bool:
    if not IS_WIN:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        logging.info("Автозапуск: %s", "включён" if enabled else "выключен")
        return True
    except OSError:
        logging.exception("Не удалось изменить автозапуск")
        return False


# ======================= Один экземпляр программы =================
class InstanceGuard:
    """Не даёт запустить второй экземпляр; повторный запуск просто открывает окно."""

    def __init__(self):
        self.sock = None

    def acquire(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", INSTANCE_PORT))
            s.listen(5)
        except OSError:
            s.close()
            return False
        self.sock = s
        return True

    @staticmethod
    def ping():
        try:
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1):
                pass
        except OSError:
            pass

    def serve(self, on_ping):
        def loop():
            while True:
                try:
                    conn, _ = self.sock.accept()
                    conn.close()
                    on_ping()
                except OSError:
                    break

        threading.Thread(target=loop, daemon=True).start()


# ================= Слежение за выключением Windows ================
class ShutdownWatcher:
    """
    Невидимое окно в отдельном потоке: Windows рассылает окнам сообщения
    WM_QUERYENDSESSION / WM_ENDSESSION перед выключением или перезагрузкой.
    Bluetooth к этому моменту ещё работает, так что можно успеть выключить ленту.
    """

    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016
    ENDSESSION_LOGOFF = 0x80000000

    def __init__(self, on_shutdown, on_cancel):
        self.on_shutdown = on_shutdown  # вызывается, когда ПК выключается
        self.on_cancel = on_cancel      # вызывается, если выключение отменили
        self._proc = None
        if IS_WIN:
            threading.Thread(target=self._run, daemon=True).start()

    def _handle(self, msg, wparam, lparam):
        logoff = bool((lparam & 0xFFFFFFFF) & self.ENDSESSION_LOGOFF)
        if msg == self.WM_QUERYENDSESSION:
            if not logoff:  # выход из учётной записи — не выключение ПК
                self.on_shutdown()
            return 1  # TRUE: не мешаем завершению работы
        if msg == self.WM_ENDSESSION:
            if wparam and not logoff:
                self.on_shutdown()  # запасной путь (если не было QUERYENDSESSION)
            elif not wparam:
                self.on_cancel()    # выключение отменено
            return 0
        return None

    def _run(self):
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(
                LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            user32.DefWindowProcW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            ]
            user32.DefWindowProcW.restype = LRESULT
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.ATOM
            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
            ]
            user32.GetMessageW.restype = wintypes.BOOL
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = LRESULT
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE

            def wndproc(hwnd, msg, wparam, lparam):
                try:
                    result = self._handle(msg, wparam, lparam)
                    if result is not None:
                        return result
                except Exception:
                    logging.exception("Ошибка обработки выключения")
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._proc = WNDPROC(wndproc)  # держим ссылку, иначе сборщик мусора её удалит
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = "LumenShutdownWatcher"
            if not user32.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            # Обычное (не message-only) окно, чтобы получать рассылку о выключении.
            # Оно никогда не показывается и не попадает на панель задач.
            hwnd = user32.CreateWindowExW(
                0, wc.lpszClassName, "Lumen shutdown watcher", 0,
                0, 0, 0, 0, None, None, hinst, None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            logging.exception("Не удалось запустить слежение за выключением ПК")


# ========================== BLE в фоне ============================
class BLEWorker:
    """Держит соединение и сканирование в отдельном потоке с asyncio-циклом."""

    def __init__(self, events: queue.Queue):
        self.events = events
        self.client = None
        self.loop = asyncio.new_event_loop()
        self.lock = None
        self.conn_lock = None
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.lock = asyncio.Lock()
        self.conn_lock = asyncio.Lock()
        self._ready.set()
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _emit(self, kind, data=""):
        self.events.put((kind, data))

    def _lost(self, client):
        if client is self.client:  # игнорируем отключение «старого» устройства
            self._emit("disconnected")

    async def connect(self, address):
        async with self.conn_lock:
            if (
                self.client
                and self.client.is_connected
                and str(self.client.address).upper() == address.upper()
            ):
                self._emit("connected")
                return
            self._emit("connecting")
            old, self.client = self.client, None
            if old:
                try:
                    await old.disconnect()
                except Exception:
                    pass
            try:
                client = BleakClient(address, timeout=15, disconnected_callback=self._lost)
                self.client = client
                await client.connect()
                self._emit("connected")
            except Exception as exc:
                self.client = None
                self._emit("connect_error", f"{type(exc).__name__}: {exc}")

    async def disconnect(self):
        client, self.client = self.client, None
        try:
            if client and client.is_connected:
                await client.disconnect()
        except Exception:
            pass

    async def write(self, *packets):
        if not self.client or not self.client.is_connected:
            return
        async with self.lock:
            try:
                for packet in packets:
                    await self.client.write_gatt_char(WRITE_UUID, packet, response=False)
                    self._emit("sent", packet.hex(" ").upper())
                    await asyncio.sleep(0.025)
            except Exception as exc:
                self._emit("write_error", f"{type(exc).__name__}: {exc}")

    async def power_off_now(self):
        """Срочное выключение ленты (при завершении работы ПК): команда дважды."""
        if not self.client or not self.client.is_connected:
            logging.info("Лента не подключена — выключать нечего")
            return
        async with self.lock:
            for _ in range(2):
                await self.client.write_gatt_char(WRITE_UUID, POWER_OFF, response=False)
                await asyncio.sleep(0.15)
        logging.info("Команда выключения отправлена")

    async def scan(self, timeout=6.0):
        try:
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
            results = []
            for dev, adv in found.values():
                uuids = [u.lower() for u in (adv.service_uuids or [])]
                results.append(
                    {
                        "address": dev.address,
                        "name": dev.name or adv.local_name or "",
                        "rssi": adv.rssi,
                        "led": any("fff0" in u for u in uuids),
                    }
                )
            self._emit("scan_done", results)
        except Exception as exc:
            self._emit("scan_error", f"{type(exc).__name__}: {exc}")


# ======================= Кастомные виджеты ========================
class PowerButton(tk.Canvas):
    """Круглая кнопка питания со свечением цвета ленты."""

    def __init__(self, master, command, size=64, bg=BG):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.size, self.command, self.bg_color = size, command, bg
        self.on, self.accent, self.hover = False, "#ff3b30", False
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.redraw()

    def _set_hover(self, value):
        self.hover = value
        self.redraw()

    def _click(self, _e):
        self.on = not self.on
        self.redraw()
        self.command(self.on)

    def set_state(self, on):
        self.on = on
        self.redraw()

    def set_accent(self, color):
        if color != self.accent:
            self.accent = color
            self.redraw()

    def _circle(self, c, r, **kw):
        self.create_oval(c - r, c - r, c + r, c + r, **kw)

    def redraw(self):
        self.delete("all")
        c = self.size / 2
        r = c - 10
        if self.on:
            for rad, t in ((c - 1, 0.07), (c - 4, 0.12), (c - 7, 0.20)):
                self._circle(c, rad, fill=mix(self.bg_color, self.accent, t), outline="")
        fill = mix(self.accent, CARD_2, 0.80) if self.on else (CARD_2 if not self.hover else BORDER)
        self._circle(c, r, fill=fill, outline=self.accent if self.on else BORDER, width=2)
        col = self.accent if self.on else MUTED
        ir = r * 0.45
        self.create_arc(c - ir, c - ir + 1, c + ir, c + ir + 1, start=125, extent=290,
                        style="arc", outline=col, width=3)
        self.create_line(c, c - ir - 2, c, c - 1, fill=col, width=3, capstyle="round")


class ColorWheel(tk.Canvas):
    """Цветовой круг: угол = оттенок, расстояние от центра = насыщенность."""

    def __init__(self, master, size, command, bg=CARD):
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0,
                         cursor="hand2")
        self.size = size
        self.cx = self.cy = size / 2
        self.R = size / 2 - 18
        self.command = command
        self._drag = False

        self._photo = ImageTk.PhotoImage(self._render())
        self.create_oval(self.cx - self.R - 3, self.cy - self.R - 3,
                         self.cx + self.R + 3, self.cy + self.R + 3,
                         outline=BORDER, width=2)
        self.create_image(self.cx, self.cy, image=self._photo)
        self._shadow = self.create_oval(0, 0, 0, 0, fill="#000000", outline="")
        self._knob = self.create_oval(0, 0, 0, 0, fill="#ffffff", outline="")
        self._dot = self.create_oval(0, 0, 0, 0, fill="#ff0000", outline="")

        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", False))
        self.set(0, 100)

    def _render(self):
        size, R = self.size, self.R
        table = [
            tuple(int(v * 255) for v in colorsys.hsv_to_rgb(i / 360, 1, 1)) for i in range(360)
        ]
        img = Image.new("RGB", (size, size), (0, 0, 0))
        px = img.load()
        c = size / 2
        for y in range(size):
            dy = y - c
            for x in range(size):
                dx = x - c
                d = math.hypot(dx, dy)
                if d > R + 2:
                    continue
                sat = min(1.0, d / R)
                hr, hg, hb = table[int(math.degrees(math.atan2(dx, -dy)) % 360) % 360]
                px[x, y] = (
                    int(255 - sat * (255 - hr)),
                    int(255 - sat * (255 - hg)),
                    int(255 - sat * (255 - hb)),
                )
        S = 4
        mask = Image.new("L", (size * S, size * S), 0)
        ImageDraw.Draw(mask).ellipse(
            [(c - R) * S, (c - R) * S, (c + R) * S, (c + R) * S], fill=255
        )
        mask = mask.resize((size, size), Image.LANCZOS)
        img = img.convert("RGBA")
        img.putalpha(mask)
        return img

    def _press(self, e):
        if math.hypot(e.x - self.cx, e.y - self.cy) <= self.R + 16:
            self._drag = True
            self._move(e)

    def _move(self, e):
        if not self._drag:
            return
        dx, dy = e.x - self.cx, e.y - self.cy
        d = math.hypot(dx, dy)
        hue = math.degrees(math.atan2(dx, -dy)) % 360 if d > 0 else 0
        sat = min(1.0, d / self.R) * 100
        if sat < 5:  # «прилипание» к белому в центре
            sat = 0
        self.set(hue, sat)
        self.command(hue, sat)

    def set(self, hue, sat):
        a = math.radians(hue)
        r = self.R * sat / 100
        x, y = self.cx + r * math.sin(a), self.cy - r * math.cos(a)
        self.coords(self._shadow, x - 15, y - 13, x + 15, y + 17)
        self.coords(self._knob, x - 14, y - 14, x + 14, y + 14)
        self.coords(self._dot, x - 10, y - 10, x + 10, y + 10)
        self.itemconfigure(self._dot, fill=hsv_hex(hue, sat, 100))
        self.tag_raise(self._shadow)
        self.tag_raise(self._knob)
        self.tag_raise(self._dot)


class GradientSlider(tk.Canvas):
    """Слайдер с градиентной дорожкой от тёмного к текущему цвету."""

    PAD = 20
    TRACK_H = 16

    def __init__(self, master, width, command, bg=CARD, vmin=1, vmax=100, value=50):
        super().__init__(master, width=width, height=48, bg=bg, highlightthickness=0,
                         cursor="hand2")
        self.w, self.command = width, command
        self.vmin, self.vmax, self.value = vmin, vmax, value
        self.accent = "#ff3b30"
        self._photo = None
        self._track = self.create_image(width / 2, 24)
        self._knob = self.create_oval(0, 0, 0, 0, fill="#ffffff", outline="")
        self._dot = self.create_oval(0, 0, 0, 0, fill=self.accent, outline="")
        self._render()
        self._place()
        self.bind("<Button-1>", self._event)
        self.bind("<B1-Motion>", self._event)

    def _render(self):
        tw, th = self.w - 2 * self.PAD, self.TRACK_H
        left = mix("#20212a", self.accent, 0.10)
        img = Image.new("RGB", (tw, th))
        d = ImageDraw.Draw(img)
        for x in range(tw):
            d.line([(x, 0), (x, th)], fill=hex_to_rgb(mix(left, self.accent, x / (tw - 1))))
        S = 4
        mask = Image.new("L", (tw * S, th * S), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, tw * S - 1, th * S - 1], radius=th * S // 2, fill=255
        )
        mask = mask.resize((tw, th), Image.LANCZOS)
        img = img.convert("RGBA")
        img.putalpha(mask)
        self._photo = ImageTk.PhotoImage(img)
        self.itemconfigure(self._track, image=self._photo)

    def _place(self):
        t = (self.value - self.vmin) / (self.vmax - self.vmin)
        x = self.PAD + t * (self.w - 2 * self.PAD)
        y = 24
        self.coords(self._knob, x - 14, y - 14, x + 14, y + 14)
        self.coords(self._dot, x - 7, y - 7, x + 7, y + 7)

    def _event(self, e):
        t = max(0.0, min(1.0, (e.x - self.PAD) / (self.w - 2 * self.PAD)))
        self.value = self.vmin + t * (self.vmax - self.vmin)
        self._place()
        self.command(self.value)

    def set_accent(self, color):
        if color != self.accent:
            self.accent = color
            self._render()
            self.itemconfigure(self._dot, fill=color)


def make_tray_icon(color, on):
    """Иконка трея: кольцо с точкой цвета ленты (серая, если лента выключена)."""
    S, N = 4, 64 * 4
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    main = color if on else "#6b7080"
    c = N / 2
    for r, fill in ((28, main), (19, "#101116"), (10, main)):
        d.ellipse([c - r * S, c - r * S, c + r * S, c + r * S], fill=fill)
    return img.resize((64, 64), Image.LANCZOS)


# ====================== Окно выбора устройства ====================
def signal_text(rssi):
    if rssi is None:
        return ""
    bars = "▂▄▆█" if rssi > -60 else "▂▄▆" if rssi > -70 else "▂▄" if rssi > -82 else "▂"
    return f"{bars} {rssi} dBm"


class DevicePicker(ctk.CTkToplevel):
    """Список найденных Bluetooth-устройств с выбором."""

    def __init__(self, app, first_run=False):
        super().__init__(app, fg_color=BG)
        self.app = app
        self.title("Выбор устройства")
        self.geometry("430x620")
        self.resizable(False, False)
        self.results = []
        self.scanning = False
        self.protocol("WM_DELETE_WINDOW", self.close)

        ctk.CTkLabel(self, text="Устройства", font=font(26, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=26, pady=(22, 0))
        hint = (
            "Добро пожаловать! Включи ленту — она должна появиться в списке. "
            "Выбери её, и приложение запомнит устройство."
            if first_run
            else "Выбери ленту из найденных поблизости Bluetooth-устройств."
        )
        ctk.CTkLabel(self, text=hint, font=font(12), text_color=MUTED, justify="left",
                     wraplength=370, anchor="w").pack(anchor="w", padx=26, pady=(4, 10))

        status = ctk.CTkFrame(self, fg_color="transparent")
        status.pack(fill="x", padx=26)
        self.status_lbl = ctk.CTkLabel(status, text="", font=font(12, "bold"),
                                       text_color=MUTED)
        self.status_lbl.pack(anchor="w")
        self.progress = ctk.CTkProgressBar(status, mode="indeterminate", height=5,
                                           progress_color=BLUE, fg_color=CARD_2)
        self.progress.pack(fill="x", pady=(6, 10))
        self.progress.set(0)

        self.list = ctk.CTkScrollableFrame(self, fg_color=CARD, corner_radius=22,
                                           border_width=1, border_color=BORDER, height=360)
        self.list.pack(fill="both", expand=True, padx=20)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=26, pady=16)
        self.show_all = ctk.CTkCheckBox(bottom, text="Без названия", font=font(12),
                                        text_color=MUTED, checkbox_width=18,
                                        checkbox_height=18, command=self._render_list)
        self.show_all.pack(side="left")
        self.scan_btn = ctk.CTkButton(bottom, text="Искать снова", width=130, height=38,
                                      corner_radius=14, fg_color=BLUE, hover_color="#2f6fd6",
                                      font=font(13, "bold"), command=self.rescan)
        self.scan_btn.pack(side="right")

        self.after(200, self._raise)
        self.rescan()

    def _raise(self):
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def close(self):
        self.app.picker = None
        self.destroy()

    def rescan(self):
        if self.scanning:
            return
        self.scanning = True
        self.status_lbl.configure(text="Идёт поиск устройств…", text_color=MUTED)
        self.scan_btn.configure(state="disabled")
        self.progress.start()
        self.app.ble.submit(self.app.ble.scan(6.0))

    def _finish_scan(self):
        self.scanning = False
        self.progress.stop()
        self.progress.set(0)
        self.scan_btn.configure(state="normal")

    def show_results(self, results):
        self._finish_scan()
        self.results = results
        self._render_list()

    def show_error(self, text):
        self._finish_scan()
        self.results = []
        self._render_list()
        self.status_lbl.configure(text=f"Ошибка поиска: {text[:60]}", text_color=ERR)

    def _render_list(self):
        for child in self.list.winfo_children():
            child.destroy()
        items = [d for d in self.results if d["name"] or self.show_all.get()]
        items.sort(key=lambda d: (not d["led"], not d["name"], -(d["rssi"] or -999)))
        if self.status_lbl.cget("text_color") != ERR:
            self.status_lbl.configure(text=f"Найдено устройств: {len(items)}", text_color=MUTED)

        if not items:
            ctk.CTkLabel(
                self.list,
                text="Ничего не найдено.\nПроверь, что лента включена, а Bluetooth\n"
                     "на компьютере работает, и нажми «Искать снова».",
                font=font(13), text_color=MUTED, justify="center",
            ).pack(pady=50)
            return

        current = self.app.cfg["address"].upper()
        for d in items:
            row = ctk.CTkFrame(self.list, fg_color=CARD_2, corner_radius=16)
            row.pack(fill="x", padx=6, pady=5)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=d["name"] or "Без названия", font=font(14, "bold"),
                         text_color=TEXT if d["name"] else MUTED, anchor="w"
                         ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
            ctk.CTkLabel(row, text=f'{d["address"]}   {signal_text(d["rssi"])}',
                         font=font(11, family=MONO), text_color=MUTED, anchor="w"
                         ).grid(row=1, column=0, sticky="w", padx=14,
                                pady=(0, 2 if d["led"] else 10))
            if d["led"]:
                ctk.CTkLabel(row, text="Похоже на LED-ленту", font=font(11, "bold"),
                             text_color=OK, anchor="w"
                             ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))
            is_current = d["address"].upper() == current
            ctk.CTkButton(
                row, text="Текущее" if is_current else "Выбрать", width=92, height=34,
                corner_radius=12, font=font(12, "bold"),
                state="disabled" if is_current else "normal",
                fg_color=BORDER if is_current else BLUE, hover_color="#2f6fd6",
                command=lambda d=d: self._choose(d),
            ).grid(row=0, column=1, rowspan=3, padx=12)

    def _choose(self, d):
        self.app.select_device(d["address"], d["name"])
        self.close()


# ============================ Приложение ==========================
class App(ctk.CTk):
    WIDTH = 400
    PRESETS = [  # (название, оттенок, насыщенность)
        ("Красный", 0, 100), ("Оранжевый", 24, 100), ("Жёлтый", 52, 100),
        ("Зелёный", 130, 100), ("Голубой", 185, 100), ("Синий", 222, 100),
        ("Фиолетовый", 272, 100), ("Розовый", 320, 100), ("Белый", 0, 0),
    ]

    def __init__(self, minimized=False):
        super().__init__()
        if minimized:
            self.withdraw()  # сразу прячем: окна нет ни на экране, ни на панели задач
        ctk.set_appearance_mode("dark")
        self.title("Lumen")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        self.cfg = load_config()
        self.events = queue.Queue()
        self.ble = BLEWorker(self.events)
        self.log = deque(maxlen=3)

        self.hue, self.sat = self.cfg["hue"], self.cfg["sat"]
        self.bright, self.on = self.cfg["bright"], bool(self.cfg["on"])
        self.connected = False
        self.picker = None
        self.tray = None
        self._flush_scheduled = False
        self._dirty_color = self._dirty_bright = False
        self._retry_job = None
        self._retry_n = 0
        self._save_job = None
        self._shutting_down = False

        self._build_ui()
        self.power_btn.set_state(self.on)
        self._refresh_ui()
        self._update_device_label()

        self.update_idletasks()
        self.geometry(f"{self.WIDTH}x{self.winfo_reqheight()}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Если файл программы переместили — обновим путь в автозапуске
        if IS_WIN and autostart_value() not in (None, launch_command()):
            set_autostart(True)

        self._setup_tray()
        self.shutdown_watcher = ShutdownWatcher(
            self._on_shutdown_query, self._on_shutdown_cancel
        )
        self.after(80, self._poll_events)

        if self.cfg["address"]:
            self._connect()
        else:
            self._set_status("Выбрать устройство", WARN)
            if not minimized:  # первый запуск — сразу ищем ленту
                self.after(400, lambda: self._open_picker(first_run=True))

    def report_callback_exception(self, exc, val, tb):
        logging.error("Ошибка в интерфейсе", exc_info=(exc, val, tb))

    # ---------------------------- UI ----------------------------
    def _card(self, pady=8):
        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=26,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", padx=20, pady=pady)
        return card

    def _build_ui(self):
        # ---------- Шапка ----------
        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=24, pady=(22, 10))
        self.header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.header, text="Lumen", font=font(30, "bold"),
                     text_color=TEXT).grid(row=0, column=0, sticky="w")

        self.status = ctk.CTkButton(
            self.header, text="●  Не подключено", height=30, corner_radius=15,
            fg_color=CARD, hover_color=CARD_2, border_width=1, border_color=BORDER,
            text_color=MUTED, font=font(12, "bold"), command=lambda: self._connect(True),
            width=190,
        )
        self.status.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.gear = ctk.CTkButton(
            self.header, text="⚙", width=40, height=40, corner_radius=20,
            fg_color=CARD, hover_color=CARD_2, border_width=1, border_color=BORDER,
            text_color=MUTED, font=font(18), command=self._toggle_settings,
        )
        self.gear.grid(row=0, column=1, rowspan=2, padx=(0, 10))

        self.power_btn = PowerButton(self.header, self._on_power, size=64, bg=BG)
        self.power_btn.grid(row=0, column=2, rowspan=2)

        # ---------- Настройки (скрыты) ----------
        self.settings = ctk.CTkFrame(self, fg_color=CARD, corner_radius=22,
                                     border_width=1, border_color=BORDER)
        ctk.CTkLabel(self.settings, text="Устройство", font=font(12),
                     text_color=MUTED).pack(anchor="w", padx=18, pady=(14, 4))
        dev = ctk.CTkFrame(self.settings, fg_color=CARD_2, corner_radius=16)
        dev.pack(fill="x", padx=18)
        dev.grid_columnconfigure(0, weight=1)
        self.dev_name = ctk.CTkLabel(dev, text="", font=font(14, "bold"),
                                     text_color=TEXT, anchor="w")
        self.dev_name.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        self.dev_addr = ctk.CTkLabel(dev, text="", font=font(11, family=MONO),
                                     text_color=MUTED, anchor="w")
        self.dev_addr.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
        ctk.CTkButton(dev, text="Сменить", width=90, height=34, corner_radius=12,
                      fg_color=BLUE, hover_color="#2f6fd6", font=font(12, "bold"),
                      command=self._open_picker).grid(row=0, column=1, rowspan=2, padx=12)

        self.autostart_sw = ctk.CTkSwitch(
            self.settings, text="Запускать вместе с Windows (в трее)", font=font(13),
            text_color=TEXT, progress_color=BLUE, command=self._on_autostart,
        )
        self.autostart_sw.pack(anchor="w", padx=18, pady=(16, 0))
        if autostart_value() is not None:
            self.autostart_sw.select()
        if not IS_WIN:
            self.autostart_sw.configure(state="disabled", text="Автозапуск (только Windows)")

        self.tray_sw = ctk.CTkSwitch(
            self.settings, text="Крестик сворачивает в трей", font=font(13),
            text_color=TEXT, progress_color=BLUE, command=self._on_tray_switch,
        )
        self.tray_sw.pack(anchor="w", padx=18, pady=(12, 0))
        if self.cfg["close_to_tray"]:
            self.tray_sw.select()
        if not HAS_TRAY:
            self.tray_sw.configure(state="disabled", text="Трей недоступен (pip install pystray)")

        self.shutdown_sw = ctk.CTkSwitch(
            self.settings, text="Выключать ленту при завершении работы ПК", font=font(13),
            text_color=TEXT, progress_color=BLUE, command=self._on_shutdown_switch,
        )
        self.shutdown_sw.pack(anchor="w", padx=18, pady=(12, 0))
        if self.cfg["off_on_shutdown"]:
            self.shutdown_sw.select()
        if not IS_WIN:
            self.shutdown_sw.configure(state="disabled",
                                       text="Выключение при завершении ПК (только Windows)")

        self.debug = ctk.CTkLabel(self.settings, text="Отправленные пакеты появятся здесь",
                                  font=font(11, family=MONO), text_color=MUTED,
                                  justify="left", anchor="w", wraplength=330)
        self.debug.pack(fill="x", padx=18, pady=(14, 14))

        # ---------- Цвет ----------
        color_card = self._card()
        self.wheel = ColorWheel(color_card, 320, self._on_wheel, bg=CARD)
        self.wheel.pack(pady=(18, 6))

        info = ctk.CTkFrame(color_card, fg_color="transparent")
        info.pack(fill="x", padx=24)
        self.swatch = ctk.CTkFrame(info, width=34, height=34, corner_radius=17,
                                   fg_color="#ff0000")
        self.swatch.pack(side="left")
        self.swatch.pack_propagate(False)
        texts = ctk.CTkFrame(info, fg_color="transparent")
        texts.pack(side="left", padx=12)
        self.hex_label = ctk.CTkLabel(texts, text="#FF0000", font=font(17, "bold"),
                                      text_color=TEXT, anchor="w")
        self.hex_label.pack(anchor="w")
        self.info_label = ctk.CTkLabel(texts, text="", font=font(12),
                                       text_color=MUTED, anchor="w")
        self.info_label.pack(anchor="w")

        presets = ctk.CTkFrame(color_card, fg_color="transparent")
        presets.pack(pady=(14, 20))
        self.preset_btns = []
        for _name, h, s in self.PRESETS:
            color = hsv_hex(h, s, 100)
            btn = ctk.CTkButton(
                presets, text="", width=30, height=30, corner_radius=15,
                fg_color=color, hover_color=color, border_width=3, border_color=CARD,
                command=lambda h=h, s=s: self._on_preset(h, s),
            )
            btn.pack(side="left", padx=4)
            self.preset_btns.append((btn, h, s))

        # ---------- Яркость ----------
        br_card = self._card(pady=(8, 20))
        head = ctk.CTkFrame(br_card, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(18, 0))
        ctk.CTkLabel(head, text="Яркость", font=font(14), text_color=MUTED).pack(side="left")
        self.bright_label = ctk.CTkLabel(head, text="50%", font=font(26, "bold"),
                                         text_color=TEXT)
        self.bright_label.pack(side="right")
        self.slider = GradientSlider(br_card, 340, self._on_bright, bg=CARD,
                                     value=self.bright)
        self.slider.pack(pady=(4, 16))

    def _toggle_settings(self):
        if self.settings.winfo_ismapped():
            self.settings.pack_forget()
        else:
            self.settings.pack(fill="x", padx=20, pady=(0, 6), after=self.header)
        self.update_idletasks()
        self.geometry(f"{self.WIDTH}x{self.winfo_reqheight()}")

    def _refresh_ui(self):
        accent = hsv_hex(self.hue, self.sat, 100)
        self.wheel.set(self.hue, self.sat)
        self.slider.set_accent(accent)
        self.power_btn.set_accent(accent)
        self.swatch.configure(fg_color=accent)
        self.hex_label.configure(text=accent.upper())
        self.info_label.configure(
            text=f"Оттенок {round(self.hue)}°  ·  Насыщенность {round(self.sat)}%"
        )
        self.bright_label.configure(text=f"{round(self.bright)}%")
        for btn, h, s in self.preset_btns:
            dh = abs((self.hue - h + 180) % 360 - 180)
            selected = abs(self.sat - s) <= 3 and (s == 0 or dh <= 3)
            btn.configure(border_color="#ffffff" if selected else CARD)

    def _update_device_label(self):
        if self.cfg["address"]:
            self.dev_name.configure(text=self.cfg["name"] or "Без названия")
            self.dev_addr.configure(text=self.cfg["address"])
        else:
            self.dev_name.configure(text="Не выбрано")
            self.dev_addr.configure(text="Нажми «Сменить», чтобы найти ленту")

    # ----------------------- Окно / трей ------------------------
    def _show_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after(250, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _on_close(self):
        if self.tray and self.cfg["close_to_tray"]:
            self.withdraw()  # окно исчезает и с экрана, и с панели задач
        else:
            self._quit()

    def _setup_tray(self):
        if not HAS_TRAY:
            return
        try:
            def cb(*args):
                return lambda icon, item: self.events.put(("tray", args))

            colors = pystray.Menu(
                *[pystray.MenuItem(name, cb("preset", h, s)) for name, h, s in self.PRESETS]
            )
            menu = pystray.Menu(
                pystray.MenuItem("Открыть Lumen", cb("show"), default=True),
                pystray.MenuItem(lambda item: "Выключить" if self.on else "Включить",
                                 cb("toggle")),
                pystray.MenuItem("Цвета", colors),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", cb("quit")),
            )
            self.tray = pystray.Icon(
                APP_NAME, make_tray_icon(hsv_hex(self.hue, self.sat, 100), self.on),
                APP_NAME, menu,
            )
            self.tray.run_detached()
        except Exception:
            logging.exception("Не удалось создать иконку в трее")
            self.tray = None

    def _update_tray(self):
        if not self.tray:
            return
        try:
            self.tray.icon = make_tray_icon(hsv_hex(self.hue, self.sat, 100), self.on)
            self.tray.title = f"Lumen — {'включено' if self.on else 'выключено'}"
            self.tray.update_menu()
        except Exception:
            logging.exception("Не удалось обновить иконку в трее")

    def _handle_tray(self, data):
        action = data[0]
        if action == "show":
            self._show_window()
        elif action == "toggle":
            self.power_btn.set_state(not self.on)
            self._on_power(not self.on)
        elif action == "preset":
            self._on_preset(data[1], data[2])
        elif action == "quit":
            self._quit()

    def _quit(self):
        self._save_now()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.destroy()

    # ---------------------- Выбор устройства --------------------
    def _open_picker(self, first_run=False):
        if self.picker is not None:
            try:
                if self.picker.winfo_exists():
                    self.picker.lift()
                    return
            except tk.TclError:
                pass
        self.picker = DevicePicker(self, first_run=first_run)

    def select_device(self, address, name):
        self.cfg["address"], self.cfg["name"] = address, name
        save_config(self.cfg)
        logging.info("Выбрано устройство %s (%s)", name, address)
        self._update_device_label()
        self._cancel_retry()
        self._retry_n = 0
        self.connected = False
        self.ble.submit(self.ble.connect(address))

    # ------------------------ Подключение -----------------------
    def _set_status(self, text, color):
        self.status.configure(text=f"●  {text}", text_color=color)

    def _connect(self, manual=False):
        if not self.cfg["address"]:
            self._open_picker()
            return
        self._cancel_retry()
        if manual:
            self._retry_n = 0
        self.ble.submit(self.ble.connect(self.cfg["address"]))

    def _cancel_retry(self):
        if self._retry_job:
            self.after_cancel(self._retry_job)
            self._retry_job = None

    def _schedule_retry(self, prefix):
        if self._retry_job or not self.cfg["address"]:
            return
        delay = RETRY_DELAYS[min(self._retry_n, len(RETRY_DELAYS) - 1)]
        self._retry_n += 1
        self._set_status(f"{prefix} · повтор {delay} с", ERR if prefix.startswith("Нет") else MUTED)
        self._retry_job = self.after(delay * 1000, self._retry)

    def _retry(self):
        self._retry_job = None
        if not self.connected and not self._shutting_down:
            self.ble.submit(self.ble.connect(self.cfg["address"]))

    def _apply_saved_state(self):
        """После (пере)подключения возвращаем ленте сохранённый цвет и яркость."""
        if not self.cfg["configured"] or self._shutting_down:
            return
        if self.on:
            self._send_all()
        else:
            self._send(POWER_OFF)

    def _poll_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "connecting":
                    self._set_status("Подключение…", WARN)
                elif kind == "connected":
                    self.connected = True
                    self._retry_n = 0
                    self._cancel_retry()
                    name = self.cfg["name"] or self.cfg["address"]
                    self._set_status(f"Подключено · {short(name)}", OK)
                    logging.info("Подключено к %s", self.cfg["address"])
                    self._apply_saved_state()
                elif kind == "disconnected":
                    self.connected = False
                    logging.info("Соединение потеряно")
                    self._schedule_retry("Отключено")
                elif kind == "connect_error":
                    self.connected = False
                    logging.info("Не удалось подключиться: %s", data)
                    self.debug.configure(text=data, text_color=ERR)
                    self._schedule_retry("Нет связи")
                elif kind == "write_error":
                    logging.info("Ошибка отправки: %s", data)
                    self.debug.configure(text=data, text_color=ERR)
                elif kind == "sent":
                    self.log.append(data)
                    self.debug.configure(text="\n".join(self.log), text_color=MUTED)
                elif kind == "scan_done":
                    if self.picker is not None and self.picker.winfo_exists():
                        self.picker.show_results(data)
                elif kind == "scan_error":
                    logging.info("Ошибка поиска: %s", data)
                    if self.picker is not None and self.picker.winfo_exists():
                        self.picker.show_error(data)
                elif kind == "restore":
                    if self.connected:
                        self._apply_saved_state()
                elif kind == "tray":
                    self._handle_tray(data)
        except queue.Empty:
            pass
        self.after(80, self._poll_events)

    # -------------------------- События -------------------------
    def _on_power(self, on):
        self.on = on
        self.cfg["configured"] = True
        self._schedule_save()
        if on:
            self._send_all()
        else:
            self._send(POWER_OFF)

    def _on_wheel(self, hue, sat):
        self.hue, self.sat = hue, sat
        self._refresh_ui()
        self._changed(color=True)

    def _on_preset(self, hue, sat):
        self.hue, self.sat = hue, sat
        self._refresh_ui()
        self._changed(color=True)

    def _on_bright(self, value):
        self.bright = value
        self._refresh_ui()
        self._changed(bright=True)

    def _on_autostart(self):
        want = bool(self.autostart_sw.get())
        if not set_autostart(want):
            (self.autostart_sw.deselect if want else self.autostart_sw.select)()
            self.debug.configure(text="Не удалось изменить автозапуск", text_color=ERR)

    def _on_tray_switch(self):
        self.cfg["close_to_tray"] = bool(self.tray_sw.get())
        save_config(self.cfg)

    def _on_shutdown_switch(self):
        self.cfg["off_on_shutdown"] = bool(self.shutdown_sw.get())
        save_config(self.cfg)

    # ----------------- Сохранение и отправка --------------------
    def _schedule_save(self):
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, self._save_now)

    def _persist_state(self):
        self.cfg.update(
            hue=round(self.hue, 1), sat=round(self.sat, 1), bright=round(self.bright, 1),
            on=self.on,
        )
        save_config(self.cfg)

    def _save_now(self):
        self._save_job = None
        self._persist_state()
        self._update_tray()

    # ---------------- Выключение / отмена выключения ПК ----------------
    # Вызываются из потока ShutdownWatcher, поэтому интерфейс здесь не трогаем.
    def _on_shutdown_query(self):
        if not self.cfg["off_on_shutdown"] or self._shutting_down:
            return
        self._shutting_down = True
        logging.info("Завершение работы ПК: выключаем ленту")
        self._persist_state()  # сохранённое состояние остаётся прежним («включено»)
        try:
            self.ble.submit(self.ble.power_off_now()).result(timeout=4)
        except Exception:
            logging.exception("Не удалось выключить ленту при завершении работы")

    def _on_shutdown_cancel(self):
        if self._shutting_down:
            logging.info("Завершение работы отменено — возвращаем состояние ленты")
            self._shutting_down = False
            self.events.put(("restore", ""))

    def _send(self, *packets):
        if self._shutting_down:  # во время выключения ПК ленту не трогаем
            return
        self.ble.submit(self.ble.write(*packets))

    def _send_all(self):
        self._send(POWER_ON, make_color(self.hue, self.sat), make_brightness(self.bright))

    def _changed(self, color=False, bright=False):
        self.cfg["configured"] = True
        self._schedule_save()
        self._dirty_color |= color
        self._dirty_bright |= bright
        if not self._flush_scheduled:  # не чаще ~12 раз в секунду
            self._flush_scheduled = True
            self.after(80, self._flush)

    def _flush(self):
        self._flush_scheduled = False
        color, bright = self._dirty_color, self._dirty_bright
        self._dirty_color = self._dirty_bright = False

        packets = []
        if not self.on:  # трогаем цвет/яркость — включаем ленту
            self.on = True
            self.power_btn.set_state(True)
            packets.append(POWER_ON)
            color = bright = True

        if color:
            packets += [make_color(self.hue, self.sat), make_brightness(self.bright)]
        elif bright:
            packets.append(make_brightness(self.bright))
            if COLOR_AFTER_BRIGHTNESS:
                packets.append(make_color(self.hue, self.sat))
        if packets:
            self._send(*packets)

    def destroy(self):
        try:
            self.ble.submit(self.ble.disconnect()).result(timeout=3)
        except Exception:
            pass
        super().destroy()


def main():
    # pythonw.exe запускает программу без консоли: stdout/stderr отсутствуют
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8",
    )
    sys.excepthook = lambda t, v, tb: logging.error("Необработанная ошибка", exc_info=(t, v, tb))

    guard = InstanceGuard()
    if not guard.acquire():  # уже запущено — просто покажем окно запущенной копии
        guard.ping()
        return

    app = App(minimized="--minimized" in sys.argv)
    guard.serve(lambda: app.events.put(("tray", ("show",))))
    app.mainloop()


if __name__ == "__main__":
    main()
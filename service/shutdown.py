import logging
import threading

from config import Config


class ShutdownWatcher:
    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016
    ENDSESSION_LOGOFF = 0x80000000

    def __init__(self, on_shutdown, on_cancel):
        self.on_shutdown = on_shutdown
        self.on_cancel = on_cancel
        self._proc = None

    def start(self):
        if Config.IS_WIN:
            threading.Thread(target=self._run, daemon=True).start()

    def _handle(self, msg, wparam, lparam):
        logoff = bool((lparam & 0xFFFFFFFF) & self.ENDSESSION_LOGOFF)
        if msg == self.WM_QUERYENDSESSION:
            if not logoff:
                self.on_shutdown()
            return 1
        if msg == self.WM_ENDSESSION:
            if wparam and not logoff:
                self.on_shutdown()
            elif not wparam:
                self.on_cancel()
            return 0
        return None

    def _run(self):
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            lresult = ctypes.c_ssize_t
            wndproc_type = ctypes.WINFUNCTYPE(
                lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", wndproc_type),
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
            user32.DefWindowProcW.restype = lresult
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
            user32.DispatchMessageW.restype = lresult
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

            self._proc = wndproc_type(wndproc)
            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = "LumenShutdownWatcher"
            if not user32.RegisterClassW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
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


class ShutdownHandler:
    def __init__(self, settings, controller, connection, events):
        self.settings = settings
        self.controller = controller
        self.connection = connection
        self.events = events
        self.watcher = ShutdownWatcher(self.on_shutdown, self.on_cancel)

    def start(self):
        self.watcher.start()

    def on_shutdown(self):
        if not self.settings.off_on_shutdown or self.controller.suspended:
            return
        self.controller.suspended = True
        logging.info("Завершение работы ПК: выключаем ленту")
        self.settings.store_state(self.controller.state)
        try:
            self.connection.power_off_blocking(4)
        except Exception:
            logging.exception("Не удалось выключить ленту при завершении работы")

    def on_cancel(self):
        if self.controller.suspended:
            logging.info("Завершение работы отменено — возвращаем состояние ленты")
            self.controller.suspended = False
            self.events.emit("restore")

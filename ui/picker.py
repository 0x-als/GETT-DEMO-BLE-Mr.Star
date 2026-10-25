import tkinter as tk

import customtkinter as ctk

from ui.theme import Theme


class DevicePicker(ctk.CTkToplevel):
    def __init__(self, master, scanner, current_address, on_select, on_close, first_run=False):
        super().__init__(master, fg_color=Theme.BG)
        self.scanner = scanner
        self.current_address = (current_address or "").upper()
        self.on_select = on_select
        self.on_close = on_close
        self.devices = []
        self.scanning = False
        self.has_error = False

        self.title("Выбор устройства")
        self.geometry("430x620")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._build(first_run)
        self.after(200, self._raise)
        self.rescan()

    @staticmethod
    def signal_text(rssi):
        if rssi is None:
            return ""
        if rssi > -60:
            bars = "▂▄▆█"
        elif rssi > -70:
            bars = "▂▄▆"
        elif rssi > -82:
            bars = "▂▄"
        else:
            bars = "▂"
        return f"{bars} {rssi} dBm"

    def _build(self, first_run):
        ctk.CTkLabel(
            self, text="Устройства", font=Theme.font(26, "bold"), text_color=Theme.TEXT
        ).pack(anchor="w", padx=26, pady=(22, 0))

        if first_run:
            hint = (
                "Добро пожаловать! Включи ленту — она должна появиться в списке. "
                "Выбери её, и приложение запомнит устройство."
            )
        else:
            hint = "Выбери ленту из найденных поблизости Bluetooth-устройств."
        ctk.CTkLabel(
            self, text=hint, font=Theme.font(12), text_color=Theme.MUTED,
            justify="left", wraplength=370, anchor="w",
        ).pack(anchor="w", padx=26, pady=(4, 10))

        status = ctk.CTkFrame(self, fg_color="transparent")
        status.pack(fill="x", padx=26)
        self.status_label = ctk.CTkLabel(
            status, text="", font=Theme.font(12, "bold"), text_color=Theme.MUTED
        )
        self.status_label.pack(anchor="w")
        self.progress = ctk.CTkProgressBar(
            status, mode="indeterminate", height=5,
            progress_color=Theme.BLUE, fg_color=Theme.CARD_2,
        )
        self.progress.pack(fill="x", pady=(6, 10))
        self.progress.set(0)

        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color=Theme.CARD, corner_radius=22,
            border_width=1, border_color=Theme.BORDER, height=360,
        )
        self.list_frame.pack(fill="both", expand=True, padx=20)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=26, pady=16)
        self.show_all = ctk.CTkCheckBox(
            bottom, text="Без названия", font=Theme.font(12), text_color=Theme.MUTED,
            checkbox_width=18, checkbox_height=18, command=self._render_list,
        )
        self.show_all.pack(side="left")
        self.scan_button = ctk.CTkButton(
            bottom, text="Искать снова", width=130, height=38, corner_radius=14,
            fg_color=Theme.BLUE, hover_color=Theme.BLUE_HOVER,
            font=Theme.font(13, "bold"), command=self.rescan,
        )
        self.scan_button.pack(side="right")

    def _raise(self):
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def close(self):
        self.on_close()
        self.destroy()

    def rescan(self):
        if self.scanning:
            return
        self.scanning = True
        self.has_error = False
        self.status_label.configure(text="Идёт поиск устройств…", text_color=Theme.MUTED)
        self.scan_button.configure(state="disabled")
        self.progress.start()
        self.scanner.start()

    def _finish_scan(self):
        self.scanning = False
        self.progress.stop()
        self.progress.set(0)
        self.scan_button.configure(state="normal")

    def show_results(self, devices):
        self._finish_scan()
        self.devices = devices
        self._render_list()

    def show_error(self, text):
        self._finish_scan()
        self.devices = []
        self.has_error = True
        self._render_list()
        self.status_label.configure(text=f"Ошибка поиска: {text[:60]}", text_color=Theme.ERR)

    def _visible_devices(self):
        items = [d for d in self.devices if d.name or self.show_all.get()]
        items.sort(key=lambda d: (not d.led, not d.name, -(d.rssi or -999)))
        return items

    def _render_list(self):
        for child in self.list_frame.winfo_children():
            child.destroy()
        items = self._visible_devices()
        if not self.has_error:
            self.status_label.configure(
                text=f"Найдено устройств: {len(items)}", text_color=Theme.MUTED
            )

        if not items:
            ctk.CTkLabel(
                self.list_frame,
                text="Ничего не найдено.\nПроверь, что лента включена, а Bluetooth\n"
                     "на компьютере работает, и нажми «Искать снова».",
                font=Theme.font(13), text_color=Theme.MUTED, justify="center",
            ).pack(pady=50)
            return

        for device in items:
            self._add_row(device)

    def _add_row(self, device):
        row = ctk.CTkFrame(self.list_frame, fg_color=Theme.CARD_2, corner_radius=16)
        row.pack(fill="x", padx=6, pady=5)
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            row, text=device.name or "Без названия", font=Theme.font(14, "bold"),
            text_color=Theme.TEXT if device.name else Theme.MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        ctk.CTkLabel(
            row, text=f"{device.address}   {self.signal_text(device.rssi)}",
            font=Theme.font(11, family=Theme.MONO), text_color=Theme.MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 2 if device.led else 10))
        if device.led:
            ctk.CTkLabel(
                row, text="Похоже на LED-ленту", font=Theme.font(11, "bold"),
                text_color=Theme.OK, anchor="w",
            ).grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

        is_current = device.address.upper() == self.current_address
        ctk.CTkButton(
            row, text="Текущее" if is_current else "Выбрать", width=92, height=34,
            corner_radius=12, font=Theme.font(12, "bold"),
            state="disabled" if is_current else "normal",
            fg_color=Theme.BORDER if is_current else Theme.BLUE,
            hover_color=Theme.BLUE_HOVER,
            command=lambda d=device: self._choose(d),
        ).grid(row=0, column=1, rowspan=3, padx=12)

    def _choose(self, device):
        self.on_select(device.address, device.name)
        self.close()

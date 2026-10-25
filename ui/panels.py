import customtkinter as ctk

from config import Config
from service.color import ColorUtils
from ui.theme import Theme
from ui.widgets import ColorWheel, GradientSlider, PowerButton


class Card(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(
            master, fg_color=Theme.CARD, corner_radius=26,
            border_width=1, border_color=Theme.BORDER,
        )


class HeaderPanel(ctk.CTkFrame):
    def __init__(self, master, on_status, on_settings, on_power):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=Config.APP_NAME, font=Theme.font(30, "bold"), text_color=Theme.TEXT
        ).grid(row=0, column=0, sticky="w")

        self.status_button = ctk.CTkButton(
            self, text="●  Не подключено", height=30, corner_radius=15, width=190,
            fg_color=Theme.CARD, hover_color=Theme.CARD_2,
            border_width=1, border_color=Theme.BORDER,
            text_color=Theme.MUTED, font=Theme.font(12, "bold"), command=on_status,
        )
        self.status_button.grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.settings_button = ctk.CTkButton(
            self, text="⚙", width=40, height=40, corner_radius=20,
            fg_color=Theme.CARD, hover_color=Theme.CARD_2,
            border_width=1, border_color=Theme.BORDER,
            text_color=Theme.MUTED, font=Theme.font(18), command=on_settings,
        )
        self.settings_button.grid(row=0, column=1, rowspan=2, padx=(0, 10))

        self.power_button = PowerButton(self, on_power, extent=64, bg=Theme.BG)
        self.power_button.grid(row=0, column=2, rowspan=2)

    def set_status(self, text, color):
        self.status_button.configure(text=f"●  {text}", text_color=color)

    def set_power(self, on):
        self.power_button.set_state(on)

    def set_accent(self, color):
        self.power_button.set_accent(color)


class SettingsPanel(ctk.CTkFrame):
    def __init__(
        self, master, settings, autostart_enabled, tray_available,
        on_pick_device, on_autostart, on_tray, on_shutdown,
    ):
        super().__init__(
            master, fg_color=Theme.CARD, corner_radius=22,
            border_width=1, border_color=Theme.BORDER,
        )
        self.on_autostart = on_autostart
        self.on_tray = on_tray
        self.on_shutdown = on_shutdown

        ctk.CTkLabel(
            self, text="Устройство", font=Theme.font(12), text_color=Theme.MUTED
        ).pack(anchor="w", padx=18, pady=(14, 4))

        device = ctk.CTkFrame(self, fg_color=Theme.CARD_2, corner_radius=16)
        device.pack(fill="x", padx=18)
        device.grid_columnconfigure(0, weight=1)
        self.device_name = ctk.CTkLabel(
            device, text="", font=Theme.font(14, "bold"), text_color=Theme.TEXT, anchor="w"
        )
        self.device_name.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        self.device_address = ctk.CTkLabel(
            device, text="", font=Theme.font(11, family=Theme.MONO),
            text_color=Theme.MUTED, anchor="w",
        )
        self.device_address.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))
        ctk.CTkButton(
            device, text="Сменить", width=90, height=34, corner_radius=12,
            fg_color=Theme.BLUE, hover_color=Theme.BLUE_HOVER,
            font=Theme.font(12, "bold"), command=on_pick_device,
        ).grid(row=0, column=1, rowspan=2, padx=12)

        self.autostart_switch = self._make_switch(
            "Запускать вместе с Windows (в трее)", self._autostart_changed,
            autostart_enabled, Config.IS_WIN, "Автозапуск (только Windows)", top=16,
        )
        self.tray_switch = self._make_switch(
            "Крестик сворачивает в трей", self._tray_changed,
            settings.close_to_tray, tray_available, "Трей недоступен (pip install pystray)",
        )
        self.shutdown_switch = self._make_switch(
            "Выключать ленту при завершении работы ПК", self._shutdown_changed,
            settings.off_on_shutdown, Config.IS_WIN,
            "Выключение при завершении ПК (только Windows)",
        )

        self.debug_label = ctk.CTkLabel(
            self, text="Отправленные пакеты появятся здесь",
            font=Theme.font(11, family=Theme.MONO), text_color=Theme.MUTED,
            justify="left", anchor="w", wraplength=330,
        )
        self.debug_label.pack(fill="x", padx=18, pady=(14, 14))

        self.set_device(settings.name, settings.address)

    def _make_switch(self, text, command, value, available, unavailable_text, top=12):
        switch = ctk.CTkSwitch(
            self, text=text, font=Theme.font(13), text_color=Theme.TEXT,
            progress_color=Theme.BLUE, command=command,
        )
        switch.pack(anchor="w", padx=18, pady=(top, 0))
        if value:
            switch.select()
        if not available:
            switch.configure(state="disabled", text=unavailable_text)
        return switch

    def _autostart_changed(self):
        wanted = bool(self.autostart_switch.get())
        if not self.on_autostart(wanted):
            if wanted:
                self.autostart_switch.deselect()
            else:
                self.autostart_switch.select()
            self.show_debug("Не удалось изменить автозапуск", error=True)

    def _tray_changed(self):
        self.on_tray(bool(self.tray_switch.get()))

    def _shutdown_changed(self):
        self.on_shutdown(bool(self.shutdown_switch.get()))

    def set_device(self, name, address):
        if address:
            self.device_name.configure(text=name or "Без названия")
            self.device_address.configure(text=address)
        else:
            self.device_name.configure(text="Не выбрано")
            self.device_address.configure(text="Нажми «Сменить», чтобы найти ленту")

    def show_debug(self, text, error=False):
        self.debug_label.configure(
            text=text, text_color=Theme.ERR if error else Theme.MUTED
        )


class ColorPanel(Card):
    def __init__(self, master, on_wheel, on_preset):
        super().__init__(master)
        self.on_preset = on_preset

        self.wheel = ColorWheel(self, 320, on_wheel, bg=Theme.CARD)
        self.wheel.pack(pady=(18, 6))

        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(fill="x", padx=24)
        self.swatch = ctk.CTkFrame(
            info, width=34, height=34, corner_radius=17, fg_color="#ff0000"
        )
        self.swatch.pack(side="left")
        self.swatch.pack_propagate(False)
        texts = ctk.CTkFrame(info, fg_color="transparent")
        texts.pack(side="left", padx=12)
        self.hex_label = ctk.CTkLabel(
            texts, text="#FF0000", font=Theme.font(17, "bold"),
            text_color=Theme.TEXT, anchor="w",
        )
        self.hex_label.pack(anchor="w")
        self.info_label = ctk.CTkLabel(
            texts, text="", font=Theme.font(12), text_color=Theme.MUTED, anchor="w"
        )
        self.info_label.pack(anchor="w")

        presets = ctk.CTkFrame(self, fg_color="transparent")
        presets.pack(pady=(14, 20))
        self.preset_buttons = []
        for _name, hue, sat in Config.PRESETS:
            color = ColorUtils.hsv_hex(hue, sat, 100)
            button = ctk.CTkButton(
                presets, text="", width=30, height=30, corner_radius=15,
                fg_color=color, hover_color=color,
                border_width=3, border_color=Theme.CARD,
                command=lambda h=hue, s=sat: self.on_preset(h, s),
            )
            button.pack(side="left", padx=4)
            self.preset_buttons.append((button, hue, sat))

    def update_color(self, hue, sat, accent):
        self.wheel.set(hue, sat)
        self.swatch.configure(fg_color=accent)
        self.hex_label.configure(text=accent.upper())
        self.info_label.configure(
            text=f"Оттенок {round(hue)}°  ·  Насыщенность {round(sat)}%"
        )
        for button, preset_hue, preset_sat in self.preset_buttons:
            hue_gap = abs((hue - preset_hue + 180) % 360 - 180)
            selected = abs(sat - preset_sat) <= 3 and (preset_sat == 0 or hue_gap <= 3)
            button.configure(border_color="#ffffff" if selected else Theme.CARD)


class BrightnessPanel(Card):
    def __init__(self, master, value, on_change):
        super().__init__(master)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(18, 0))
        ctk.CTkLabel(
            head, text="Яркость", font=Theme.font(14), text_color=Theme.MUTED
        ).pack(side="left")
        self.value_label = ctk.CTkLabel(
            head, text=f"{round(value)}%", font=Theme.font(26, "bold"), text_color=Theme.TEXT
        )
        self.value_label.pack(side="right")
        self.slider = GradientSlider(self, 340, on_change, bg=Theme.CARD, value=value)
        self.slider.pack(pady=(4, 16))

    def update_value(self, value, accent):
        self.value_label.configure(text=f"{round(value)}%")
        self.slider.set_accent(accent)

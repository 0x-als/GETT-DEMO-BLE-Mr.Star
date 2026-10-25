import math
import tkinter as tk

from PIL import ImageTk

from service.color import ColorUtils
from ui.raster import Raster
from ui.theme import Theme


class PowerButton(tk.Canvas):
    def __init__(self, master, command, extent=54, bg=Theme.BG):
        self.k = Theme.scale_of(master)
        self.side = round(extent * self.k)
        super().__init__(
            master, width=self.side, height=self.side, bg=bg,
            highlightthickness=0, cursor="hand2",
        )
        self.command = command
        self.backdrop = bg
        self.on = False
        self.accent = "#ff3b30"
        self.hover = False
        self._photo = None
        self._image = self.create_image(self.side / 2, self.side / 2)
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.redraw()

    def _set_hover(self, value):
        self.hover = value
        self.redraw()

    def _click(self, _event):
        self.on = not self.on
        self.redraw()
        self.command(self.on)

    def set_state(self, on):
        if on != self.on:
            self.on = on
            self.redraw()

    def set_accent(self, color):
        if color != self.accent:
            self.accent = color
            if self.on:
                self.redraw()

    def redraw(self):
        image = Raster.power(
            self.side, self.k, self.backdrop, self.accent, self.on, self.hover
        )
        self._photo = ImageTk.PhotoImage(image)
        self.itemconfigure(self._image, image=self._photo)


class ColorWheel(tk.Canvas):
    def __init__(self, master, extent, command, bg=Theme.CARD):
        self.k = Theme.scale_of(master)
        self.side = round(extent * self.k)
        super().__init__(
            master, width=self.side, height=self.side, bg=bg,
            highlightthickness=0, cursor="hand2",
        )
        self.cx = self.cy = self.side / 2
        self.radius = self.side / 2 - 16 * self.k
        self.command = command
        self.backdrop = bg
        self._dragging = False
        self._knob_color = None
        self._knob_photo = None

        self._wheel_photo = ImageTk.PhotoImage(
            Raster.wheel(self.side, self.radius, self.k, bg)
        )
        self.create_image(self.cx, self.cy, image=self._wheel_photo)
        self._knob = self.create_image(self.cx, self.cy)

        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_dragging", False))
        self.set(0, 100)

    def _press(self, event):
        if math.hypot(event.x - self.cx, event.y - self.cy) <= self.radius + 16 * self.k:
            self._dragging = True
            self._move(event)

    def _move(self, event):
        if not self._dragging:
            return
        dx, dy = event.x - self.cx, event.y - self.cy
        distance = math.hypot(dx, dy)
        hue = math.degrees(math.atan2(dx, -dy)) % 360 if distance > 0 else 0
        sat = min(1.0, distance / self.radius) * 100
        if sat < 5:
            sat = 0
        self.set(hue, sat)
        self.command(hue, sat)

    def set(self, hue, sat):
        angle = math.radians(hue)
        distance = self.radius * sat / 100
        x = self.cx + distance * math.sin(angle)
        y = self.cy - distance * math.cos(angle)
        color = ColorUtils.hsv_hex(hue, sat, 100)
        if color != self._knob_color:
            self._knob_color = color
            image = Raster.knob(color, round(26 * self.k), max(2, round(2.6 * self.k)))
            self._knob_photo = ImageTk.PhotoImage(image)
            self.itemconfigure(self._knob, image=self._knob_photo)
        self.coords(self._knob, x, y)


class GradientSlider(tk.Canvas):
    def __init__(self, master, length, command, bg=Theme.CARD, vmin=1, vmax=100, value=50):
        self.k = Theme.scale_of(master)
        self.length_px = round(length * self.k)
        self.height_px = round(40 * self.k)
        self.pad = round(18 * self.k)
        super().__init__(
            master, width=self.length_px, height=self.height_px, bg=bg,
            highlightthickness=0, cursor="hand2",
        )
        self.command = command
        self.backdrop = bg
        self.vmin, self.vmax, self.value = vmin, vmax, value
        self.accent = "#ff3b30"
        self._track_photo = None
        self._knob_photo = None
        self._track = self.create_image(self.length_px / 2, self.height_px / 2)
        self._knob = self.create_image(0, 0)
        self._render()
        self._place()
        self.bind("<Button-1>", self._event)
        self.bind("<B1-Motion>", self._event)

    def _render(self):
        track = Raster.track(
            self.length_px - 2 * self.pad, max(6, round(8 * self.k)),
            self.k, self.accent, self.backdrop,
        )
        self._track_photo = ImageTk.PhotoImage(track)
        self.itemconfigure(self._track, image=self._track_photo)
        knob = Raster.knob(self.accent, round(24 * self.k), max(2, round(3 * self.k)))
        self._knob_photo = ImageTk.PhotoImage(knob)
        self.itemconfigure(self._knob, image=self._knob_photo)

    def _place(self):
        ratio = (self.value - self.vmin) / (self.vmax - self.vmin)
        x = self.pad + ratio * (self.length_px - 2 * self.pad)
        self.coords(self._knob, x, self.height_px / 2)

    def _event(self, event):
        span = self.length_px - 2 * self.pad
        ratio = max(0.0, min(1.0, (event.x - self.pad) / span))
        self.value = self.vmin + ratio * (self.vmax - self.vmin)
        self._place()
        self.command(self.value)

    def set_value(self, value):
        self.value = max(self.vmin, min(self.vmax, value))
        self._place()

    def set_accent(self, color):
        if color != self.accent:
            self.accent = color
            self._render()


class ColorDot(tk.Canvas):
    def __init__(self, master, color, command, extent=28, bg=Theme.CARD):
        self.k = Theme.scale_of(master)
        self.side = round(extent * self.k)
        super().__init__(
            master, width=self.side, height=self.side, bg=bg,
            highlightthickness=0, cursor="hand2",
        )
        self.color = color
        self.command = command
        self.backdrop = bg
        self.selected = False
        self.hover = False
        self._photo = None
        self._image = self.create_image(self.side / 2, self.side / 2)
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._render()

    def _set_hover(self, value):
        self.hover = value
        self._render()

    def set_selected(self, selected):
        if selected != self.selected:
            self.selected = selected
            self._render()

    def _render(self):
        image = Raster.dot(
            self.side, self.k, self.color, self.selected, self.hover, self.backdrop
        )
        self._photo = ImageTk.PhotoImage(image)
        self.itemconfigure(self._image, image=self._photo)

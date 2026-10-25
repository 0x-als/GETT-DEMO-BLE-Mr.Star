import colorsys
import math

from PIL import Image, ImageDraw, ImageFilter

from service.color import ColorUtils
from ui.theme import Theme

_RESAMPLING = getattr(Image, "Resampling", Image)
RESAMPLE = _RESAMPLING.LANCZOS
SMOOTH = _RESAMPLING.BILINEAR
SS = 4


class Raster:
    @staticmethod
    def rgba(color, alpha=255):
        r, g, b = ColorUtils.hex_to_rgb(color)
        return (r, g, b, alpha)

    @staticmethod
    def shrink(image, width, height):
        return image.resize((width, height), RESAMPLE)

    @classmethod
    def flatten(cls, image, background):
        base = Image.new("RGBA", image.size, cls.rgba(background))
        return Image.alpha_composite(base, image).convert("RGB")

    @staticmethod
    def blank(width, height):
        return Image.new("RGBA", (width * SS, height * SS), (0, 0, 0, 0))

    @classmethod
    def knob(cls, color, diameter, ring):
        pad = max(4, round(diameter * 0.4))
        total = diameter + 2 * pad
        big = total * SS
        center = big / 2
        radius = diameter * SS / 2

        shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        drop = diameter * 0.10 * SS
        ImageDraw.Draw(shadow).ellipse(
            [center - radius, center - radius + drop, center + radius, center + radius + drop],
            fill=(0, 0, 0, 150),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(diameter * 0.12 * SS))

        top = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(top)
        draw.ellipse(
            [center - radius, center - radius, center + radius, center + radius],
            fill=cls.rgba(Theme.TEXT),
        )
        inner = radius - ring * SS
        draw.ellipse(
            [center - inner, center - inner, center + inner, center + inner],
            fill=cls.rgba(color),
        )
        return cls.shrink(Image.alpha_composite(shadow, top), total, total)

    @classmethod
    def power(cls, side, k, backdrop, accent, on, hover):
        center = side / 2
        radius = side / 2 - 8 * k
        base = Image.new("RGBA", (side, side), cls.rgba(backdrop))

        if on:
            glow = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            ImageDraw.Draw(glow).ellipse(
                [center - radius, center - radius, center + radius, center + radius],
                fill=cls.rgba(accent, 120),
            )
            glow = glow.filter(ImageFilter.GaussianBlur(3.5 * k))
            base = Image.alpha_composite(base, glow)
            fill = ColorUtils.mix(backdrop, accent, 0.14)
            outline = accent
            icon = accent
        else:
            fill = Theme.CARD_2 if hover else Theme.CARD
            outline = Theme.BORDER
            icon = Theme.MUTED

        shape = cls.blank(side, side)
        draw = ImageDraw.Draw(shape)
        c, r = center * SS, radius * SS
        draw.ellipse(
            [c - r, c - r, c + r, c + r],
            fill=cls.rgba(fill), outline=cls.rgba(outline),
            width=max(1, round(1.6 * k * SS)),
        )

        color = cls.rgba(icon)
        stroke = max(2, round(2.2 * k * SS))
        arc_r = r * 0.42
        reach = arc_r + stroke / 2
        box = [c - reach, c - reach, c + reach, c + reach]
        draw.arc(box, start=310, end=360, fill=color, width=stroke)
        draw.arc(box, start=0, end=230, fill=color, width=stroke)
        for angle in (310, 230):
            x = c + arc_r * math.cos(math.radians(angle))
            y = c + arc_r * math.sin(math.radians(angle))
            draw.ellipse(
                [x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2], fill=color
            )
        top, bottom = c - arc_r * 1.18, c - arc_r * 0.05
        draw.line([(c, top), (c, bottom)], fill=color, width=stroke)
        for y in (top, bottom):
            draw.ellipse(
                [c - stroke / 2, y - stroke / 2, c + stroke / 2, y + stroke / 2], fill=color
            )

        shape = cls.shrink(shape, side, side)
        return Image.alpha_composite(base, shape).convert("RGB")

    @classmethod
    def wheel(cls, side, radius, k, backdrop):
        hues = [
            tuple(int(v * 255) for v in colorsys.hsv_to_rgb(i / 360, 1, 1)) for i in range(360)
        ]
        image = Image.new("RGB", (side, side), (0, 0, 0))
        pixels = image.load()
        center = side / 2
        limit = radius + 2
        for y in range(side):
            dy = y - center
            span = limit * limit - dy * dy
            if span < 0:
                continue
            reach = math.sqrt(span)
            for x in range(max(0, int(center - reach)), min(side, int(center + reach) + 1)):
                dx = x - center
                sat = min(1.0, math.hypot(dx, dy) / radius)
                hr, hg, hb = hues[int(math.degrees(math.atan2(dx, -dy)) % 360) % 360]
                pixels[x, y] = (
                    int(255 - sat * (255 - hr)),
                    int(255 - sat * (255 - hg)),
                    int(255 - sat * (255 - hb)),
                )

        c, r = center * SS, radius * SS
        mask = Image.new("L", (side * SS, side * SS), 0)
        ImageDraw.Draw(mask).ellipse([c - r, c - r, c + r, c + r], fill=255)
        mask = mask.resize((side, side), RESAMPLE)
        wheel = image.convert("RGBA")
        wheel.putalpha(mask)

        ring = cls.blank(side, side)
        rr = (radius + 8 * k) * SS
        ImageDraw.Draw(ring).ellipse(
            [c - rr, c - rr, c + rr, c + rr],
            outline=cls.rgba(Theme.BORDER), width=max(1, round(1.5 * k * SS)),
        )
        ring = cls.shrink(ring, side, side)

        base = Image.new("RGBA", (side, side), cls.rgba(backdrop))
        base = Image.alpha_composite(base, ring)
        base = Image.alpha_composite(base, wheel)
        return base.convert("RGB")

    @classmethod
    def track(cls, width, height, k, accent, backdrop):
        big_w, big_h = width * SS, height * SS
        left = ColorUtils.mix(Theme.TRACK, accent, 0.10)

        outer = Image.new("L", (big_w, big_h), 0)
        ImageDraw.Draw(outer).rounded_rectangle(
            [0, 0, big_w - 1, big_h - 1], radius=big_h // 2, fill=255
        )
        inset = max(SS, round(k * SS))
        inner = Image.new("L", (big_w, big_h), 0)
        ImageDraw.Draw(inner).rounded_rectangle(
            [inset, inset, big_w - 1 - inset, big_h - 1 - inset],
            radius=(big_h - 2 * inset) // 2, fill=255,
        )

        gradient = Image.new("RGB", (width, 1))
        for x in range(width):
            gradient.putpixel(
                (x, 0), ColorUtils.hex_to_rgb(ColorUtils.mix(left, accent, x / (width - 1)))
            )
        gradient = gradient.resize((big_w, big_h), SMOOTH)

        layer = Image.new("RGBA", (big_w, big_h), cls.rgba(Theme.BORDER))
        layer.putalpha(outer)
        layer.paste(gradient, (0, 0), inner)
        return cls.flatten(cls.shrink(layer, width, height), backdrop)

    @classmethod
    def dot(cls, side, k, color, selected, hover, backdrop):
        layer = cls.blank(side, side)
        draw = ImageDraw.Draw(layer)
        c = side * SS / 2
        if selected:
            outer = (side / 2 - 1.5 * k) * SS
            draw.ellipse(
                [c - outer, c - outer, c + outer, c + outer],
                outline=cls.rgba(Theme.TEXT), width=max(1, round(1.8 * k * SS)),
            )
            inner = outer - 4.2 * k * SS
            draw.ellipse([c - inner, c - inner, c + inner, c + inner], fill=cls.rgba(color))
        else:
            radius = (side / 2 - 4 * k + (1.5 * k if hover else 0)) * SS
            draw.ellipse(
                [c - radius, c - radius, c + radius, c + radius],
                fill=cls.rgba(color),
                outline=cls.rgba(ColorUtils.mix(color, "#ffffff", 0.22)),
                width=max(1, round(k * SS)),
            )
        return cls.flatten(cls.shrink(layer, side, side), backdrop)

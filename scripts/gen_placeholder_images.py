#!/usr/bin/env python3
"""Generate placeholder app/driver PNGs at Homey's required sizes.

These are DEV placeholders so the app validates and shows something recognisable.
They are intentionally distinct (app vs each driver) per the App Store guideline
that app image != driver image. Real artwork is a later polish step.
"""

import os

from PIL import Image, ImageDraw

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

GREEN = (34, 181, 115)
GREEN_D = (18, 120, 74)
INK = (255, 255, 255)


def _bg(w, h, top, bot):
    img = Image.new("RGB", (w, h), top)
    for y in range(h):
        t = y / max(1, h - 1)
        img.paste(
            tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)),
            (0, y, w, y + 1),
        )
    return img


def _poly(d, pts, cx, cy, s, fill):
    d.polygon([(cx + x * s, cy + y * s) for x, y in pts], fill=fill)


def house(d, cx, cy, s):
    _poly(d, [(0, -3.8), (3.6, -1), (3.6, -0.6), (2.4, -0.6), (2.4, 3.4),
              (0.8, 3.4), (0.8, 1.0), (-0.8, 1.0), (-0.8, 3.4), (-2.4, 3.4),
              (-2.4, -0.6), (-3.6, -0.6), (-3.6, -1)], cx, cy, s, INK)
    _poly(d, [(0.4, -1.9), (-1.2, 0.7), (-0.1, 0.7), (-0.5, 2.4),
              (1.4, -0.4), (0.2, -0.4)], cx, cy, s, GREEN_D)


def station(d, cx, cy, s):
    d.rounded_rectangle([cx - 3.2 * s, cy - 2.4 * s, cx + 3.2 * s, cy - 1.4 * s],
                        radius=0.3 * s, fill=INK)
    d.rounded_rectangle([cx - 2.8 * s, cy - 1.4 * s, cx + 2.8 * s, cy + 3.0 * s],
                        radius=0.6 * s, fill=INK)
    _poly(d, [(0.2, -0.8), (-1.0, 1.2), (-0.1, 1.2), (-0.5, 2.8),
              (1.1, 0.6), (0.1, 0.6)], cx, cy, s, GREEN_D)


def panel(d, cx, cy, s):
    d.rounded_rectangle([cx - 2.6 * s, cy - 3.8 * s, cx + 2.6 * s, cy + 3.8 * s],
                        radius=0.5 * s, fill=INK)
    for row in range(3):
        yy = cy + (-2.4 + row * 1.5) * s
        d.rounded_rectangle([cx - 1.7 * s, yy, cx - 0.5 * s, yy + 0.9 * s],
                            radius=0.2 * s, fill=GREEN_D)
        d.rounded_rectangle([cx + 0.5 * s, yy, cx + 1.7 * s, yy + 0.9 * s],
                            radius=0.2 * s, fill=GREEN_D)
    d.rounded_rectangle([cx - 1.7 * s, cy + 2.1 * s, cx + 1.7 * s, cy + 2.8 * s],
                        radius=0.2 * s, fill=GREEN_D)


def make(path, w, h, glyph, top=GREEN, bot=GREEN_D):
    img = _bg(w, h, top, bot)
    d = ImageDraw.Draw(img)
    glyph(d, w * 0.5, h * 0.5, min(w, h) / 11.0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    print("wrote", os.path.relpath(path, ROOT))


def main():
    # App images.
    for name, (w, h) in {"small": (250, 175), "large": (500, 350),
                          "xlarge": (1000, 700)}.items():
        make(os.path.join(ROOT, "assets", "images", f"{name}.png"), w, h, house)
    # Driver images (square).
    for drv, glyph, tint in (
        ("deltaproultrax", station, ((40, 190, 130), (14, 96, 60))),
        ("smartpanel3", panel, ((30, 170, 150), (12, 90, 80))),
    ):
        for name, sz in {"small": 75, "large": 500, "xlarge": 1000}.items():
            make(os.path.join(ROOT, "drivers", drv, "assets", "images", f"{name}.png"),
                 sz, sz, glyph, top=tint[0], bot=tint[1])


if __name__ == "__main__":
    main()

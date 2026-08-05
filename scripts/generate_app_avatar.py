#!/usr/bin/env python3
"""Generate the square WeChat mini-program avatar used by the ERP project."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


CANVAS_SIZE = 1024
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/Dengb.ttf"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)


def interpolate(start: tuple[int, int, int], end: tuple[int, int, int], amount: float):
    return tuple(round(a + (b - a) * amount) for a, b in zip(start, end, strict=True))


def choose_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    raise FileNotFoundError("No supported bold Chinese font was found in C:/Windows/Fonts")


def create_avatar(size: int = CANVAS_SIZE) -> Image.Image:
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    top_left = (8, 61, 50)
    bottom_right = (24, 126, 101)

    # A restrained diagonal jade gradient keeps the mark legible at icon size.
    for y in range(size):
        for x in range(size):
            amount = (x + y) / (2 * (size - 1))
            pixels[x, y] = interpolate(top_left, bottom_right, amount)

    # Soft central glow without introducing busy detail.
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    margin = round(size * 0.14)
    glow_draw.ellipse(
        (margin, margin, size - margin, size - margin),
        fill=(116, 210, 181, 48),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(round(size * 0.11)))
    image = Image.alpha_composite(image.convert("RGBA"), glow)

    ornament = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ornament_draw = ImageDraw.Draw(ornament)
    center = size / 2

    # Industrial dial geometry: visible on a large icon, quiet when reduced.
    for radius, alpha, width in (
        (size * 0.365, 45, size * 0.0045),
        (size * 0.325, 24, size * 0.0025),
    ):
        box = (center - radius, center - radius, center + radius, center + radius)
        ornament_draw.ellipse(box, outline=(207, 232, 224, alpha), width=max(2, round(width)))

    accent = (231, 194, 112, 210)
    dot_radius = size * 0.012
    dial_radius = size * 0.365
    for angle in (0, 90, 180, 270):
        radians = math.radians(angle)
        x = center + math.cos(radians) * dial_radius
        y = center + math.sin(radians) * dial_radius
        ornament_draw.ellipse(
            (x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius),
            fill=accent,
        )

    image = Image.alpha_composite(image, ornament)

    # Render the exact Chinese character with a subtle shadow and warm white ink.
    text = "东"
    font = choose_font(round(size * 0.53))
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = measure.textbbox((0, 0), text, font=font)
    text_width = right - left
    text_height = bottom - top
    x = (size - text_width) / 2 - left
    y = (size - text_height) / 2 - top - size * 0.012

    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.text((x, y + size * 0.018), text, font=font, fill=(0, 27, 22, 175))
    shadow = shadow.filter(ImageFilter.GaussianBlur(round(size * 0.014)))
    image = Image.alpha_composite(image, shadow)

    text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_draw.text((x, y), text, font=font, fill=(248, 246, 236, 255))
    image = Image.alpha_composite(image, text_layer)

    return image.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("wechat-miniprogram/assets/app-avatar-dong.png"),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    create_avatar().save(args.output, format="PNG", optimize=True)
    print(f"Generated {args.output} ({CANVAS_SIZE}x{CANVAS_SIZE})")


if __name__ == "__main__":
    main()

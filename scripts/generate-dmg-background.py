#!/usr/bin/env python3
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont


WIDTH = 720
HEIGHT = 480


def load_font(candidates, size):
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: generate-dmg-background.py <output-path>", file=sys.stderr)
        return 1

    output_path = Path(sys.argv[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    title_font = load_font(
        [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ],
        34,
    )
    body_font = load_font(
        [
            "/System/Library/Fonts/SFNS.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ],
        18,
    )
    footer_font = load_font(
        [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/SFNS.ttf",
        ],
        17,
    )

    image = Image.new("RGBA", (WIDTH, HEIGHT), "#f7f4f7")
    draw = ImageDraw.Draw(image)

    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r = int(246 - (ratio * 10))
        g = int(242 - (ratio * 7))
        b = int(246 - (ratio * 2))
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b, 255))

    panel = Image.new("RGBA", (WIDTH - 72, HEIGHT - 84), (255, 255, 255, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        [(0, 0), (panel.width - 1, panel.height - 1)],
        radius=28,
        fill=(255, 255, 255, 228),
        outline=(231, 216, 225, 255),
        width=2,
    )
    panel = panel.filter(ImageFilter.GaussianBlur(radius=0.4))
    image.alpha_composite(panel, (36, 28))

    header = "Drag to Applications"
    header_box = draw.textbbox((0, 0), header, font=title_font)
    header_x = (WIDTH - (header_box[2] - header_box[0])) // 2
    draw.text((header_x, 54), header, font=title_font, fill="#4c2035")

    subheader = "Apple Music Downloader"
    sub_box = draw.textbbox((0, 0), subheader, font=body_font)
    sub_x = (WIDTH - (sub_box[2] - sub_box[0])) // 2
    draw.text((sub_x, 98), subheader, font=body_font, fill="#8e5b72")

    hint = "Packaged app, Applications shortcut, and first-launch note"
    hint_box = draw.textbbox((0, 0), hint, font=body_font)
    hint_x = (WIDTH - (hint_box[2] - hint_box[0])) // 2
    draw.text((hint_x, 208), hint, font=body_font, fill="#c34f7a")

    footer = "Modified from the Gamdl project for macOS desktop use"
    footer_box = draw.textbbox((0, 0), footer, font=footer_font)
    footer_x = (WIDTH - (footer_box[2] - footer_box[0])) // 2
    draw.text((footer_x, HEIGHT - 52), footer, font=footer_font, fill="#7b5a69")

    image.save(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

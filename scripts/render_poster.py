#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import http.server
import os
import shutil
import socketserver
import subprocess
import tempfile
import threading
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HTML = ROOT / "marketing/poster/index.html"
DEFAULT_OUTPUT = ROOT / "marketing/poster/export/poster-final-hidpi.png"
DEFAULT_VIEWPORT = (1200, 1600)
DEFAULT_POSTER = (1080, 1350)
DEFAULT_RADIUS = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the marketing poster from HTML at a deterministic high DPI."
    )
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="Poster HTML file.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="PNG output path.")
    parser.add_argument(
        "--chrome-path",
        type=Path,
        default=None,
        help="Explicit Chrome/Chromium binary path.",
    )
    parser.add_argument(
        "--render-scale",
        type=int,
        default=2,
        help="Device scale factor used during browser rendering.",
    )
    parser.add_argument(
        "--output-scale",
        type=int,
        default=None,
        help="Output scale relative to poster CSS size. Defaults to render scale.",
    )
    parser.add_argument(
        "--viewport-width",
        type=int,
        default=DEFAULT_VIEWPORT[0],
        help="Headless browser viewport width in CSS pixels.",
    )
    parser.add_argument(
        "--viewport-height",
        type=int,
        default=DEFAULT_VIEWPORT[1],
        help="Headless browser viewport height in CSS pixels.",
    )
    parser.add_argument(
        "--poster-width",
        type=int,
        default=DEFAULT_POSTER[0],
        help="Poster width in CSS pixels.",
    )
    parser.add_argument(
        "--poster-height",
        type=int,
        default=DEFAULT_POSTER[1],
        help="Poster height in CSS pixels.",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=DEFAULT_RADIUS,
        help="Poster corner radius in CSS pixels.",
    )
    parser.add_argument(
        "--crop-top-adjust",
        type=int,
        default=0,
        help="Additional crop offset on Y axis in CSS pixels. Negative moves crop upward.",
    )
    parser.add_argument(
        "--crop-left-adjust",
        type=int,
        default=0,
        help="Additional crop offset on X axis in CSS pixels.",
    )
    return parser.parse_args()


def find_chrome(explicit: Path | None) -> str:
    candidates = [
        explicit,
        Path(os.environ["CHROME_PATH"]) if "CHROME_PATH" in os.environ else None,
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path)
    raise FileNotFoundError(
        "Chrome/Chromium binary not found. Pass --chrome-path or set CHROME_PATH."
    )


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


@contextlib.contextmanager
def serve_directory(directory: Path):
    handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
        *args, directory=str(directory), **kwargs
    )
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1]
        finally:
            httpd.shutdown()
            thread.join()


def capture_screenshot(
    chrome_path: str,
    url: str,
    screenshot_path: Path,
    viewport_width: int,
    viewport_height: int,
    render_scale: int,
) -> None:
    command = [
        chrome_path,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw",
        f"--force-device-scale-factor={render_scale}",
        f"--window-size={viewport_width},{viewport_height}",
        f"--screenshot={screenshot_path}",
        url,
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def apply_rounded_alpha(image: Image.Image, radius: int) -> Image.Image:
    result = image.convert("RGBA")
    if radius <= 0:
        return result
    mask = Image.new("L", result.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, result.width - 1, result.height - 1), radius=radius, fill=255)
    result.putalpha(mask)
    return result


def normalize_edge_pixels(image: Image.Image) -> Image.Image:
    result = image.convert("RGBA")
    if result.width < 2 or result.height < 2:
        return result

    # Remove 1px seams introduced by screenshot crop boundaries.
    for x in range(result.width):
        result.putpixel((x, 0), result.getpixel((x, 1)))
        result.putpixel((x, result.height - 1), result.getpixel((x, result.height - 2)))
    for y in range(result.height):
        result.putpixel((0, y), result.getpixel((1, y)))
        result.putpixel((result.width - 1, y), result.getpixel((result.width - 2, y)))
    return result


def main() -> int:
    args = parse_args()
    html_path = args.html.resolve()
    output_path = args.output.resolve()
    render_scale = args.render_scale
    output_scale = args.output_scale or render_scale

    if render_scale < 1 or output_scale < 1:
        raise ValueError("render/output scale must be >= 1")
    if not html_path.exists():
        raise FileNotFoundError(f"Poster HTML not found: {html_path}")
    if not html_path.is_relative_to(ROOT):
        raise ValueError(f"Poster HTML must live under repository root: {ROOT}")

    chrome_path = find_chrome(args.chrome_path)
    relative_html = html_path.relative_to(ROOT).as_posix()
    crop_left = (
        ((args.viewport_width - args.poster_width) // 2) + args.crop_left_adjust
    ) * render_scale
    crop_top = (
        ((args.viewport_height - args.poster_height) // 2) + args.crop_top_adjust
    ) * render_scale
    crop_right = crop_left + args.poster_width * render_scale
    crop_bottom = crop_top + args.poster_height * render_scale

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with serve_directory(ROOT) as port, tempfile.TemporaryDirectory() as temp_dir:
        screenshot_path = Path(temp_dir) / "poster-viewport.png"
        url = f"http://127.0.0.1:{port}/{relative_html}"
        capture_screenshot(
            chrome_path=chrome_path,
            url=url,
            screenshot_path=screenshot_path,
            viewport_width=args.viewport_width,
            viewport_height=args.viewport_height,
            render_scale=render_scale,
        )
        viewport = Image.open(screenshot_path).convert("RGBA")
        cropped = viewport.crop((crop_left, crop_top, crop_right, crop_bottom))
        cropped = apply_rounded_alpha(cropped, args.radius * render_scale)

        target_size = (args.poster_width * output_scale, args.poster_height * output_scale)
        if cropped.size != target_size:
            cropped = cropped.resize(target_size, Image.Resampling.LANCZOS)

        cropped = normalize_edge_pixels(cropped)
        cropped.save(output_path)
        print(output_path)
        print(f"size={cropped.size[0]}x{cropped.size[1]}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

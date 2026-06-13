#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_SVG="$ROOT_DIR/assets/macos/app-icon.svg"
OUTPUT_DIR="$ROOT_DIR/assets/macos"
MASTER_PNG="$OUTPUT_DIR/app-icon-1024.png"
ICONSET_DIR="$OUTPUT_DIR/AppIcon.iconset"
ICNS_PATH="$OUTPUT_DIR/app-icon.icns"

if [[ ! -f "$SOURCE_SVG" ]]; then
  echo "Missing source icon: $SOURCE_SVG" >&2
  exit 1
fi

mkdir -p "$ICONSET_DIR"

if [[ -f "$MASTER_PNG" ]] && sips -g pixelWidth -g pixelHeight "$MASTER_PNG" >/dev/null 2>&1; then
  :
else
  sips -s format png "$SOURCE_SVG" --out "$MASTER_PNG" >/dev/null
fi

if [[ -s "$ICNS_PATH" ]]; then
  echo "Using existing macOS icon assets:"
  echo "  $MASTER_PNG"
  echo "  $ICONSET_DIR"
  echo "  $ICNS_PATH"
  exit 0
fi

make_icon() {
  local size="$1"
  local name="$2"
  sips -z "$size" "$size" "$MASTER_PNG" --out "$ICONSET_DIR/$name" >/dev/null
}

make_icon 16 icon_16x16.png
make_icon 32 icon_16x16@2x.png
make_icon 32 icon_32x32.png
make_icon 64 icon_32x32@2x.png
make_icon 128 icon_128x128.png
make_icon 256 icon_128x128@2x.png
make_icon 256 icon_256x256.png
make_icon 512 icon_256x256@2x.png
make_icon 512 icon_512x512.png
cp "$MASTER_PNG" "$ICONSET_DIR/icon_512x512@2x.png"

iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"

echo "Generated macOS icon assets:"
echo "  $MASTER_PNG"
echo "  $ICONSET_DIR"
echo "  $ICNS_PATH"

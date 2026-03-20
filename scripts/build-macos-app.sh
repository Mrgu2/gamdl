#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build"
APP_NAME="Apple Music Downloader"
APP_PATH="$DIST_DIR/$APP_NAME.app"
DMG_PATH="$DIST_DIR/$APP_NAME.dmg"
ICON_PATH="$ROOT_DIR/assets/macos/app-icon.icns"
DMG_BACKGROUND_SCRIPT="$ROOT_DIR/scripts/generate-dmg-background.py"
DMG_NOTE_NAME="IMPORTANT - First Launch ／ 首次启动说明.txt"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install uv first."
  exit 1
fi

cd "$ROOT_DIR"
rm -rf "$BUILD_DIR" "$APP_PATH" "$DMG_PATH"
mkdir -p "$DIST_DIR"

uv sync --extra desktop-build
"$ROOT_DIR/scripts/generate-macos-icon.sh"

uv run pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ICON_PATH" \
  --collect-all webview \
  --hidden-import webview.platforms.cocoa \
  --hidden-import AppKit \
  --hidden-import WebKit \
  --add-data "README.md:." \
  gamdl/desktop_app.py

if [[ -n "${APPLE_DEVELOPER_IDENTITY:-}" ]]; then
  codesign \
    --deep \
    --force \
    --options runtime \
    --sign "$APPLE_DEVELOPER_IDENTITY" \
    "$APP_PATH"
fi

DMG_STAGE_DIR="$(mktemp -d "$BUILD_DIR/dmg-stage.XXXXXX")"
DMG_MOUNT_POINT="$(mktemp -d "$BUILD_DIR/dmg-mount.XXXXXX")"
DMG_RW_PATH="$BUILD_DIR/$APP_NAME-temp.dmg"
DMG_BACKGROUND_DIR="$DMG_STAGE_DIR/.background"
DMG_BACKGROUND_PATH="$DMG_BACKGROUND_DIR/background.png"
DMG_NOTE_PATH="$DMG_STAGE_DIR/$DMG_NOTE_NAME"

cleanup() {
  if mount | grep -Fq "on $DMG_MOUNT_POINT "; then
    hdiutil detach "$DMG_MOUNT_POINT" -force >/dev/null 2>&1 || true
  fi
  rm -rf "$DMG_STAGE_DIR" "$DMG_MOUNT_POINT"
}

trap cleanup EXIT

mkdir -p "$DMG_BACKGROUND_DIR"
uv run python "$DMG_BACKGROUND_SCRIPT" "$DMG_BACKGROUND_PATH"
SetFile -a V "$DMG_BACKGROUND_DIR"

cat > "$DMG_NOTE_PATH" <<'EOF'
Apple Music Downloader
这是一个基于 Gamdl 修改的 macOS 桌面版。

为什么 macOS 可能要求你额外确认
这个构建版本目前还没有通过 Apple 的 notarization（公证）。因此即使 app 本身没有被破坏，Gatekeeper 仍然可能在第一次启动时阻止它运行。

如何打开
1. 先把 Apple Music Downloader 拖到 Applications 文件夹。
2. 打开一次应用。
3. 如果 macOS 阻止启动，先关闭弹窗，然后打开“系统设置 > 隐私与安全性”。
4. 在安全性区域找到 Apple Music Downloader，然后点击“仍要打开”。
5. 确认弹窗后，再次打开应用。

如果这个构建不是来自你信任的人，请不要打开。
项目地址：https://github.com/Mrgu2/gamdl

Apple Music Downloader
Modified from the Gamdl project for macOS desktop use.

Why macOS may ask you to confirm this app
This build has not been notarized by Apple yet. Because of that, Gatekeeper may block the first launch even though the app bundle is intact.

How to open it
1. Drag Apple Music Downloader to Applications.
2. Open the app once.
3. If macOS blocks it, close the alert and open System Settings > Privacy & Security.
4. In the Security section, click Open Anyway for Apple Music Downloader.
5. Confirm the dialog, then open the app again.

If you received this build from someone you do not trust, do not open it.
Project page: https://github.com/Mrgu2/gamdl
EOF

ditto "$APP_PATH" "$DMG_STAGE_DIR/$APP_NAME.app"
osascript <<EOF
set targetFolder to POSIX file "$DMG_STAGE_DIR" as alias
tell application "Finder"
  make new alias file at targetFolder to POSIX file "/Applications"
end tell
EOF
DMG_APPLICATIONS_ALIAS="$(find "$DMG_STAGE_DIR" -maxdepth 1 -type f ! -name "$DMG_NOTE_NAME" -print -quit)"
mv "$DMG_APPLICATIONS_ALIAS" "$DMG_STAGE_DIR/Applications"

hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DMG_STAGE_DIR" \
  -ov \
  -format UDRW \
  -fs HFS+ \
  "$DMG_RW_PATH"

hdiutil attach \
  "$DMG_RW_PATH" \
  -mountpoint "$DMG_MOUNT_POINT" \
  -noautoopen \
  -readwrite \
  -noverify >/dev/null

osascript <<EOF
set backgroundAlias to POSIX file "$DMG_MOUNT_POINT/.background/background.png" as alias
tell application "Finder"
  tell disk "$APP_NAME"
    open
    delay 1
    set theWindow to container window
    set current view of theWindow to icon view
    set toolbar visible of theWindow to false
    set statusbar visible of theWindow to false
    set bounds of theWindow to {140, 120, 860, 600}
    set theViewOptions to icon view options of theWindow
    set arrangement of theViewOptions to arranged by name
    set icon size of theViewOptions to 112
    set text size of theViewOptions to 13
    set background picture of theViewOptions to backgroundAlias
    update without registering applications
    delay 2
    close
    open
    delay 1
  end tell
end tell
EOF

chmod -Rf go-w "$DMG_MOUNT_POINT"
sync
hdiutil detach "$DMG_MOUNT_POINT" >/dev/null

hdiutil convert \
  "$DMG_RW_PATH" \
  -ov \
  -format UDZO \
  -imagekey zlib-level=9 \
  -o "$DMG_PATH" >/dev/null

rm -f "$DMG_RW_PATH"

if [[ -n "${APPLE_NOTARY_PROFILE:-}" ]]; then
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$APPLE_NOTARY_PROFILE" --wait
  xcrun stapler staple "$APP_PATH"
  xcrun stapler staple "$DMG_PATH"
fi

echo "Built app: $APP_PATH"
echo "Built dmg: $DMG_PATH"

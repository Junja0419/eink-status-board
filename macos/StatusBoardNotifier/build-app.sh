#!/bin/bash
# Status Board 알림 앱(.app)을 만든다 — Apple Silicon·Intel 공용(universal), ad-hoc 서명.
#   bash macos/StatusBoardNotifier/build-app.sh
# 결과: build/Status Board.app 과 회사 Mac 으로 옮길 build/StatusBoard.zip (같은 경로에 덮어쓴다)
set -euo pipefail
cd "$(dirname "$0")"

APP="build/Status Board.app"
ZIP="build/StatusBoard.zip"
ARCHS=(--arch arm64 --arch x86_64)

swift test
swift build -c release "${ARCHS[@]}"
BIN_DIR=$(swift build -c release "${ARCHS[@]}" --show-bin-path)

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build/AppIcon.iconset
cp "$BIN_DIR/StatusBoardNotifier" "$APP/Contents/MacOS/"
cp Resources/Info.plist "$APP/Contents/"

# AppIcon.png(1024, tools/make_icons.py 가 만든다) → .icns
for size in 16 32 128 256 512; do
  sips -z $size $size Resources/AppIcon.png --out "build/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z $double $double Resources/AppIcon.png --out "build/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns build/AppIcon.iconset -o "$APP/Contents/Resources/AppIcon.icns"

codesign --force --sign - "$APP"
codesign --verify --strict "$APP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "앱: $(pwd)/$APP ($(lipo -archs "$APP/Contents/MacOS/StatusBoardNotifier"))"
echo "옮길 파일: $(pwd)/$ZIP"

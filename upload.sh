#!/usr/bin/env bash
# ESP32-S3 펌웨어 컴파일 + 업로드
#   사전 준비: cp eink-status-board.ino.sample eink-status-board.ino 후
#             WIFI_SSID / WIFI_PASSWORD / WS_HOST / DEVICE_TOKEN 수정
#   시리얼 포트가 다르면:   PORT=/dev/cu.usbserial-XXXX bash upload.sh
#   컴파일만 확인하려면:    NO_UPLOAD=1 bash upload.sh
# 보드 옵션: OPI PSRAM, Huge APP 파티션, 업로드 속도 115200
set -euo pipefail
cd "$(dirname "$0")"

FQBN="esp32:esp32:esp32s3:UploadSpeed=115200,PSRAM=opi,PartitionScheme=huge_app"

if [ ! -f eink-status-board.ino ]; then
  echo "eink-status-board.ino 가 없습니다. 먼저 .ino.sample 을 복사해 크리덴셜을 채우세요:" >&2
  echo "  cp eink-status-board.ino.sample eink-status-board.ino" >&2
  exit 1
fi

# 설정 줄(const char* 이름 = "값";)에 placeholder 가 남아 있으면 중단
if grep -qE 'const char\*[[:space:]]+(WIFI_SSID|WIFI_PASSWORD|WS_HOST|DEVICE_TOKEN)[[:space:]]*=[[:space:]]*"(YOUR_[A-Z_]+|SERVER_IP)"' eink-status-board.ino; then
  echo "eink-status-board.ino 에 아직 채우지 않은 값(YOUR_... / SERVER_IP)이 있습니다." >&2
  exit 1
fi

# arduino-cli 내장 ctags 는 x86_64 전용이라 Rosetta 없는 Apple Silicon 에서는 실행되지 않는다.
# 그 경우에만 프로토타입 자동 생성을 건너뛰는 대용품(tools/ctags-shim)으로 바꾼다.
EXTRA_ARGS=()
BUNDLED_CTAGS="$(ls "$HOME"/Library/Arduino15/packages/builtin/tools/ctags/*/ctags 2>/dev/null | tail -1 || true)"
if [ -n "$BUNDLED_CTAGS" ] && ! "$BUNDLED_CTAGS" --version >/dev/null 2>&1; then
  echo "내장 ctags 를 실행할 수 없어(Rosetta 없음) tools/ctags-shim 으로 대체합니다."
  EXTRA_ARGS+=(--build-property "tools.ctags.path=$PWD/tools/ctags-shim")
fi

if [ -n "${NO_UPLOAD:-}" ]; then
  arduino-cli compile --fqbn "$FQBN" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} eink-status-board.ino
  echo "컴파일 성공 (NO_UPLOAD 설정으로 업로드는 건너뜀)"
  exit 0
fi

PORT="${PORT:-/dev/cu.usbserial-110}"
if [ ! -e "$PORT" ]; then
  echo "시리얼 포트 $PORT 가 없습니다. ESP32 를 USB 로 연결했는지 확인하고, 포트가 다르면" >&2
  echo "  PORT=\$(ls /dev/cu.usb* | head -1) bash upload.sh" >&2
  echo "현재 보이는 포트: $(ls /dev/cu.usb* 2>/dev/null | tr '\n' ' ')" >&2
  exit 1
fi

arduino-cli compile --upload --fqbn "$FQBN" --port "$PORT" \
  ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} eink-status-board.ino

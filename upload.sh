#!/usr/bin/env bash
# ESP32-S3 펌웨어 컴파일 + 업로드
#   사전 준비: cp eink-status-board.ino.sample eink-status-board.ino 후 Wi-Fi/서버 정보 수정
#   시리얼 포트가 다르면: PORT=/dev/cu.usbserial-XXXX bash upload.sh
# 보드 옵션: OPI PSRAM, Huge APP 파티션, 업로드 속도 115200
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f eink-status-board.ino ]; then
  echo "eink-status-board.ino 가 없습니다. 먼저 .ino.sample 을 복사해 크리덴셜을 채우세요:" >&2
  echo "  cp eink-status-board.ino.sample eink-status-board.ino" >&2
  exit 1
fi

arduino-cli compile --upload \
  --fqbn esp32:esp32:esp32s3:UploadSpeed=115200,PSRAM=opi,PartitionScheme=huge_app \
  --port "${PORT:-/dev/cu.usbserial-110}" \
  eink-status-board.ino

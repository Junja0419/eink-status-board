# ESP32-S3 보드 옵션: OPI PSRAM 활성화, Huge APP 파티션 적용, 업로드 속도 제한(115200)
arduino-cli compile --upload \
  --fqbn esp32:esp32:esp32s3:UploadSpeed=115200,PSRAM=opi,PartitionScheme=huge_app \
  --port /dev/cu.usbserial-110 \
  eink-status-board.ino
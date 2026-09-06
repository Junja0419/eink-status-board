# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ESP32-S3 + CrowPanel 3.7" E-paper (UC8253, 416x240, 1-bit BW) 실시간 상태 표시 시스템.
FastAPI 서버가 이미지를 1-bit 바이너리(12,480 bytes)로 렌더링하여 WebSocket으로 ESP32에 푸시한다.

## Commands

### Server
```bash
cd server
pip install -r requirements.txt
python main.py                  # 0.0.0.0:5000. 개발 시 SERVER_RELOAD=true python main.py
# or: uvicorn main:app --host 0.0.0.0 --port 5000
```
Admin UI: http://localhost:5000/admin

수동 검증(자동 테스트 없음): 서버를 띄운 뒤 `GET /status`, 이미지 업로드 → `POST /api/presets/{id}/activate`, `ws://host/ws` 접속 시 12,480바이트 수신 확인.

### Firmware
```bash
# arduino-cli 사전 설치 필요. ESP32 보드 패키지 + WebSockets 라이브러리 설치:
arduino-cli core install esp32:esp32
arduino-cli lib install WebSockets
# EPaperDrive 라이브러리는 Elecrow에서 수동 다운로드하여 Arduino libraries 폴더에 복사

bash upload.sh    # compile + upload (esp32s3, OPI PSRAM, huge_app partition)
```

## Architecture

```
Client (Admin HTML / Apple Shortcuts / curl)
  │  POST /api/presets/{id}/activate
  │  POST /api/shortcuts/activate (by name)
  ▼
server/main.py (FastAPI)
  │  Pillow: RGB → Floyd-Steinberg dithered 1-bit
  │  → ROTATE_90 + FLIP_LEFT_RIGHT (하드웨어 스캔 방향 보정)
  │  → 12,480 bytes packed binary
  ▼
WebSocket /ws → broadcast to all ESP32 clients
  ▼
eink-status-board.ino (ESP32-S3)
  │  memcpy to frameBuffer
  │  EPD_FastInit → EPD_Display → EPD_Update → EPD_DeepSleep
  ▼
CrowPanel 3.7" E-paper
```

### Server (`server/main.py`)
- **단일 파일 구조**: 모든 API, WebSocket 핸들러, 이미지 렌더링이 `main.py` 하나에 있음
- **전역 상태**: `current_frame_bytes`, `current_display_image`, `current_status_text` — 새 ESP32 접속 시 즉시 최신 프레임 전송에 사용. 활성화 시 세 값 교체 + 브로드캐스트를 `_activate_lock` 안에서 한 단위로 수행
- **ConnectionManager**: WebSocket 클라이언트 목록 관리, `asyncio.gather`로 동시 broadcast, 실패 클라이언트 자동 제거
- **프리셋 저장**: `server/data/presets.json` (JSON 파일, git 제외), 이미지는 `server/data/images/{id}.png`. 읽기-수정-쓰기는 `_presets_lock`으로 직렬화하고 임시 파일 + `os.replace`로 원자적 저장
- **프리셋 이름은 유일 키**: 단축어 API가 이름으로 조회하므로 생성 시 중복 이름은 409, 줄바꿈/제어문자는 422, 최대 200개
- **Admin XSS 방어**: 카드 HTML은 문자열 템플릿으로 조립하므로 `escapeHtml`이 따옴표까지 이스케이프해야 한다 (속성값 컨텍스트). 새 필드를 템플릿에 넣을 때 반드시 감쌀 것
- **업로드 검증 순서**: HTTP 미들웨어가 Content-Length > 5MB 를 본문 수신 전에 413 → 엔드포인트가 파일을 청크로 읽어 메모리 상한 유지 → `Image.open` 헤더로 픽셀 수 검사(24MP 초과 400) → `convert("RGB")`. 순서를 바꾸면 메모리/디스크 고갈 공격에 노출됨. `SERVER_HOST`/`ws_max_size` 는 `python main.py` 로 실행할 때만 적용됨
- **이미지 파이프라인**: 업로드 → letterbox 리사이즈(416x240) → Floyd-Steinberg 디더링(`img.convert("1")`) → 회전/반전 → byte packing. 단순 threshold 변환하면 이모지/컬러 디테일이 날아가므로 반드시 디더링 사용
- **Apple 단축어 연동**: `/api/shortcuts/names`가 plain text 줄바꿈 목록 반환 → iOS JSON 파싱 버그 회피 구조

### Firmware (`eink-status-board.ino`)
- **듀얼 파일 관리**: `eink-status-board.ino`(실제 크리덴셜)와 `eink-status-board.ino.sample`(더미 크리덴셜)은 Wi-Fi SSID/PW, 서버 호스트를 제외하면 동일한 코드. **펌웨어 수정 시 반드시 양쪽 모두에 반영해야 한다.**
- `eink-status-board.ino`는 `.gitignore`에 등록됨. `.ino.sample`만 git 추적 대상
- **펌웨어 변경 시 알림**: `.ino` 파일이 수정되면 ESP32 디바이스에 `bash upload.sh`로 수동 업로드해야 반영됨. 변경 사항을 사용자에게 명시적으로 알려야 한다
- `src/` 폴더의 `EPD_*.cpp/h`는 Elecrow EPaperDrive 라이브러리 (UC8253 드라이버). GxEPD2와 호환 안 됨
- 5초 자동 재연결, 15초 heartbeat ping

### Admin UI (`server/static/admin.html`)
- Vanilla HTML/JS/CSS 단일 파일, 외부 프레임워크 없음
- 프리셋 CRUD + 활성화 + 1-bit 흑백 미리보기

## Key Constants

| Constant | Value | Note |
|----------|-------|------|
| Display resolution | 416 x 240 (landscape) | 서버는 가로 기준, 펌웨어는 240x416 세로 기준 |
| Frame buffer | 12,480 bytes | 416 * 240 / 8 |
| Power pin | GPIO 7 | HIGH = display ON |
| Server port | 5000 | `SERVER_PORT` env var로 변경 가능 |
| Max upload | 5 MB / 24 MP | `MAX_UPLOAD_SIZE`, `MAX_IMAGE_PIXELS` |

## Environment Variables

`.env` 파일 또는 시스템 환경변수로 설정:
- `SERVER_HOST` — 바인딩 주소 (기본: `0.0.0.0`, 프록시 뒤에서는 `127.0.0.1`)
- `SERVER_PORT` — 서버 포트 (기본: `5000`)
- `SERVER_RELOAD` — uvicorn 자동 재시작 (기본: `false`, 개발 시에만 `true`)

## Docs

- `README.md` — 개요, 빠른 시작, API 레퍼런스. API를 바꾸면 여기 표를 함께 갱신
- `docs/deploy-gcp.md` — GCP e2-micro + DuckDNS + systemd 배포, 보안 강화 절
- `docs/apple-shortcuts.md` — 마스터 단축어 구성 절차
- `GEMINI.md` — git 제외, 다른 에이전트용 요약. 유지보수 대상 아님

## Important Gotchas

- **디더링 필수**: 1-bit 변환 시 `img.convert("1")` (Floyd-Steinberg) 사용. threshold 방식은 이모지/컬러 이미지를 망침
- **회전+반전**: E-paper 컨트롤러 메모리 스캔 방향 때문에 `ROTATE_90 + FLIP_LEFT_RIGHT` 필수. 이 순서를 바꾸면 화면이 뒤집힘
- **해상도 방향 차이**: 서버는 416x240(가로), 펌웨어는 240x416(세로)으로 동일한 디스플레이를 참조
- **iOS 단축어 제한**: unsigned `.shortcut` 파일 설치가 iOS 15+에서 차단됨. "마스터 단축어" 패턴(GET 이름목록 → 선택 → POST 활성화)으로 우회
- **Pillow tobytes() 패딩**: 회전 후 폭이 240px(8의 배수)라 현재 해상도에서는 패딩이 없지만, 상수 변경에 대비해 `_manual_pack_1bit` 폴백 유지
- **인증 없음**: 모든 API가 무인증. 인터넷 노출 시 `docs/deploy-gcp.md`의 보안 절(방화벽/Tailscale/Caddy) 적용이 전제. 인증을 추가한다면 단축어(`/api/shortcuts/*`)와 Admin 양쪽에 키를 넣을 수 있어야 함

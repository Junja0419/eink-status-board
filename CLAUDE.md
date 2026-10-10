# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ESP32-S3 + CrowPanel 3.7" E-paper (UC8253, 416x240, 1-bit BW) 실시간 상태 표시 시스템.
FastAPI 서버(v3.0)가 이미지·텍스트를 1-bit 바이너리(12,480 bytes)로 렌더링하여 WebSocket으로 ESP32에 푸시한다.
운영에서는 Caddy(HTTPS) 뒤에서 Google 로그인(브라우저) / API 키(단축어) / 디바이스 토큰(ESP32)으로 보호된다.

## Commands

### Server
```bash
cd server
pip install -r requirements.txt
AUTH_DISABLED=true python main.py   # 로컬 개발: 0.0.0.0:5000, 무인증. 코드 수정 중이면 SERVER_RELOAD=true 추가
```
- `AUTH_DISABLED=true` 없이 띄우려면 `GOOGLE_CLIENT_ID/SECRET`, `ALLOWED_EMAILS`, `SESSION_SECRET`(32자+), `PUBLIC_BASE_URL` 이 모두 필요하다. 하나라도 비면 `_validate_auth_config()` 가 `인증 설정 누락` 으로 시작을 거부한다 — 의도된 fail-closed 이므로 완화하지 말 것. 같은 함수가 `AUTH_DISABLED=true` + `PUBLIC_BASE_URL=https://...` 조합(운영 `.env` 에 개발 스위치가 남는 사고)과 `https://` 가 아닌 `PUBLIC_BASE_URL`(`http://localhost…`/`http://127.0.0.1…` 만 예외)도 거부한다.
- Admin UI: http://localhost:5000/admin

수동 검증(자동 테스트 없음):
- 로컬(`AUTH_DISABLED=true`): `GET /status`, 이미지 업로드 또는 `POST /api/presets/text` → `POST /api/presets/{id}/activate`, `ws://host/ws` 접속 시 12,480바이트 수신 확인.
- 인증 경로(더미 OAuth 값 + 24자+ `API_KEY`/`DEVICE_TOKEN` + 32자+ `SESSION_SECRET` 으로 기동): `/healthz` 200, `/status` 401, 틀린 `X-API-Key` 로 `/api/shortcuts/names` 401, 토큰 없는 WebSocket 403.

### Firmware
```bash
# arduino-cli 사전 설치 필요. ESP32 보드 패키지 + WebSockets 라이브러리 설치:
arduino-cli core install esp32:esp32
arduino-cli lib install WebSockets
# EPaperDrive 라이브러리는 Elecrow에서 수동 다운로드하여 Arduino libraries 폴더에 복사

NO_UPLOAD=1 bash upload.sh                    # 컴파일만 (기기 불필요) — 기기에 올리기 전 사전 확인
bash upload.sh                                # compile + upload (esp32s3, OPI PSRAM, huge_app partition)
PORT=/dev/cu.usbserial-XXXX bash upload.sh    # 시리얼 포트 지정 (없으면 보이는 포트를 안내하고 중단)
```
`upload.sh` 는 `.ino` 에 기본값(`YOUR_WIFI_SSID`, `YOUR_WIFI_PASSWORD`, `SERVER_IP`, `YOUR_DEVICE_TOKEN`)이 남아 있으면 빌드를 거부한다. Rosetta 없는 Apple Silicon 에서는 내장 ctags(x86_64 전용)를 못 쓰므로 `tools/ctags-shim/ctags`(no-op)로 자동 대체한다.

## Architecture

```
Admin HTML(세션) / Apple Shortcuts·curl(X-API-Key) / ESP32(X-Device-Token)
  │  HTTPS (Caddy :443) → 127.0.0.1:5000
  ▼
server/main.py (FastAPI)
  │  auth_gate 미들웨어 → 라우트
  │  이미지: Pillow RGB → Floyd-Steinberg dithered 1-bit
  │  텍스트: 416x240 mode "1" 로 직접 렌더 → 같은 파이프라인
  │  → ROTATE_90 + FLIP_LEFT_RIGHT (하드웨어 스캔 방향 보정) → 12,480 bytes
  ▼
WebSocket /ws → broadcast to all ESP32 clients
  ▼
eink-status-board.ino (ESP32-S3)
  │  CRC32 중복 검사 → memcpy to frameBuffer
  │  EPD_FastInit → EPD_Display → EPD_Update → EPD_DeepSleep
  ▼
CrowPanel 3.7" E-paper
```

### Server (`server/main.py`)
- **단일 파일 구조**: 모든 API, 인증, WebSocket 핸들러, 이미지·텍스트 렌더링이 `main.py` 하나에 있음
- **인증은 secure-by-default**: `auth_gate`(`@app.middleware("http")`)가 모든 HTTP 요청을 먼저 검사한다.
  `PUBLIC_PATHS`(`/`=로그인 전 메인 페이지 `static/login.html`, `/healthz`, `/login`, `/auth/callback`, `/logout`, `ICON_FILES` 의 파비콘 3개)만 무인증, `API_KEY_PATHS`(`/api/shortcuts/names`, `/api/shortcuts/activate`)만 세션 **또는** `X-API-Key`(`Authorization: Bearer` 도 허용), 나머지는 전부 Google 세션 필수. 새 라우트는 아무것도 안 해도 보호된다 — 두 집합에 넣는 것은 의도적 결정일 때만, 상태를 바꾸는 라우트를 API 키에 열지 말 것
- **`auth_gate` 는 HTTP 미들웨어라 WebSocket 라우트를 덮지 못한다**: `/ws` 핸들러가 `_device_authorized()`(`X-Device-Token`)로 직접 검사하고, 틀리면 accept 전에 close(핸드셰이크 HTTP 403). **앞으로 WebSocket 라우트를 추가하면 반드시 스스로 인증할 것**. 게이트는 라우터와 같은 `request.scope["path"]` 로 경로를 판단한다
- **비밀 비교**: `_secret_matches`(`hmac.compare_digest`, 기대값이 비어 있으면 항상 거부). `API_KEY` 가 비면 단축어 경로는 세션만, `DEVICE_TOKEN` 이 비면 어떤 디바이스도 접속 불가
- **세션**: 로그인 후 `request.session["email"]` 만 저장(Google 토큰은 저장 안 함). `_session_email` 이 요청마다 `ALLOWED_EMAILS` 를 다시 검사한다. `/auth/callback` 은 `email_verified is True` + 소문자 이메일 일치일 때만 세션 발급(Authlib 이 state·nonce·PKCE S256·id_token 서명을 검증). 쿠키 `eink_session`, SameSite=Lax, `PUBLIC_IS_HTTPS`(스킴 대소문자 무시) 면 Secure, 30일. 세션은 서버에 저장되지 않는 서명 쿠키이므로 개별 삭제 대신 **세션 세대**(`session_generation`, `secrets.randbits(53)`)로 폐기한다: 로그인 시 `session["gen"]` 에 현재 세대를 넣고, `_session_email` 은 `gen` 이 서버의 현재 세대와 같을 때만 받아들인다(서명·30일·`ALLOWED_EMAILS` 검사에 더해). `POST /logout` 은 **유효한 세션이 있을 때만** 세대를 새 무작위 값으로 바꿔 저장한다(다른 기기·복사된 쿠키·다른 허용 계정 포함 전부 폐기, 세션 없는 교차 사이트 POST 는 아무것도 못 바꿈). 판정이 시계와 무관하다(시각 비교를 도입하지 말 것). **fail closed**: 세대의 기본값이 무작위라 `state.json` 을 잃거나 깨지면 시작할 때 새 세대가 정해져 옛 쿠키가 모두 거부되고 모두 한 번 다시 로그인할 뿐이다. `restore_state()` 가 저장된 세대(`state.json` 의 `session_generation`)가 있으면 이어 쓰고 lifespan 이 시작 직후 저장한다. 오래된 백업을 복원하면 옛 세대가 돌아와 그 세대로 발급된 쿠키가 다시 유효해질 수 있으므로 복원 뒤 로그인→로그아웃 한 번 또는 `SESSION_SECRET` 교체. 로그아웃의 저장이 실패하면 500 "로그아웃 저장 실패" 페이지로 `SESSION_SECRET` 교체를 안내한다. 콜백의 오류 분기(OAuthError·예외)는 기존 세션을 **지우지 않는다** — `/auth/callback?error=x` 로의 교차 사이트 이동으로 도달할 수 있어 지우면 강제 로그아웃 수단이 된다. state 검증을 통과해 Google 로그인을 마친 뒤 허용되지 않은 계정일 때만 `session.clear()`. `/login` 이 Google 설정 문서를 못 받으면 502 안내 페이지
- **미들웨어 순서**: `SessionMiddleware` 는 `auth_gate` 보다 **나중에** `add_middleware` 해야 한다(나중에 추가한 쪽이 바깥 = 먼저 실행). 순서를 바꾸면 `auth_gate` 가 `request.session` 을 못 읽는다
- **전역 상태**: `current_frame_bytes`, `current_display_image`, `current_status_text`, `current_preset_id` — 새 ESP32 접속 시 즉시 최신 프레임 전송에 사용. 활성화 시 네 값 교체 + 전송을 `_activate_lock` 안에서 한 단위로 수행(이미지 로딩·1-bit 변환은 락 밖 `to_thread`). `known_devices` 는 디바이스 접속 이력(최대 20, 오래된 오프라인부터 제거)
- **락 3개** (모두 `asyncio.Lock`, 단일 이벤트 루프 전제, **중첩 금지**): `_presets_lock`(presets.json 읽기-수정-쓰기), `_activate_lock`(전역 프레임 교체 + 전송 + 전송 간격 상태 `_last_push_at`/`_deferred_push`/`_deferred_force`), `_state_lock`(state.json 쓰기). `persist_state()` 는 항상 다른 락을 푼 뒤에 호출. `activate()` 는 락 안에서 프리셋을 다시 조회(그 사이 삭제되면 404)하고, 이름 변경·삭제도 `_presets_lock` 을 푼 **뒤에** `_activate_lock` 을 잡아 `current_status_text`/`current_preset_id` 를 갱신한다(동시 활성화가 옛 이름·삭제된 프리셋을 되살리지 못하게). 두 락은 차례로만 잡고 중첩하지 않는다
- **영속화**: `server/data/presets.json`, `images/{id}.png`, `state.json`(`active_preset_id` + `session_generation` + `devices`) — `server/data/` 는 `images/.gitkeep` 만 빼고 git 제외, `mkstemp` 로 만든 고유 임시 파일(600) + `os.replace`(`_atomic_write_json`)로 원자적 저장(동시 쓰기가 서로의 임시 파일을 건드리지 않음). `restore_state()` 가 시작 시 마지막 활성 프리셋의 프레임과 세션 세대를 되살리고 디바이스 항목은 `clean_device_entry` 로 실시간 보고와 같은 규칙으로 재검증한다(임시 `ip-…` 항목은 절대 복원하지 않음, `uptime_s` ≤ 2^32). 데이터 파일은 UTF-8 BOM 이 붙어 있어도 읽는다(`utf-8-sig` — BOM 이 붙은 `presets.json` 을 손상으로 취급하지 않음). `persist_state()` 는 쓰기 실패를 로그만 남기고 `False` 를 돌려준다(이미 적용된 변경을 500 으로 만들지 않음 — 결과를 확인하는 곳은 `/logout` 뿐). `presets.json` 항목은 `_valid_preset_entry`(8-hex `id`=`PRESET_ID_RE` + 비어 있지 않은 `name`)를 통과해야 하고 `load_presets()` 는 나머지를 건너뛴다. 시작 시 `quarantine_corrupt_presets()` 가 깨진 파일의 원본을 `presets.json.corrupt-<unix time>` 으로 보관하고 유효 항목만 남긴다 — 데이터 문제가 시작을 막으면 안 되므로 lifespan 에서 프리셋 처리와 `restore_state()` 를 따로 try 해 예외를 로그로 삼킨다(프리셋 오류가 세션 세대 복원을 막지 않게). 활성화·활성 프리셋 삭제는 `persist_state()` 로 즉시, 디바이스 hello·연결 해제는 `schedule_persist()`(`STATE_PERSIST_DELAY` 2초 디바운스)로 저장하고 종료 시 마지막으로 flush. status 보고는 메모리만
- **디바이스 보고 검증**: `apply_device_report` 는 `hello`/`status` 만 받고 형식·범위를 검사해 통과한 필드만 반영(`id` 는 `DEVICE_ID_RE`, `fw`/`reset` 은 `DEVICE_TEXT_RE` 전체 일치, `rssi` -127..0, `uptime_s` ≥0, bool 제외). 신원(`id`/`fw`/`reset`)은 연결당 **첫 hello 에서만** 반영(`accept_identity`). 2,000자 초과·비JSON·알 수 없는 type 은 조용히 무시. 새 필드를 받으려면 여기서 검증하고, Admin 은 디바이스 값을 신뢰하지 않는 문자열로 렌더(escape). 동시 연결은 `MAX_DEVICE_CONNECTIONS`(4) — 초과 시 `ConnectionManager.connect` 가 None, 핸들러가 accept 전에 close
- **프레임 중복 억제 / force / 전송 간격**: `activate()` 는 새 프레임이 `current_frame_bytes` 와 바이트 단위로 같고 `force` 가 아니면 전송을 생략한다(`skipped: true`). 단 같은 프레임의 예약 전송이 아직 대기 중이면 `deferred: true, skipped: false` 로 응답한다. `force=True` 와 `POST /api/display/refresh` 는 먼저 텍스트 `{"type":"force_refresh"}` 를 broadcast 한 뒤 프레임을 보낸다. 전송은 `push_current_frame()`(락을 잡은 채 호출) 이 `MIN_PUSH_INTERVAL`(3초)로 제한한다 — 간격 안(또는 예약이 대기 중인 동안)의 요청은 예약 하나(`_push_later`)로 합쳐 실행 시점의 최신 프레임을 한 번 보내고(`force` 는 OR), 응답은 `deferred: true`, `clients_notified: 0`. 펌웨어는 마지막으로 그린 프레임과 CRC32 가 같으면 갱신을 건너뛰고, `force_refresh` 를 받으면 다음 프레임은 무조건 그린다. 메시지 이름·필드(`force_refresh`/`hello`/`status`)를 바꾸면 서버와 펌웨어 양쪽을 고칠 것
- **텍스트 프리셋 렌더링**: `render_text_image` 는 416x240 **mode "1"** + `draw.fontmode = "1"`(안티앨리어싱 없음)로 그려 이후 디더링이 글자 가장자리에 노이즈를 만들지 않게 한다 — 안티앨리어싱을 켜지 말 것. 이모지·제어문자는 `clean_display_text` 가 제거(한글 폰트에 글리프가 없어 □ 로 찍힘), 최대 200자·6줄. 글자 크기는 `_fit_text` 가 `TEXT_MIN_FONT_SIZE`~상한 범위를 이분 탐색해 자동 맞춤하고, 줄바꿈은 단어 단위를 우선하되(`_wrap_paragraph`) 글자 단위(`_split_long_word`) 배치가 1.5배 이상 크거나 단어 단위로는 안 들어갈 때만 글자 단위로 쪼갠다. 최소 크기로도 안 들어가면 `ValueError` → 422. 폰트는 `FONT_CANDIDATES`(`FONT_PATH` 최우선), 없으면 503. 원문은 저장하지 않고 PNG 만 저장하므로 이후 이미지 프리셋과 동일하게 취급
- **ConnectionManager**: WebSocket 클라이언트·연결별 디바이스 정보 관리, `asyncio.gather` + 전송 타임아웃(`WS_SEND_TIMEOUT`)으로 동시 broadcast(`broadcast_bytes`/`broadcast_text`), 실패 클라이언트 자동 제거
- **프리셋 이름은 유일 키**: 단축어 API가 이름으로 조회하므로 생성·이름 변경(`PATCH`) 시 중복 이름은 409, 줄바꿈/제어문자는 422, 최대 200개. `presets.json` 배열 순서 = Admin·단축어 목록 순서(`PUT /api/presets/order` 는 ID 전체의 순열이 아니면 409)
- **Admin XSS 방어**: 카드 HTML은 문자열 템플릿으로 조립하므로 `escapeHtml`이 따옴표까지 이스케이프해야 한다 (속성값 컨텍스트). 새 필드를 템플릿에 넣을 때 반드시 감쌀 것
- **본문 크기·업로드 검증 순서**: `auth_gate` 가 인증을 먼저 확인한 뒤 `Transfer-Encoding`(chunked) 은 411, Content-Length 가 한도(`POST /api/presets` 는 5MB+64KB, 그 외 전부 `MAX_JSON_BODY_SIZE` 16KB)를 넘거나 숫자가 아니면 본문 수신 전에 413 → 엔드포인트가 파일을 청크로 읽어 메모리 상한 유지 → `Image.open` 헤더로 픽셀 수 검사(24MP 초과 400) → `convert("RGB")`. 순서를 바꾸면 메모리/디스크 고갈 공격에 노출됨
- **`python main.py` 로 실행할 때만 적용되는 것**: `SERVER_HOST`/`SERVER_PORT`/`SERVER_RELOAD`, `ws_max_size`(64KB), `proxy_headers` + `forwarded_allow_ips="127.0.0.1"`(Caddy 의 `X-Forwarded-*`)
- **이미지 파이프라인**: 업로드 → letterbox 리사이즈(416x240) → Floyd-Steinberg 디더링(`img.convert("1")`) → 회전/반전 → byte packing. 단순 threshold 변환하면 이모지/컬러 디테일이 날아가므로 반드시 디더링 사용
- **Apple 단축어 연동**: `/api/shortcuts/names`가 plain text 줄바꿈 목록 반환 → iOS JSON 파싱 버그 회피 구조. 요청 본문은 `{"name"(1~100자), "force"?}`

### Firmware (`eink-status-board.ino`)
- **듀얼 파일 관리**: `eink-status-board.ino`(실제 크리덴셜)와 `eink-status-board.ino.sample`(더미 크리덴셜)은 `WIFI_SSID`/`WIFI_PASSWORD`/`WS_HOST`/`DEVICE_TOKEN` 값을 제외하면 동일한 코드. **펌웨어 수정 시 반드시 양쪽 모두에 반영해야 한다.** 실제 `.ino` 는 크리덴셜이 들어 있으니 내용을 출력하거나 문서에 옮기지 말 것
- `eink-status-board.ino`는 `.gitignore`에 등록됨. `.ino.sample`만 git 추적 대상
- **펌웨어 변경 시 알림**: `.ino` 파일이 수정되면 ESP32 디바이스에 USB 로 `bash upload.sh` 수동 업로드해야 반영됨. 변경 사항을 사용자에게 명시적으로 알려야 한다
- 상단 설정 상수: `WIFI_SSID`, `WIFI_PASSWORD`, `WS_HOST`, `WS_PORT`(443), `WS_PATH`(`/ws`), `WS_USE_TLS`(true; LAN 개발 서버는 false + 5000), `DEVICE_TOKEN`, `FW_VERSION`
- 동작: wss + 내장 ISRG 루트(`ROOT_CA_PEM`)로 체인·호스트명 검증(Let's Encrypt 발급 전제), 핸드셰이크에 `X-Device-Token` 전송, 접속 시 `hello`·5분마다와 화면 갱신 후 `status` 전송, 프레임 CRC32 가 직전과 같으면 갱신 생략(`force_refresh` 수신 시 예외), Wi-Fi 5분 단절 시 `ESP.restart()`(RTC 메모리에 CRC 를 남겨 화면 유지), 5초 자동 재연결·15초 heartbeat ping, 화면 재그리기 최소 간격 `MIN_REFRESH_INTERVAL_MS`(5초 — 서버가 더 빨리 보내도(침해 포함) 5초에 한 번만 그리고 가장 최근 프레임이 이김, CRC 가 같은 프레임은 여전히 건너뜀)
- **함수는 사용 전에 선언**: Apple Silicon 에서 ctags 대용품을 쓰면 Arduino 의 프로토타입 자동 생성이 꺼진다. 스케치 상단의 전방 선언 블록에 새 함수를 추가할 것 (`.ino` 와 `.ino.sample` 양쪽)
- `src/` 폴더의 `EPD_*.cpp/h`는 Elecrow EPaperDrive 라이브러리 (UC8253 드라이버). GxEPD2와 호환 안 됨

### Admin UI (`server/static/admin.html`)
- Vanilla HTML/JS/CSS 단일 파일, 외부 프레임워크 없음
- **디자인**: Linear 계열 다크 테마. 색·모서리·글꼴은 `:root` 토큰(`--canvas`, `--surface-1..3`, `--hairline*`, `--ink*`, `--primary`)을 쓰고 새 색이 필요하면 토큰부터 추가한다. 라벤더 `--primary` 는 주요 버튼·포커스 링·선택(활성 프리셋) 같은 강조에만 쓰고, 그라디언트·글로우·이모지 아이콘 대신 선 아이콘(`.i` SVG)을 쓴다. 작은 정보성 글자는 `--ink-subtle` 이상(`--ink-tertiary` 는 대비 부족). `login.html` 과 `_message_page`(main.py)도 같은 값을 쓰므로 색을 바꾸면 세 곳을 함께 고칠 것
- **파비콘**: `server/static/favicon.svg`·`favicon.ico`·`apple-touch-icon.png` 는 `tools/make_icons.py` 가 한 좌표 정의에서 생성한다(직접 편집하지 말고 스크립트를 고친 뒤 다시 실행). 서빙은 `ICON_FILES` → `_icon_route`
- 로그인 흐름: `/`(메인, 버튼) → `/login`(Google 로 이동) → `/auth/callback` → `/admin`. 미인증 브라우저 GET 은 `/` 로 303, Admin 의 fetch 401 도 `/` 로 이동. 리디렉션 URI 는 항상 `PUBLIC_BASE_URL + /auth/callback` 이며 메인 페이지 경로와 무관
- Google 로그인/로그아웃(로그아웃은 HTML 응답이라 fetch 가 아닌 폼 제출), 디바이스 목록, 강제 새로고침, 활성 프리셋 강조, 이름 인라인 변경, 순서 변경, 이미지·텍스트 프리셋 생성(텍스트는 실시간 미리보기), 1-bit 흑백 미리보기, 단축어 가이드(주소는 지금 접속한 `location.origin` 으로 채움 — 운영에서는 항상 https)

## Key Constants

| Constant | Value | Note |
|----------|-------|------|
| Display resolution | 416 x 240 (landscape) | 서버는 가로 기준, 펌웨어는 240x416 세로 기준 |
| Frame buffer | 12,480 bytes | 416 * 240 / 8 |
| Power pin | GPIO 7 | HIGH = display ON |
| Server port | 5000 | `SERVER_PORT` env var로 변경 가능 |
| Max upload | 5 MB / 24 MP | `MAX_UPLOAD_SIZE`, `MAX_IMAGE_PIXELS` |
| Text preset | 200자 / 6줄 / 14~120px | `TEXT_MAX_CHARS`, `TEXT_MAX_LINES`, `TEXT_MIN/MAX_FONT_SIZE` |
| Limits | 이름 100자 / 프리셋 200개 / 디바이스 이력 20 / 동시 디바이스 연결 4 | `MAX_PRESET_NAME_LENGTH`, `MAX_PRESETS`, `MAX_KNOWN_DEVICES`, `MAX_DEVICE_CONNECTIONS` |
| Request body | JSON 16KB (업로드 제외) | `MAX_JSON_BODY_SIZE` |
| Push interval | 3초 | `MIN_PUSH_INTERVAL` (env), `STATE_PERSIST_DELAY` 2초 |
| Secrets | `SESSION_SECRET` ≥32자, `API_KEY`/`DEVICE_TOKEN` ≥24자 | 시작 시 검사 |
| Session | 30일 | `SESSION_MAX_AGE` |

## Environment Variables

`server/.env`(python-dotenv) 또는 시스템 환경변수(우선). 상세 표는 `README.md` 설정 절:
- `SERVER_HOST`(기본 `0.0.0.0`, 프록시 뒤 `127.0.0.1`), `SERVER_PORT`(`5000`), `SERVER_RELOAD`(`false`, 개발에서만 true)
- `AUTH_DISABLED` — true 면 인증·OAuth 변수·`/ws` 토큰 모두 불필요. **로컬 개발 전용**이며 `PUBLIC_BASE_URL=https://...` 와 함께면 시작을 거부
- `PUBLIC_BASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`(쉼표 구분), `SESSION_SECRET` — 인증 사용 시 필수
- `API_KEY`(단축어용, `/api/shortcuts/*` 에만 통함), `DEVICE_TOKEN`(ESP32용, `/ws` 에만 통함) — 비우면 각각 세션 전용 / 디바이스 접속 불가
- `FONT_PATH` — 텍스트 프리셋 한글 폰트 경로(없으면 Nanum/Noto CJK/macOS 폰트 자동 탐색)
- `MIN_PUSH_INTERVAL` — 패널로 프레임을 보내는 최소 간격(초, 기본 `3`). 연타는 합쳐서 마지막 상태만 전송

## Docs

- `README.md` — 개요, 빠른 시작, 설정(환경변수 표), API 레퍼런스(인증 열 포함), WebSocket 프로토콜. **라우트·상태 코드·환경변수·프로토콜을 바꾸면 여기 표를 함께 갱신**
- `docs/deploy-gcp.md` — GCP e2-micro + DuckDNS + Caddy(HTTPS) + systemd 배포, 검증 체크리스트, 보안 모델
- `docs/google-oauth.md` — Google OAuth 클라이언트 생성, 접근 제한 방식, 문제 해결
- `docs/apple-shortcuts.md` — 마스터 단축어 구성 절차(`X-API-Key` 헤더 포함)
- `GEMINI.md` — git 제외, 다른 에이전트용 요약. 유지보수 대상 아님

## Important Gotchas

- **디더링 필수**: 1-bit 변환 시 `img.convert("1")` (Floyd-Steinberg) 사용. threshold 방식은 이모지/컬러 이미지를 망침
- **회전+반전**: E-paper 컨트롤러 메모리 스캔 방향 때문에 `ROTATE_90 + FLIP_LEFT_RIGHT` 필수. 이 순서를 바꾸면 화면이 뒤집힘
- **해상도 방향 차이**: 서버는 416x240(가로), 펌웨어는 240x416(세로)으로 동일한 디스플레이를 참조
- **iOS 단축어 제한**: unsigned `.shortcut` 파일 설치가 iOS 15+에서 차단됨. "마스터 단축어" 패턴(GET 이름목록 → 선택 → POST 활성화)으로 우회. 두 요청 모두 `X-API-Key` 헤더가 필요
- **Pillow tobytes() 패딩**: 회전 후 폭이 240px(8의 배수)라 현재 해상도에서는 패딩이 없지만, 상수 변경에 대비해 `_manual_pack_1bit` 폴백 유지
- **인증 모델 유지**: 새 라우트는 자동으로 세션 필요(그대로 둘 것). 상태를 바꾸는 동작은 GET 으로 만들지 말 것(SameSite=Lax 쿠키는 교차 사이트 GET 내비게이션에 실린다). `/logout` 은 전역 폐기이므로 유효한 세션 확인 없이 폐기하게 바꾸지 말 것(교차 사이트 POST 로 강제 로그아웃 가능해짐). `/auth/callback` 의 오류 분기에서 세션을 지우는 것도 같은 이유로 금지(교차 사이트 GET 으로 도달 가능). https 판정은 `PUBLIC_IS_HTTPS` 상수를 쓰고 직접 `startswith("https://")` 하지 말 것. 로그·응답·문서에 `API_KEY`/`DEVICE_TOKEN`/세션 값/실제 이메일을 남기지 말 것(문서는 `you@gmail.com`, `<도메인>` 같은 자리표시자). Caddy 접근 로그는 헤더 유출 방지를 위해 의도적으로 꺼 둠
- **TLS 체인**: 펌웨어는 ISRG 루트만 신뢰하므로 서버 인증서는 Let's Encrypt 여야 한다. `WS_HOST` 는 IP 가 아니라 인증서의 도메인이어야 함
- **requirements**: `starlette>=0.49.1` 은 multipart·FileResponse Range DoS 권고 때문이므로 낮추지 말 것

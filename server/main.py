"""
============================================================
 E-ink Status Board — FastAPI 백엔드 서버 v3.0
============================================================

 ESP32-S3 CrowPanel 3.7인치 E-paper 디스플레이로
 상태 이미지를 실시간 푸시(Push)하는 WebSocket 서버.

 실행 방법:
   python main.py                       # 운영: .env 에 OAuth 설정 필요
   AUTH_DISABLED=true python main.py    # 로컬 개발 (인증 없음)

 인증 (AUTH_DISABLED=true 가 아니면 항상 적용):
   브라우저  — Google 로그인, ALLOWED_EMAILS 에 있는 계정만 세션 발급
   단축어    — X-API-Key 헤더 (API_KEY), /api/shortcuts/* 에만 유효
   ESP32     — X-Device-Token 헤더 (DEVICE_TOKEN), /ws 에만 유효

 API:
   GET    /                                  — 로그인 전 메인 페이지 (무인증, 로그인 상태면 /admin)
   GET    /healthz                           — 헬스 체크 (무인증)
   GET    /login                             — Google 로그인 시작 (메인 페이지의 버튼)
   GET    /auth/callback                     — Google OAuth 콜백
   POST   /logout                            — 로그아웃
   GET    /admin                             — 관리자 페이지
   GET    /status                            — 현재 상태·디바이스 목록
   GET    /current-preview.png               — 현재 표시 중인 화면 미리보기
   POST   /api/display/refresh               — 현재 화면 강제 새로고침
   WS     /ws                                — ESP32 WebSocket 연결
   GET    /api/presets                       — 프리셋 목록
   POST   /api/presets                       — 이미지 프리셋 생성 (multipart: name, image)
   POST   /api/presets/text                  — 텍스트 프리셋 생성 (JSON: name, text, font_size)
   POST   /api/presets/text/preview          — 텍스트 렌더링 미리보기 (저장 안 함)
   PUT    /api/presets/order                 — 프리셋 순서 변경 (JSON: ids)
   PATCH  /api/presets/{id}                  — 프리셋 이름 변경 (JSON: name)
   DELETE /api/presets/{id}                  — 프리셋 삭제
   POST   /api/presets/{id}/activate         — 프리셋 활성화 (?force=true 로 강제 갱신)
   GET    /api/presets/{id}/preview.png      — 프리셋 미리보기 (1-bit 디더링 결과)
   GET    /api/shortcuts/names               — Apple 단축어용 이름 목록 (Plain Text)
   POST   /api/shortcuts/activate            — Apple 단축어용 이름 기반 활성화 (JSON)

 기술 스택:
   FastAPI, Uvicorn, WebSockets, Pillow (PIL), Authlib
============================================================
"""

import asyncio
import hmac
import html
import json
import logging
import os
import re
import secrets
import tempfile
import time
import uuid as uuid_lib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from fastapi import (
    FastAPI, File, Form, HTTPException, Request, UploadFile,
    WebSocket, WebSocketDisconnect,
)
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
    RedirectResponse, Response,
)
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.sessions import SessionMiddleware

# .env 파일 로드 (있을 때만)
load_dotenv()

# ──────────────────────────────────────────────
#  로깅 설정
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("eink-server")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ──────────────────────────────────────────────
#  디스플레이 상수
# ──────────────────────────────────────────────

# 전자잉크 패널 해상도 (가로 모드, Landscape)
DISPLAY_WIDTH = 416
DISPLAY_HEIGHT = 240

# 1-bit 이미지의 바이트 크기: 416 × 240 ÷ 8 = 12,480 바이트
FRAME_BUFFER_SIZE = (DISPLAY_WIDTH * DISPLAY_HEIGHT) // 8

# ──────────────────────────────────────────────
#  업로드·프리셋 제한
# ──────────────────────────────────────────────

# 업로드 파일 최대 크기 (5MB)
#  - Content-Length 가 이보다 크면 미들웨어가 본문을 받기 전에 413 으로 끊는다
#  - 그 뒤 엔드포인트는 파일을 청크로 읽어 메모리 사용량을 이 값으로 묶는다
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
# multipart 경계·필드 오버헤드 허용치
UPLOAD_OVERHEAD = 64 * 1024

# 이미지 업로드를 제외한 모든 요청(JSON)의 본문 한도. 가장 큰 정상 요청(프리셋 200개 순서 변경)도
# 수 KB 이므로, 이보다 큰 본문은 읽지 않고 거부한다.
MAX_JSON_BODY_SIZE = 16 * 1024

# 디코딩 허용 최대 픽셀 수 (약 24MP). 작은 파일이 거대한 해상도로 부풀어
# 메모리를 고갈시키는 "decompression bomb"을 헤더 단계에서 차단한다.
MAX_IMAGE_PIXELS = 24_000_000

# 프리셋 이름 길이 제한
MAX_PRESET_NAME_LENGTH = 100

# 프리셋 최대 개수 (디스크 채우기 방지)
MAX_PRESETS = 200

# ──────────────────────────────────────────────
#  텍스트 프리셋 렌더링
# ──────────────────────────────────────────────

TEXT_MAX_CHARS = 200
TEXT_MAX_LINES = 6
TEXT_MARGIN = 16            # 화면 가장자리 여백(px)
TEXT_MAX_FONT_SIZE = 120
TEXT_MIN_FONT_SIZE = 14

# 한글 폰트 후보 — FONT_PATH 환경변수가 최우선, 이후 OS별 기본 경로
FONT_CANDIDATES = [
    os.environ.get("FONT_PATH", ""),
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",       # apt: fonts-nanum
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",       # apt: fonts-noto-cjk
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",                # macOS
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
]

# 한글 폰트에는 이모지 글리프가 없어 □ 로 찍히므로 렌더링 전에 제거한다
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿"
    "‍️⃣\U000E0020-\U000E007F]"
)

# ──────────────────────────────────────────────
#  WebSocket·디바이스
# ──────────────────────────────────────────────

# 전송 타임아웃(초) — 읽지 않는 클라이언트 하나가 전체 푸시를 막지 못하게 한다
WS_SEND_TIMEOUT = 5.0

# 디바이스가 보내는 상태 보고(JSON 텍스트) 최대 길이
MAX_DEVICE_MESSAGE_CHARS = 2000

# 최근 접속 이력을 기억할 디바이스 수
MAX_KNOWN_DEVICES = 20

# 동시에 붙을 수 있는 디바이스(WebSocket) 수. 토큰이 유출돼도 연결을 무한정 열 수 없게 한다.
MAX_DEVICE_CONNECTIONS = 4

# 디바이스 접속 이력을 state.json 에 쓰는 최소 간격(초). 접속/해제가 몰려도 디스크 쓰기는 한 번으로 합친다.
STATE_PERSIST_DELAY = 2.0

# 패널로 프레임을 내보내는 최소 간격(초). E-ink 전체 갱신 한 번에 걸리는 시간과 비슷하게 둔다.
# 이보다 빠르게 들어온 활성화는 버리지 않고, 간격이 지난 뒤 "가장 마지막 상태"만 한 번 보낸다.
MIN_PUSH_INTERVAL = float(os.environ.get("MIN_PUSH_INTERVAL", "3"))

# 디바이스에게 "다음 프레임은 CRC가 같아도 다시 그려라"고 알리는 메시지
FORCE_REFRESH_MESSAGE = '{"type":"force_refresh"}'

DEVICE_ID_RE = re.compile(r"[A-Za-z0-9:_\-]{1,32}")
# 디바이스가 보고하는 문자열(fw, reset)에 허용하는 형식 — 예: "1.1.0", "poweron".
# 그 외 문자는 저장하지 않는다 (Admin 화면·로그에 그대로 찍히는 값이므로).
DEVICE_TEXT_RE = re.compile(r"[A-Za-z0-9._+\-]{1,16}")
DEVICE_ADDR_RE = re.compile(r"[A-Za-z0-9:.\-]{1,64}")          # ip
DEVICE_TIME_RE = re.compile(r"[0-9T:+\-.Z]{1,40}")            # ISO-8601 시각
MAX_DEVICE_UPTIME_S = 2**32                                    # 약 136년 — 그 이상은 정상 값이 아니다

# 프리셋 ID 형식 (generate_preset_id 가 만드는 8자리 hex). 파일 경로에 쓰이므로 이 형식만 받는다.
PRESET_ID_RE = re.compile(r"[0-9a-f]{8}")
# 일정 제안 링크의 종료 시각 (Power Automate 의 endWithTimeZone / end / convertToUtc 결과).
# Python 3.10 의 fromisoformat 은 7자리 소수 초와 Z 를 못 읽어 직접 해석한다. 소수 초는 버린다
UNTIL_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,7})?)?(Z|[+-]\d{2}:\d{2})?"
)
MAX_UNTIL_LENGTH = 64
MAX_REVERT_AHEAD = 24 * 3600          # 복귀 예약은 24시간 안쪽 일정만
# 로그인 후 돌아갈 수 있는 주소 — 허용 목록 (open redirect 방지)
NEXT_RE = re.compile(r"/(?:admin|suggest)(?:\?[^#\\\s]*)?")
MAX_NEXT_LENGTH = 1024                # 세션 쿠키(4KB)에 들어가므로 짧게
DEVICE_FIELDS = ("id", "ip", "fw", "rssi", "uptime_s", "reset", "connected_at", "last_seen")

# ──────────────────────────────────────────────
#  서버 실행 설정 (환경변수)
# ──────────────────────────────────────────────

SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")   # 리버스 프록시 뒤에서는 127.0.0.1
SERVER_PORT = int(os.environ.get("SERVER_PORT", 5000))
# 코드 변경 시 자동 재시작 — 개발용. 운영(systemd)에서는 반드시 false 유지.
SERVER_RELOAD = _env_flag("SERVER_RELOAD")

# ──────────────────────────────────────────────
#  인증 설정 (환경변수)
# ──────────────────────────────────────────────

# 로컬 개발 전용 스위치. 이 값이 true 가 아니면 아래 OAuth 설정이 모두 있어야 서버가 뜬다.
AUTH_DISABLED = _env_flag("AUTH_DISABLED")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"

# 로그인을 허용할 Google 계정 (쉼표 구분, 소문자 비교)
ALLOWED_EMAILS = frozenset(
    email.strip().lower()
    for email in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if email.strip()
)

# 세션 쿠키 서명 키 (32자 이상 무작위 문자열)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
SESSION_MAX_AGE = 30 * 24 * 60 * 60   # 30일

# 외부에서 접속하는 주소 (예: https://<도메인>.duckdns.org) — OAuth 리다이렉트 URI 기준
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
# urlsplit 은 스킴을 소문자로 정규화한다 — "HTTPS://…" 처럼 적어도 같은 판정이 나오게 이 값만 쓴다
PUBLIC_IS_HTTPS = urlsplit(PUBLIC_BASE_URL).scheme == "https"

# Apple 단축어용 키 — /api/shortcuts/* 에만 통한다 (비우면 단축어 경로 차단)
API_KEY = os.environ.get("API_KEY", "").strip()

# ESP32용 토큰 — /ws 에만 통한다 (비우면 어떤 디바이스도 접속 불가)
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "").strip()

MIN_SECRET_LENGTH = 24


def _validate_auth_config() -> None:
    """인증 설정이 불완전하면 서버를 띄우지 않는다 (fail closed)."""
    if AUTH_DISABLED:
        # 운영용 .env 에 AUTH_DISABLED 가 실수로 남아 전체가 열리는 사고를 막는다
        if PUBLIC_IS_HTTPS:
            raise RuntimeError(
                "AUTH_DISABLED=true 는 로컬 개발 전용입니다. 운영 설정(PUBLIC_BASE_URL=https://...)과 "
                "함께 쓸 수 없습니다."
            )
        return

    required = {
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
        "ALLOWED_EMAILS": ",".join(ALLOWED_EMAILS),
        "SESSION_SECRET": SESSION_SECRET,
        "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            f"인증 설정 누락: {', '.join(missing)}. server/.env 에 값을 넣거나, "
            "로컬 개발이라면 AUTH_DISABLED=true 로 실행하세요."
        )
    if len(SESSION_SECRET) < 32:
        raise RuntimeError("SESSION_SECRET 은 32자 이상이어야 합니다.")
    # 세션 쿠키에 Secure 플래그가 붙으려면 https 여야 한다. 평문 http 는 로컬 테스트용 주소만 허용.
    base = urlsplit(PUBLIC_BASE_URL)
    is_local_http = base.scheme == "http" and base.hostname in ("localhost", "127.0.0.1")
    if not (base.scheme == "https" and base.hostname) and not is_local_http:
        raise RuntimeError("PUBLIC_BASE_URL 은 https:// 로 시작하는 전체 주소여야 합니다.")
    for name, value in (("API_KEY", API_KEY), ("DEVICE_TOKEN", DEVICE_TOKEN)):
        if value and len(value) < MIN_SECRET_LENGTH:
            raise RuntimeError(f"{name} 은 {MIN_SECRET_LENGTH}자 이상이어야 합니다.")

    # 비밀 값은 생성 명령의 "결과"여야 한다. 공백·따옴표·$( 가 들어 있으면 명령 문자열을
    # 그대로 붙여 넣은 것이므로(공개된 값), 그 상태로 서버가 뜨지 않게 한다.
    secrets_in_use = {"SESSION_SECRET": SESSION_SECRET, "API_KEY": API_KEY, "DEVICE_TOKEN": DEVICE_TOKEN}
    for name, value in secrets_in_use.items():
        if value and not re.fullmatch(r"[A-Za-z0-9._~+/=\-]+", value):
            raise RuntimeError(
                f"{name} 에 허용되지 않는 문자(공백·따옴표·괄호 등)가 있습니다. "
                "생성 명령을 실행한 결과값만 넣으세요: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
    distinct = [v for v in secrets_in_use.values() if v]
    if len(distinct) != len(set(distinct)):
        raise RuntimeError("SESSION_SECRET / API_KEY / DEVICE_TOKEN 은 서로 다른 값이어야 합니다.")


_validate_auth_config()

# 파비콘 — 로그인 전 페이지에서도 쓰므로 공개. 경로 → (static/ 파일, MIME). tools/make_icons.py 로 만든다
ICON_FILES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
# 세션 없이 접근 가능한 경로 ("/" 는 로그인 전 메인 페이지)
PUBLIC_PATHS = frozenset({"/", "/healthz", "/login", "/auth/callback", "/logout", *ICON_FILES})
# API 키로도 접근 가능한 경로 (그 외 경로는 Google 로그인 세션만 통한다)
API_KEY_PATHS = frozenset({"/api/shortcuts/names", "/api/shortcuts/activate"})

oauth = OAuth()
if not AUTH_DISABLED:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url=GOOGLE_METADATA_URL,
        # PKCE(S256) — 인가 코드가 가로채여도 code_verifier 없이는 토큰으로 바꿀 수 없다
        client_kwargs={"scope": "openid email", "code_challenge_method": "S256"},
    )

# ──────────────────────────────────────────────
#  파일 경로 설정
# ──────────────────────────────────────────────

BASE_DIR = Path(__file__).parent                # server/
DATA_DIR = BASE_DIR / "data"                    # server/data/
IMAGES_DIR = DATA_DIR / "images"                # server/data/images/
PRESETS_FILE = DATA_DIR / "presets.json"        # 프리셋 메타데이터
STATE_FILE = DATA_DIR / "state.json"            # 마지막 활성 프리셋 + 디바이스 이력
STATIC_DIR = BASE_DIR / "static"                # server/static/

# ──────────────────────────────────────────────
#  전역 상태
# ──────────────────────────────────────────────
#  단일 asyncio 이벤트 루프에서만 갱신된다. 아래 네 값의 교체와 브로드캐스트는
#  _activate_lock 안에서 한 단위로 수행한다.

# 현재 표시 중인 프리셋 이름 / ID
current_status_text: str = ""
current_preset_id: Optional[str] = None

# 현재 렌더링된 프레임 바이너리 (새 클라이언트 접속 시 즉시 전송용)
current_frame_bytes: Optional[bytes] = None

# 현재 렌더링된 디스플레이 이미지 (미리보기 생성용)
current_display_image: Optional[Image.Image] = None

# 디바이스 접속 이력: id → DEVICE_FIELDS (state.json 에 저장)
known_devices: dict[str, dict] = {}

def _new_session_generation() -> int:
    """세션 세대 값으로 쓸 무작위 정수 (JSON 에서 정밀도를 잃지 않도록 53비트)."""
    return secrets.randbits(53)


# 로그인 세션의 "세대". 세션 쿠키에는 발급 당시의 세대 값이 들어가고, 지금 값과 같을 때만 유효하다.
# 세션은 서버에 저장하지 않는 서명 쿠키라 개별 삭제가 불가능하므로, 로그아웃할 때 세대를 새 값으로 바꿔
# 그때까지 발급된 쿠키를 한꺼번에 폐기한다. 시각이 아니라 무작위 값의 일치로 판정하므로 시계와 무관하다.
# 기본값도 무작위다: state.json 을 잃어버리면 폐기했던 쿠키가 되살아나는 대신
# 모두가 한 번 다시 로그인하게 된다 (fail closed). 저장된 값이 있으면 restore_state() 가 그 값을 쓴다.
session_generation: int = _new_session_generation()

# presets.json 읽기-수정-쓰기 직렬화용 락
_presets_lock = asyncio.Lock()

# 활성화 직렬화용 락 — 전역 상태 교체와 브로드캐스트를 한 단위로 묶어
# 동시 활성화 시 "미리보기는 A, 실제 패널은 B"가 되는 것을 방지
_activate_lock = asyncio.Lock()

# state.json 쓰기 직렬화용 락
_state_lock = asyncio.Lock()

# 프레임 전송 간격 제한용 상태 (_activate_lock 안에서만 읽고 쓴다)
_last_push_at: float = float("-inf")           # 마지막으로 실제 전송한 시각 (time.monotonic)
_deferred_push: Optional[asyncio.Task] = None  # 간격이 지난 뒤 최신 프레임을 보낼 예약 작업
_deferred_force: bool = False                  # 예약된 전송에 force_refresh 를 붙일지

# 디바이스 이력 저장 예약 작업 (디바운스)
_persist_task: Optional[asyncio.Task] = None

# 제안을 받아 바꾼 프리셋을 일정 종료 시각에 되돌리는 예약 (하나만 유지, state.json 에 저장)
# {"revert_to": 바꾸기 직전 프리셋 id, "expected": 제안으로 적용한 id, "at": aware UTC datetime}
pending_revert: Optional[dict] = None
_revert_task: Optional[asyncio.Task] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_until(value) -> Optional[datetime]:
    """일정 종료 시각 문자열을 aware UTC datetime 으로. 형식이 틀리면 None. 오프셋이 없으면 UTC 로 본다."""
    if not isinstance(value, str) or len(value) > MAX_UNTIL_LENGTH:
        return None
    m = UNTIL_RE.fullmatch(value.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, offset = m.groups()
    try:
        moment = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0),
                          tzinfo=timezone.utc)
    except ValueError:
        return None
    if offset and offset != "Z":
        hours, minutes = int(offset[1:3]), int(offset[4:6])
        if hours > 23 or minutes > 59:
            return None
        delta = timedelta(hours=hours, minutes=minutes)
        moment = moment - delta if offset[0] == "+" else moment + delta
    return moment


def _safe_next(value) -> Optional[str]:
    """로그인 후 돌아갈 주소. 허용 목록(/admin, /suggest…)에 맞으면 그대로, 아니면 None."""
    if isinstance(value, str) and len(value) <= MAX_NEXT_LENGTH and NEXT_RE.fullmatch(value):
        return value
    return None


# ──────────────────────────────────────────────
#  데이터 영속화 (JSON 파일 기반)
# ──────────────────────────────────────────────

def ensure_directories():
    """서버 구동에 필요한 디렉토리와 기본 파일을 생성한다."""
    DATA_DIR.mkdir(exist_ok=True)
    IMAGES_DIR.mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)
    if not PRESETS_FILE.exists():
        PRESETS_FILE.write_text("[]", encoding="utf-8")


def _atomic_write_json(path: Path, data) -> None:
    """
    임시 파일에 쓴 뒤 os.replace 로 교체한다.
    쓰는 도중 프로세스가 죽어도 기존 파일이 반쪽짜리로 남지 않는다.
    (mkstemp 가 만드는 파일은 600 권한이다)
    """
    payload = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    # 임시 파일 이름을 매번 다르게 한다 — 두 쓰기가 겹쳐도 서로의 임시 파일을 건드리지 않는다
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _valid_preset_entry(entry) -> bool:
    """프리셋 항목이 이 서버가 저장하는 형식인지 검사한다 (id 는 경로에 쓰이므로 형식까지 본다)."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("id"), str) and PRESET_ID_RE.fullmatch(entry["id"]) is not None
        and isinstance(entry.get("name"), str) and entry["name"].strip() != ""
    )


def load_presets() -> list[dict]:
    """
    presets.json에서 프리셋 목록을 읽어온다. 파일이 없거나 내용이 깨졌으면 빈 목록.
    형식에 맞지 않는 항목은 건너뛴다 (그런 항목 하나 때문에 목록 전체가 500 이 되지 않게).
    깨진 파일의 원본 보관은 시작 시 quarantine_corrupt_presets() 가 한다.
    """
    try:
        # utf-8-sig: 편집기가 붙인 BOM 이 있어도 정상 파일로 읽는다
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError):     # ValueError: JSON·UTF-8 디코딩 오류
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if _valid_preset_entry(entry)]


def quarantine_corrupt_presets() -> None:
    """
    시작 시 1회 실행. presets.json 이 깨졌거나 형식에 맞지 않는 항목이 있으면
    원본을 presets.json.corrupt-<시각> 으로 보관하고 유효한 항목만 남긴다.
    (그대로 두면 다음 저장이 원본을 조용히 덮어써 복구할 길이 없어진다)
    """
    try:
        raw = PRESETS_FILE.read_bytes()
    except FileNotFoundError:
        return
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        data = None
    if isinstance(data, list) and all(_valid_preset_entry(entry) for entry in data):
        return

    backup = PRESETS_FILE.with_name(f"presets.json.corrupt-{int(time.time())}")
    backup.write_bytes(raw)
    valid = [entry for entry in data if _valid_preset_entry(entry)] if isinstance(data, list) else []
    save_presets(valid)
    logger.error(f"❌ presets.json 이 손상되어 원본을 {backup.name} 으로 보관하고 유효한 {len(valid)}개만 남겼습니다.")


def save_presets(presets: list[dict]):
    """프리셋 목록을 presets.json에 원자적으로 저장한다."""
    _atomic_write_json(PRESETS_FILE, presets)


def get_preset(preset_id: str) -> Optional[dict]:
    """ID로 프리셋을 조회한다. 없으면 None 반환."""
    return next((p for p in load_presets() if p["id"] == preset_id), None)


def generate_preset_id(existing_ids: set[str]) -> str:
    """8자리 hex ID를 생성한다. 기존 ID와 겹치면 다시 뽑는다."""
    while True:
        candidate = uuid_lib.uuid4().hex[:8]
        if candidate not in existing_ids:
            return candidate


def load_state() -> dict:
    """state.json 을 읽는다. 없거나 읽을 수 없거나 깨졌으면 빈 dict (상태는 없어도 서버는 떠야 한다)."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.error(f"❌ state.json 을 읽지 못해 빈 상태로 시작합니다: {e!r}")
        return {}


def _pending_revert_json() -> Optional[dict]:
    entry = pending_revert
    if entry is None:
        return None
    return {"revert_to": entry["revert_to"], "expected": entry["expected"],
            "at": entry["at"].isoformat(timespec="seconds")}


async def persist_state() -> bool:
    """
    마지막 활성 프리셋, 디바이스 이력, 세션 세대를 state.json 에 저장한다.
    저장 실패(디스크 가득 참 등)는 로그를 남기고 False 를 돌려준다 — 이미 반영된 화면 변경을
    500 응답으로 만들지 않기 위해 예외를 던지지 않는다. 성공하면 True.
    """
    async with _state_lock:
        snapshot = {
            "active_preset_id": current_preset_id,
            "session_generation": session_generation,
            "devices": {device_id: dict(info) for device_id, info in known_devices.items()},
            "pending_revert": _pending_revert_json(),
        }
        try:
            await asyncio.to_thread(_atomic_write_json, STATE_FILE, snapshot)
        except (OSError, ValueError) as e:
            logger.error(f"❌ state.json 저장 실패: {e!r}")
            return False
        return True


def schedule_persist() -> None:
    """디바이스 이력 저장을 예약한다. 이미 예약돼 있으면 아무 일도 하지 않는다 (디바운스)."""
    global _persist_task

    async def _later():
        await asyncio.sleep(STATE_PERSIST_DELAY)
        await persist_state()

    if _persist_task is None or _persist_task.done():
        _persist_task = asyncio.create_task(_later())


def restore_state() -> None:
    """
    서버 시작 시 마지막 활성 프리셋의 프레임을 다시 만들어 둔다.
    재시작 직후 접속한 ESP32 가 곧바로 화면을 복원할 수 있다.
    """
    global current_status_text, current_preset_id, current_frame_bytes, current_display_image
    global session_generation, pending_revert

    state = load_state()

    # 저장된 세션 세대가 있으면 이어서 쓴다 — 재시작해도 로그인이 유지된다.
    # 없거나 형식이 틀리면 무작위 기본값을 그대로 둔다 = 전원 재로그인 (fail closed).
    generation = state.get("session_generation")
    if isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0:
        session_generation = generation

    # 파일 내용도 디바이스 보고와 같은 기준으로 다시 검증한다 (임시 ip-… 항목은 복원하지 않는다)
    devices = state.get("devices")
    if isinstance(devices, dict):
        for device_id, raw in list(devices.items())[:MAX_KNOWN_DEVICES]:
            entry = clean_device_entry(device_id, raw)
            if entry is not None:
                known_devices[device_id] = entry

    # 일정 종료 복귀 예약 — 두 ID 와 시각이 모두 올바를 때만 (지난 시각이면 lifespan 이 곧바로 처리)
    raw = state.get("pending_revert")
    if isinstance(raw, dict):
        revert_to, expected, moment = raw.get("revert_to"), raw.get("expected"), parse_until(raw.get("at"))
        if moment is not None and all(isinstance(i, str) and PRESET_ID_RE.fullmatch(i) for i in (revert_to, expected)):
            pending_revert = {"revert_to": revert_to, "expected": expected, "at": moment}

    preset_id = state.get("active_preset_id")
    if not isinstance(preset_id, str) or not PRESET_ID_RE.fullmatch(preset_id):
        return
    preset = get_preset(preset_id)
    if not preset:
        return
    try:
        img = load_preset_image(preset_id)
    except (FileNotFoundError, OSError):
        return

    current_status_text = preset["name"]
    current_preset_id = preset_id
    current_display_image = img
    current_frame_bytes = image_to_1bit_bytes(img)
    logger.info(f"♻️  마지막 활성 프리셋 복원: '{preset['name']}'")


# ──────────────────────────────────────────────
#  WebSocket 클라이언트 관리자
# ──────────────────────────────────────────────

class ConnectionManager:
    """
    연결된 WebSocket 클라이언트(ESP32)와 각 연결의 디바이스 정보를 관리한다.
    """

    def __init__(self):
        # websocket → 디바이스 정보(dict, DEVICE_FIELDS)
        self.connections: dict[WebSocket, dict] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> Optional[dict]:
        """
        연결을 수락하고 목록에 추가한다. 이 연결의 디바이스 정보를 돌려준다.
        이미 MAX_DEVICE_CONNECTIONS 개가 붙어 있으면 수락하지 않고 None 을 돌려준다.
        """
        host = websocket.client.host if websocket.client else "unknown"
        now = _now_iso()
        # hello 메시지가 오기 전까지는 IP 기반 임시 ID 를 쓴다
        info = {"id": f"ip-{host}", "ip": host, "connected_at": now, "last_seen": now}
        async with self._lock:
            if len(self.connections) >= MAX_DEVICE_CONNECTIONS:
                return None
            await websocket.accept()
            self.connections[websocket] = info
        remember_device(info)
        logger.info(
            f"✅ 새 클라이언트 연결: {websocket.client}  (현재 연결 수: {len(self.connections)})"
        )
        return info

    async def disconnect(self, websocket: WebSocket):
        """클라이언트를 목록에서 제거한다. 이미 제거된 경우 아무 일도 하지 않는다."""
        async with self._lock:
            info = self.connections.pop(websocket, None)
        if info is None:
            return
        info["last_seen"] = _now_iso()
        remember_device(info)
        logger.info(
            f"⚠️  클라이언트 연결 해제: {websocket.client}  (현재 연결 수: {len(self.connections)})"
        )

    async def _broadcast(self, send) -> int:
        """
        모든 클라이언트에 동시에 전송한다. 실패하거나 타임아웃된 클라이언트는
        목록에서 빼고 소켓을 닫아, 디바이스가 스스로 재연결하게 한다.

        @return  전송에 성공한 클라이언트 수
        """
        async with self._lock:
            connections = list(self.connections)

        if not connections:
            return 0

        async def _send(conn: WebSocket):
            await asyncio.wait_for(send(conn), timeout=WS_SEND_TIMEOUT)

        results = await asyncio.gather(
            *(_send(conn) for conn in connections),
            return_exceptions=True,
        )

        failed = [
            conn for conn, result in zip(connections, results)
            if isinstance(result, BaseException)
        ]
        for conn, result in zip(connections, results):
            if isinstance(result, BaseException):
                logger.warning(f"❌ 전송 실패 ({conn.client}): {result!r}")

        for conn in failed:
            await self.disconnect(conn)
            try:
                await conn.close(code=1011)
            except Exception:
                pass
        if failed:
            logger.info(f"🧹 비정상 클라이언트 {len(failed)}개 정리 완료")

        return len(connections) - len(failed)

    async def broadcast_bytes(self, data: bytes) -> int:
        return await self._broadcast(lambda conn: conn.send_bytes(data))

    async def broadcast_text(self, text: str) -> int:
        return await self._broadcast(lambda conn: conn.send_text(text))

    @property
    def client_count(self) -> int:
        """현재 연결된 클라이언트 수를 반환한다."""
        return len(self.connections)

    def connected_device_ids(self) -> set[str]:
        return {info["id"] for info in self.connections.values()}


# 전역 연결 관리자 인스턴스
manager = ConnectionManager()


def remember_device(info: dict) -> None:
    """연결 정보를 접속 이력에 반영하고, 이력이 너무 길면 오래된 오프라인 항목부터 지운다."""
    known_devices[info["id"]] = {k: info[k] for k in DEVICE_FIELDS if k in info}

    overflow = len(known_devices) - MAX_KNOWN_DEVICES
    if overflow > 0:
        connected = manager.connected_device_ids()
        offline = sorted(
            (d for d in known_devices.values() if d["id"] not in connected),
            key=lambda d: str(d.get("last_seen", "")),
        )
        for stale in offline[:overflow]:
            known_devices.pop(stale["id"], None)


def _valid_rssi(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and -127 <= value <= 0


def _valid_uptime(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_DEVICE_UPTIME_S


def clean_device_entry(device_id, raw) -> Optional[dict]:
    """state.json 에서 읽은 디바이스 항목을 검증한다. 형식에 맞는 필드만 남기고, ID 가 이상하면 None."""
    if not (isinstance(device_id, str) and DEVICE_ID_RE.fullmatch(device_id) and isinstance(raw, dict)):
        return None
    if device_id.startswith("ip-"):      # hello 전의 임시 ID — 재시작 뒤에는 의미가 없다
        return None

    entry: dict = {"id": device_id}
    text_rules = {
        "ip": DEVICE_ADDR_RE, "fw": DEVICE_TEXT_RE, "reset": DEVICE_TEXT_RE,
        "connected_at": DEVICE_TIME_RE, "last_seen": DEVICE_TIME_RE,
    }
    for key, pattern in text_rules.items():
        value = raw.get(key)
        if isinstance(value, str) and pattern.fullmatch(value):
            entry[key] = value
    if _valid_rssi(raw.get("rssi")):
        entry["rssi"] = raw["rssi"]
    if _valid_uptime(raw.get("uptime_s")):
        entry["uptime_s"] = raw["uptime_s"]
    return entry


def apply_device_report(info: dict, report: dict, accept_identity: bool) -> bool:
    """
    ESP32 가 보낸 hello / status 보고를 연결 정보에 반영한다.
    값은 신뢰하지 않고 타입·범위를 검사해 통과한 것만 받는다.

    @param accept_identity  hello 의 id/fw/reset 을 받아들일지. 연결당 첫 hello 에만 True 를 넘겨
                            한 연결이 ID 를 계속 바꿔 가며 이력을 오염시키지 못하게 한다.
    @return  반영했으면 True
    """
    kind = report.get("type")
    if kind not in ("hello", "status"):
        return False

    if kind == "hello" and accept_identity:
        device_id = report.get("id")
        if isinstance(device_id, str) and DEVICE_ID_RE.fullmatch(device_id) and device_id != info["id"]:
            # 임시 ID 항목은 실제 ID 로 대체한다. 같은 IP 의 다른 연결이 아직 그 임시 ID 를 쓰고 있으면 남겨 둔다.
            shared = any(
                other is not info and other["id"] == info["id"] for other in manager.connections.values()
            )
            if info["id"].startswith("ip-") and not shared:
                known_devices.pop(info["id"], None)
            info["id"] = device_id
        for key in ("fw", "reset"):
            value = report.get(key)
            if isinstance(value, str) and DEVICE_TEXT_RE.fullmatch(value):
                info[key] = value

    if _valid_rssi(report.get("rssi")):
        info["rssi"] = report["rssi"]
    if _valid_uptime(report.get("uptime_s")):
        info["uptime_s"] = report["uptime_s"]

    info["last_seen"] = _now_iso()
    remember_device(info)
    return True


def device_snapshot() -> list[dict]:
    """접속 이력에 현재 연결 여부를 붙여 돌려준다 (연결된 것 먼저, 최근 순)."""
    connected = manager.connected_device_ids()
    devices = [{**info, "connected": info["id"] in connected} for info in known_devices.values()]
    devices.sort(key=lambda d: str(d.get("last_seen", "")), reverse=True)
    devices.sort(key=lambda d: not d["connected"])
    return devices


# ──────────────────────────────────────────────
#  이미지 처리 함수
# ──────────────────────────────────────────────

def load_preset_image(preset_id: str) -> Image.Image:
    """
    프리셋 ID에 해당하는 저장 이미지를 로드한다.
    (저장 시 이미 416×240으로 맞춰지지만, 손으로 바꿔 넣은 경우를 대비해 한 번 더 보정)

    @param preset_id  프리셋 고유 ID
    @return           416×240 크기의 RGB PIL Image
    """
    image_path = IMAGES_DIR / f"{preset_id}.png"
    if not image_path.exists():
        raise FileNotFoundError(f"이미지 파일 없음: {image_path}")
    img = Image.open(image_path).convert("RGB")
    if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
        img = resize_with_letterbox(img)
    return img


def resize_with_letterbox(img: Image.Image) -> Image.Image:
    """
    이미지를 416×240에 맞게 리사이즈한다.
    비율이 다를 경우 흰색 여백(레터박스)을 추가하여 비율을 유지한다.
    """
    target_w, target_h = DISPLAY_WIDTH, DISPLAY_HEIGHT
    ratio = min(target_w / img.width, target_h / img.height)
    new_w = max(1, int(img.width * ratio))
    new_h = max(1, int(img.height * ratio))

    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    result = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    result.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return result


def decode_upload(content: bytes) -> Image.Image:
    """
    업로드 바이트를 검증하고 RGB 이미지로 디코딩한다.

    Pillow의 open()은 헤더만 읽으므로, 실제 픽셀 디코딩(convert) 전에
    선언된 해상도를 먼저 검사해 decompression bomb을 차단한다.

    @raises ValueError  이미지가 아니거나 해상도가 한도를 초과할 때
    """
    try:
        img = Image.open(BytesIO(content))
    except Image.DecompressionBombError:
        # Pillow 자체 한도(약 179MP)를 넘는 헤더는 open() 단계에서 바로 거부된다
        raise ValueError("이미지 해상도가 너무 큽니다.")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("유효하지 않은 이미지 파일입니다.")

    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise ValueError(
            f"이미지 해상도가 너무 큽니다 ({img.width}×{img.height}). "
            f"{MAX_IMAGE_PIXELS // 1_000_000}MP 이하로 줄여주세요."
        )

    try:
        return img.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ValueError("이미지를 디코딩할 수 없습니다.")


def image_to_1bit_bytes(img: Image.Image) -> bytes:
    """
    RGB 이미지를 ESP32가 그대로 EPD_Display()에 넘길 수 있는
    12,480바이트 1-bit 프레임으로 변환한다.

    1. convert("1")  — Floyd-Steinberg 디더링. 단순 임계값 방식은 이모지·컬러
                       이미지의 형태를 날려버리므로 반드시 디더링을 사용한다.
    2. ROTATE_90 → FLIP_LEFT_RIGHT — UC8253 컨트롤러의 메모리 스캔 방향에 맞춘
                       회전/반전. 순서를 바꾸면 화면이 뒤집힌다.
    3. tobytes()     — 8픽셀을 1바이트로 패킹 (MSB = 첫 픽셀, 1 = 흰색).

    @param img  416×240 RGB PIL Image
    @return     12,480 바이트
    """
    binary = img.convert("1")
    binary = binary.transpose(Image.Transpose.ROTATE_90)
    binary = binary.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    raw_bytes = binary.tobytes()

    # 회전 후 폭이 240px(=30바이트, 8의 배수)이므로 행 패딩은 발생하지 않는다.
    # 상수를 바꿔 폭이 8의 배수가 아니게 되는 경우를 대비한 방어 코드.
    if len(raw_bytes) != FRAME_BUFFER_SIZE:
        logger.warning(
            f"⚠️  바이트 배열 크기 불일치: 예상 {FRAME_BUFFER_SIZE}, 실제 {len(raw_bytes)} — 수동 패킹"
        )
        raw_bytes = _manual_pack_1bit(binary)

    return raw_bytes


def _manual_pack_1bit(img: Image.Image) -> bytes:
    """
    행 패딩 없이 픽셀을 1-bit 바이트 배열로 직접 패킹한다 (tobytes() 폴백).

    @param img  '1' 모드 PIL Image
    @return     FRAME_BUFFER_SIZE 바이트
    """
    if img.mode != "1":
        img = img.convert("1")

    pixels = img.load()
    byte_array = bytearray(FRAME_BUFFER_SIZE)

    for y in range(img.height):
        for x in range(img.width):
            pixel_index = y * img.width + x
            if pixels[x, y]:  # 0 = 검정, 그 외 = 흰색
                byte_array[pixel_index // 8] |= 1 << (7 - pixel_index % 8)

    return bytes(byte_array)


def render_preview_png(img: Image.Image) -> bytes:
    """
    E-ink에 실제 표시될 1-bit 디더링 결과를 PNG로 렌더링한다 (브라우저 미리보기용).
    """
    buf = BytesIO()
    img.convert("1").save(buf, format="PNG")
    return buf.getvalue()


# ──────────────────────────────────────────────
#  텍스트 → 이미지 렌더링
# ──────────────────────────────────────────────

def find_font_path() -> Optional[str]:
    """사용 가능한 첫 번째 한글 폰트 경로를 돌려준다."""
    return next((path for path in FONT_CANDIDATES if path and Path(path).is_file()), None)


def clean_display_text(raw: str) -> str:
    """
    화면에 찍을 텍스트를 정리한다: 이모지·제어문자 제거, 줄 단위 trim, 앞뒤 빈 줄 제거.

    @raises HTTPException(422)  남는 글자가 없거나 줄 수가 한도를 넘을 때
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _EMOJI_RE.sub("", text)
    text = "".join(ch for ch in text if ch == "\n" or (ord(ch) >= 32 and ch != "\x7f"))

    lines = [line.strip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    if not lines:
        raise HTTPException(422, "표시할 글자가 없습니다. 이모지는 텍스트 프리셋에서 지원하지 않습니다.")
    if len(lines) > TEXT_MAX_LINES:
        raise HTTPException(422, f"텍스트는 최대 {TEXT_MAX_LINES}줄까지 입력할 수 있습니다.")
    return "\n".join(lines)


def _split_long_word(draw: ImageDraw.ImageDraw, word: str, font, max_width: int) -> list[str]:
    """한 줄에 들어가지 않는 단어를 글자 단위로 잘라 max_width 이하의 조각들로 나눈다."""
    pieces: list[str] = []
    current = ""
    for ch in word:
        if current and draw.textlength(current + ch, font=font) > max_width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    pieces.append(current)
    return pieces


def _wrap_paragraph(
    draw: ImageDraw.ImageDraw, paragraph: str, font, max_width: int, split_long_words: bool
) -> list[str]:
    """
    공백 기준으로 단어를 이어 붙이며 max_width 를 넘으면 줄을 바꾼다.
    split_long_words 가 True 면 한 줄보다 긴 단어(띄어쓰기 없는 긴 문장·URL)를 글자 단위로 쪼갠다.
    """
    lines: list[str] = []
    current = ""
    for word in paragraph.split(" "):
        pieces = [word]
        if split_long_words and draw.textlength(word, font=font) > max_width:
            pieces = _split_long_word(draw, word, font, max_width)

        for index, piece in enumerate(pieces):
            candidate = piece if not current else f"{current} {piece}"
            fits = not current or draw.textlength(candidate, font=font) <= max_width
            if index == 0 and fits:
                current = candidate
            else:                       # 쪼갠 조각의 둘째부터는 항상 새 줄에서 시작한다
                lines.append(current)
                current = piece
    lines.append(current)
    return lines


def _fit_text(draw: ImageDraw.ImageDraw, paragraphs: list[str], font_path: str,
              max_size: int, split_long_words: bool) -> Optional[dict]:
    """
    여백 안에 들어가는 가장 큰 글자 크기의 배치를 찾는다 (크기에 대해 이분 탐색).
    @return  {"size", "font", "text", "spacing", "bbox"} 또는 어떤 크기로도 안 들어가면 None
    """
    max_w = DISPLAY_WIDTH - 2 * TEXT_MARGIN
    max_h = DISPLAY_HEIGHT - 2 * TEXT_MARGIN

    def layout(size: int) -> Optional[dict]:
        font = ImageFont.truetype(font_path, size)
        spacing = max(2, size // 5)
        text = "\n".join(
            line for paragraph in paragraphs
            for line in _wrap_paragraph(draw, paragraph, font, max_w, split_long_words)
        )
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
        if bbox[2] - bbox[0] > max_w or bbox[3] - bbox[1] > max_h:
            return None
        return {"size": size, "font": font, "text": text, "spacing": spacing, "bbox": bbox}

    best = None
    low, high = TEXT_MIN_FONT_SIZE, max(TEXT_MIN_FONT_SIZE, max_size)
    while low <= high:
        middle = (low + high) // 2
        candidate = layout(middle)
        if candidate is not None:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def render_text_image(text: str, max_font_size: Optional[int] = None) -> Image.Image:
    """
    텍스트를 416×240 흰 바탕에 가운데 정렬로 그린다.
    화면 여백 안에 들어가는 가장 큰 글자 크기를 찾아 자동으로 맞춘다.

    안티앨리어싱 없이(fontmode "1") 순수 흑백으로 그리므로, 이후 파이프라인의
    디더링이 글자 가장자리에 노이즈를 만들지 않는다.

    @param text           clean_display_text() 를 거친 텍스트
    @param max_font_size  글자 크기 상한 (None 이면 TEXT_MAX_FONT_SIZE)
    @raises RuntimeError  한글 폰트를 찾지 못했을 때
    @raises ValueError    가장 작은 글자로도 화면에 들어가지 않을 때
    """
    font_path = find_font_path()
    if font_path is None:
        raise RuntimeError("한글 폰트를 찾을 수 없습니다. FONT_PATH 환경변수를 설정하세요.")

    canvas = Image.new("1", (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)
    draw = ImageDraw.Draw(canvas)
    draw.fontmode = "1"

    paragraphs = text.split("\n")
    max_size = min(max_font_size or TEXT_MAX_FONT_SIZE, TEXT_MAX_FONT_SIZE)

    # 단어를 쪼개지 않는 배치를 우선한다. 다만 띄어쓰기 없는 긴 문장처럼 단어를 쪼개야
    # 글자가 훨씬(1.5배 이상) 커지거나, 쪼개지 않으면 아예 들어가지 않을 때는 글자 단위로 줄을 바꾼다.
    by_word = _fit_text(draw, paragraphs, font_path, max_size, split_long_words=False)
    by_char = _fit_text(draw, paragraphs, font_path, max_size, split_long_words=True)
    if by_word is not None and (by_char is None or by_char["size"] < by_word["size"] * 1.5):
        fit = by_word
    else:
        fit = by_char
    if fit is None:
        raise ValueError("텍스트가 너무 길어 화면에 들어가지 않습니다. 글자 수나 줄 수를 줄여 주세요.")

    left, top, right, bottom = fit["bbox"]
    x = (DISPLAY_WIDTH - (right - left)) // 2 - left
    y = (DISPLAY_HEIGHT - (bottom - top)) // 2 - top
    draw.multiline_text((x, y), fit["text"], font=fit["font"], fill=0, spacing=fit["spacing"], align="center")
    return canvas.convert("RGB")


# ──────────────────────────────────────────────
#  FastAPI 애플리케이션
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트 핸들러"""
    ensure_directories()
    logger.info("========================================")
    logger.info("  E-ink Status Board Server v3.0 시작")
    logger.info("========================================")
    logger.info(f"디스플레이 해상도: {DISPLAY_WIDTH}×{DISPLAY_HEIGHT}")
    logger.info(f"데이터 디렉토리: {DATA_DIR}")
    logger.info(f"Admin 페이지: {PUBLIC_BASE_URL or f'http://localhost:{SERVER_PORT}'}/admin")
    if AUTH_DISABLED:
        logger.warning("🔓 AUTH_DISABLED=true — 모든 API가 무인증입니다. 로컬 개발에서만 쓰세요.")
    else:
        logger.info(f"🔐 Google 로그인 허용 계정: {len(ALLOWED_EMAILS)}개")
        if not API_KEY:
            logger.warning("API_KEY 미설정 — Apple 단축어 경로는 로그인 세션으로만 접근 가능합니다.")
        if not DEVICE_TOKEN:
            logger.warning("DEVICE_TOKEN 미설정 — 어떤 디바이스도 /ws 에 접속할 수 없습니다.")
    # 데이터 파일에 문제가 있어도 서버는 떠야 한다 (원인을 로그로 남기고 계속)
    # (프리셋과 상태는 따로 처리한다 — 프리셋 쪽 오류가 세션 세대 복원을 막지 않도록)
    try:
        quarantine_corrupt_presets()
        logger.info(f"📌 등록된 프리셋: {len(load_presets())}개")
    except Exception as e:
        logger.error(f"❌ 프리셋 파일을 불러오지 못했습니다: {e!r}")
    try:
        restore_state()
    except Exception as e:
        logger.error(f"❌ 저장된 상태를 불러오지 못했습니다: {e!r}")
    # 세션 세대를 바로 저장해 둔다 — 다음 재시작에도 같은 값을 써서 로그인이 유지되게
    await persist_state()
    # 복원된 일정 종료 복귀 예약 (이미 지난 시각이면 곧바로 처리된다)
    _schedule_revert()
    logger.info("========================================")
    yield
    # 복귀 타이머는 끄기만 한다 — 예약 자체는 state.json 에 남아 다음 시작 때 이어진다
    if _revert_task is not None and not _revert_task.done():
        _revert_task.cancel()
        await asyncio.gather(_revert_task, return_exceptions=True)
    # 종료 시 예약돼 있던 디바이스 이력 저장을 마무리한다
    if _persist_task is not None and not _persist_task.done():
        _persist_task.cancel()
        await asyncio.gather(_persist_task, return_exceptions=True)
    await persist_state()


app = FastAPI(
    title="E-ink Status Board Server",
    description="ESP32 전자잉크 디스플레이를 위한 실시간 상태 푸시 서버 (Admin + Presets)",
    version="3.0.0",
    lifespan=lifespan,
)


# ──────────────────────────────────────────────
#  인증
# ──────────────────────────────────────────────

def _secret_matches(provided: str, expected: str) -> bool:
    """상수 시간 비교. expected 가 비어 있으면 항상 거부한다."""
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _session_email(request: Request) -> Optional[str]:
    """
    세션에 담긴 이메일이 지금도 허용 목록에 있고, 세션이 현재 세대의 것이면 돌려준다.
    로그인 시점뿐 아니라 요청마다 검사하므로, 목록에서 뺀 계정은 재시작 즉시 차단된다.
    """
    session = request.session
    if not isinstance(session, dict):
        return None
    email = session.get("email")
    generation = session.get("gen")
    if not isinstance(email, str) or email not in ALLOWED_EMAILS:
        return None
    # 지금 세대의 세션만 받는다 (로그아웃으로 세대가 바뀌면 이전 쿠키는 전부 무효)
    if not isinstance(generation, int) or isinstance(generation, bool) or generation != session_generation:
        return None
    return email


def _api_key_from(request: Request) -> str:
    key = request.headers.get("x-api-key", "")
    if not key:
        authorization = request.headers.get("authorization", "")
        if authorization[:7].lower() == "bearer ":
            key = authorization[7:].strip()
    return key


def _device_authorized(websocket: WebSocket) -> bool:
    if AUTH_DISABLED:
        return True
    return _secret_matches(websocket.headers.get("x-device-token", ""), DEVICE_TOKEN)


def _message_page(title: str, message: str, status_code: int, link_label: str = "로그인 페이지로") -> HTMLResponse:
    """로그인 실패·로그아웃 안내용 최소 HTML 페이지. 링크는 로그인 전 메인 페이지(/)로 보낸다."""
    return HTMLResponse(
        status_code=status_code,
        content=f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
<title>{html.escape(title)} — E-ink Status Board</title>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px;
         box-sizing:border-box; background:#010102; color:#f7f8f8; -webkit-font-smoothing:antialiased;
         font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo",sans-serif; }}
  main {{ width:100%; max-width:380px; padding:32px; text-align:center; background:#0f1011;
         border:1px solid #23252a; border-radius:12px; box-sizing:border-box; }}
  img {{ display:block; margin:0 auto 20px; }}
  h1 {{ font-size:22px; font-weight:600; letter-spacing:-0.4px; margin:0 0 8px; }}
  p {{ font-size:14px; color:#8a8f98; line-height:1.5; margin:0 0 24px; }}
  a {{ display:inline-flex; align-items:center; height:36px; padding:0 14px; border-radius:8px;
       text-decoration:none; background:#5e6ad2; color:#fff; font-size:14px; font-weight:500; }}
  a:hover {{ background:#828fff; }}
  a:focus-visible {{ outline:2px solid #828fff; outline-offset:2px; }}
</style></head>
<body><main><img src="/favicon.svg" width="36" height="36" alt="">
<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>
<a href="/">{html.escape(link_label)}</a></main></body></html>""",
    )


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """
    모든 HTTP 요청의 관문. 라우트 핸들러(와 multipart 본문 파싱)보다 먼저 실행된다.

    1. 인증 — PUBLIC_PATHS 가 아니면 허용된 Google 세션이 있어야 한다.
       API_KEY_PATHS 는 X-API-Key 로도 통과한다. 새 HTTP 라우트는 기본적으로 보호된다.
       (WebSocket 은 이 미들웨어를 거치지 않는다 — WS 라우트는 각자 인증해야 한다. /ws 참고)
    2. 본문 크기 — FastAPI/Starlette 는 엔드포인트 실행 전에 본문 전체를 받으므로(JSON 은 메모리,
       multipart 는 임시 파일), 한도를 넘는 Content-Length 는 여기서 413, chunked 는 411 로 끊는다.
    """
    # 라우터가 매칭에 쓰는 것과 같은 값(scope["path"])으로 판단한다.
    # request.url.path 는 URL 을 다시 파싱하므로 %3F/%23 이 섞인 경로에서 라우터와 어긋난다.
    path = request.scope["path"]

    if not AUTH_DISABLED and path not in PUBLIC_PATHS:
        authorized = _session_email(request) is not None or (
            path in API_KEY_PATHS and _secret_matches(_api_key_from(request), API_KEY)
        )
        if not authorized:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/", status_code=303)   # 브라우저는 로그인 전 메인 페이지로
            return JSONResponse(status_code=401, content={"detail": "로그인이 필요합니다."})

    # 본문 크기 — 길이를 미리 알 수 없는 chunked 본문은 받지 않고, Content-Length 로 한도를 검사한다.
    # (브라우저·Apple 단축어·curl 은 모두 Content-Length 를 보낸다.)
    if "transfer-encoding" in request.headers:
        return JSONResponse(status_code=411, content={"detail": "Content-Length 헤더가 필요합니다."})
    length = request.headers.get("content-length")
    if length:
        is_upload = request.method == "POST" and path == "/api/presets"
        limit = MAX_UPLOAD_SIZE + UPLOAD_OVERHEAD if is_upload else MAX_JSON_BODY_SIZE
        if not length.isdigit() or int(length) > limit:
            detail = (
                f"파일 크기는 {MAX_UPLOAD_SIZE // (1024 * 1024)}MB 이하여야 합니다."
                if is_upload else "요청 본문이 너무 큽니다."
            )
            return JSONResponse(status_code=413, content={"detail": detail})

    return await call_next(request)


# SessionMiddleware 는 auth_gate 보다 나중에 추가해야 바깥쪽에서 먼저 실행되어
# auth_gate 안에서 request.session 을 쓸 수 있다.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET or secrets.token_urlsafe(32),
    session_cookie="eink_session",
    max_age=SESSION_MAX_AGE,
    same_site="lax",                                  # 교차 사이트 POST 에는 쿠키가 실리지 않는다 (CSRF 방어)
    https_only=PUBLIC_IS_HTTPS,
)


@app.get("/healthz")
async def healthz():
    """프로세스 생존 확인용. 인증 없이 접근 가능하며 내부 정보는 노출하지 않는다."""
    return {"status": "ok"}


@app.get("/login")
async def login(request: Request):
    """
    Google 로그인 화면으로 보낸다. 메인 페이지(/)의 "Google 계정으로 로그인" 버튼이 여기로 온다.
    (Google 이 돌아오는 주소는 /auth/callback 으로 고정 — 이 경로는 리디렉션 URI 와 무관하다)
    """
    if AUTH_DISABLED or _session_email(request):
        return RedirectResponse("/admin", status_code=303)

    # (완료되지 않은 이전 시도의 state 는 Authlib 이 새 state 를 저장할 때 스스로 정리한다)
    try:
        return await oauth.google.authorize_redirect(
            request, f"{PUBLIC_BASE_URL}/auth/callback", prompt="select_account"
        )
    except Exception as e:
        # Google 의 OpenID 설정 문서를 받아오지 못한 경우 등
        logger.error(f"❌ Google 로그인 시작 실패: {e!r}")
        return _message_page("로그인 실패", "Google 과 통신하는 중 문제가 발생했습니다.", 502, "로그인 페이지로")


@app.get("/auth/callback")
async def auth_callback(request: Request):
    """Google 이 돌려준 인가 코드를 검증하고, 허용된 계정이면 세션을 발급한다."""
    if AUTH_DISABLED:
        return RedirectResponse("/admin", status_code=303)

    try:
        # state 검증, 코드 교환, id_token 서명·nonce 검증까지 Authlib 이 수행한다
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        # 여기서는 기존 세션을 지우지 않는다. 이 분기는 다른 사이트가 /auth/callback?error=x 로
        # 이동시키기만 해도 도달하므로(Lax 쿠키는 최상위 GET 에 실린다), 지우면 강제 로그아웃 수단이 된다.
        logger.warning(f"🚫 OAuth 실패: {str(e.error)[:64]!r}")   # error 는 쿼리스트링에서 온 값
        return _message_page("로그인 실패", "Google 로그인에 실패했습니다. 다시 시도해 주세요.", 400, "로그인 페이지로")
    except Exception as e:
        logger.error(f"❌ OAuth 처리 중 오류: {e!r}")
        return _message_page("로그인 실패", "Google 과 통신하는 중 문제가 발생했습니다.", 502, "로그인 페이지로")

    userinfo = token.get("userinfo") or {}
    email = str(userinfo.get("email", "")).strip().lower()

    # Google 로그인을 실제로 마친 경우에만 여기 도달한다(state 검증 통과) — 교차 사이트로는 유발할 수 없다
    request.session.clear()
    if userinfo.get("email_verified") is not True or email not in ALLOWED_EMAILS:
        logger.warning(f"🚫 허용되지 않은 계정의 로그인 시도: {email!r}")
        return _message_page("접근 권한 없음", "이 Google 계정은 접근이 허용되지 않았습니다.", 403, "다른 계정으로 로그인")

    request.session["email"] = email
    request.session["gen"] = session_generation   # 발급 당시의 세대 — 로그아웃으로 세대가 바뀌면 무효
    logger.info(f"🔑 로그인: {email}")
    return RedirectResponse("/admin", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    """
    로그아웃. 세션은 서버에 저장하지 않는 서명 쿠키라 하나만 골라 지울 수 없으므로,
    유효한 세션으로 로그아웃하면 세션 세대를 새 값으로 바꿔 그때까지 발급된 세션을 전부 폐기한다
    (다른 기기의 로그인과, 복사·유출된 쿠키도 함께 무효가 된다).
    세션 없이 온 요청(교차 사이트 POST 등)은 아무것도 폐기하지 않는다.
    """
    global session_generation

    saved = True
    if not AUTH_DISABLED and _session_email(request):
        previous = session_generation
        while session_generation == previous:
            session_generation = _new_session_generation()
        saved = await persist_state()
        logger.info("🚪 로그아웃 — 이전에 발급된 세션을 모두 폐기했습니다")
    request.session.clear()

    if not saved:
        # 폐기는 메모리에만 반영됐다. 재시작하면 저장돼 있던 이전 세대로 돌아가므로 사용자에게 알린다.
        return _message_page(
            "로그아웃 저장 실패",
            "이 브라우저에서는 로그아웃됐지만, 서버가 세션 폐기를 디스크에 저장하지 못했습니다. "
            "서버가 재시작되면 다른 기기의 로그인이 다시 유효해질 수 있으니 SESSION_SECRET 을 교체하세요.",
            500, "로그인 페이지로",
        )
    return _message_page("로그아웃됨", "로그아웃되었습니다.", 200, "로그인 페이지로")


# ──────────────────────────────────────────────
#  페이지 라우트
# ──────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    """
    로그인 전 메인 페이지. 이미 로그인돼 있으면(또는 인증이 꺼져 있으면) 관리자 페이지로 보낸다.
    로그인 버튼은 /login 으로 이어지고, 거기서 Google 로 넘어간다.
    """
    if AUTH_DISABLED or _session_email(request):
        return RedirectResponse(url="/admin", status_code=303)
    page = STATIC_DIR / "login.html"
    if not page.exists():
        return RedirectResponse(url="/login", status_code=303)
    return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/admin")
async def admin_page():
    """관리자 페이지(admin.html)를 서빙한다."""
    html_path = STATIC_DIR / "admin.html"
    if not html_path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "admin.html 파일을 찾을 수 없습니다."},
        )
    return FileResponse(html_path, media_type="text/html", headers={"Cache-Control": "no-cache"})


def _icon_route(filename: str, media_type: str):
    async def serve_icon():
        path = STATIC_DIR / filename
        if not path.exists():
            return Response(status_code=404)
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
    return serve_icon


for _url, (_filename, _media_type) in ICON_FILES.items():
    app.add_api_route(_url, _icon_route(_filename, _media_type), methods=["GET"], include_in_schema=False)


# ──────────────────────────────────────────────
#  상태 API
# ──────────────────────────────────────────────

@app.get("/status")
async def get_status(request: Request):
    """현재 표시 중인 프리셋, 연결·디바이스 정보, 로그인 계정을 조회한다."""
    return {
        "text": current_status_text,
        "active_preset_id": current_preset_id,
        "connected_clients": manager.client_count,
        "frame_ready": current_frame_bytes is not None,
        "frame_size_bytes": len(current_frame_bytes) if current_frame_bytes else 0,
        "devices": device_snapshot(),
        "auth_enabled": not AUTH_DISABLED,
        "user": None if AUTH_DISABLED else _session_email(request),
        "pending_revert": await asyncio.to_thread(_revert_info),
    }


@app.get("/current-preview.png")
async def current_preview():
    """
    현재 디스플레이에 표시 중인 화면의 미리보기를 PNG로 반환한다.
    아직 표시된 적 없으면 빈 흰색 화면을 반환한다.
    """
    img = current_display_image
    if img is None:
        img = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
    png_bytes = await asyncio.to_thread(render_preview_png, img)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/display/refresh")
async def refresh_display():
    """
    현재 화면을 디바이스에 강제로 다시 그리게 한다 (잔상 제거용).
    force_refresh 메시지를 먼저 보내 펌웨어의 CRC 중복 검사를 한 번 건너뛰게 한다.
    """
    async with _activate_lock:
        if current_frame_bytes is None:
            raise HTTPException(409, "표시 중인 화면이 없습니다. 먼저 프리셋을 적용하세요.")
        notified, deferred = await push_current_frame(force=True)

    logger.info(f"🔄 화면 강제 새로고침 → {'예약됨' if deferred else f'{notified}개 디바이스'}")
    return {"success": True, "clients_notified": notified, "deferred": deferred}


# ──────────────────────────────────────────────
#  WebSocket 엔드포인트
# ──────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    ESP32가 연결을 맺고 유지하는 WebSocket 엔드포인트.

    연결 흐름:
      0. X-Device-Token 헤더 검증 — 틀리면 핸드셰이크 단계에서 거부(HTTP 403)
      1. 연결 수락 및 관리 목록에 추가
      2. 이미 렌더링된 프레임이 있으면 즉시 전송 (최신 상태 동기화)
         펌웨어는 마지막으로 그린 프레임과 CRC 가 같으면 다시 그리지 않는다
      3. 연결 유지 — 디바이스가 보내는 hello/status 보고(JSON)를 받아 이력에 반영
      4. 연결 종료 시 목록에서 제거하고 이력 저장

    프레임 푸시는 활성화 API가 manager.broadcast_bytes()로 수행한다.
    """
    if not _device_authorized(websocket):
        logger.warning(f"🚫 디바이스 토큰이 없는/틀린 WebSocket 접속 거부: {websocket.client}")
        await websocket.close(code=1008)
        return

    info = await manager.connect(websocket)
    if info is None:
        logger.warning(f"🚫 디바이스 연결 한도({MAX_DEVICE_CONNECTIONS}) 초과 — 접속 거부: {websocket.client}")
        await websocket.close(code=1013)
        return

    hello_seen = False

    try:
        if current_frame_bytes is not None:
            await websocket.send_bytes(current_frame_bytes)

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            if text is None:
                continue   # 바이너리는 쓰지 않는다 — 무시하되 연결은 유지
            if len(text) > MAX_DEVICE_MESSAGE_CHARS or not text.startswith("{"):
                continue
            try:
                report = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(report, dict):
                continue
            first_hello = report.get("type") == "hello" and not hello_seen
            if apply_device_report(info, report, accept_identity=first_hello) and first_hello:
                hello_seen = True
                logger.info(
                    f"📟 디바이스 hello: id={info['id']} fw={info.get('fw')!r} "
                    f"rssi={info.get('rssi')} reset={info.get('reset')!r}"
                )
                schedule_persist()

    except WebSocketDisconnect:
        pass

    except Exception as e:
        logger.error(f"❌ 클라이언트 비정상 종료 ({websocket.client}): {e!r}")

    finally:
        await manager.disconnect(websocket)
        schedule_persist()


# ──────────────────────────────────────────────
#  프리셋 API
# ──────────────────────────────────────────────

def clean_preset_name(raw: str) -> str:
    """
    프리셋 이름을 검증한다. 이름은 단축어 API의 조회 키이자 줄바꿈 구분 목록의
    한 줄이므로, 공백만 있는 이름과 줄바꿈·제어문자를 허용하지 않는다.
    """
    name = raw.strip()
    if not name:
        raise HTTPException(422, "프리셋 이름을 입력하세요.")
    if len(name) > MAX_PRESET_NAME_LENGTH:
        raise HTTPException(422, f"프리셋 이름은 {MAX_PRESET_NAME_LENGTH}자 이하여야 합니다.")
    if any(ord(ch) < 32 or ch == "\x7f" for ch in name):
        raise HTTPException(422, "프리셋 이름에 줄바꿈이나 제어 문자를 쓸 수 없습니다.")
    return name


async def store_new_preset(name: str, img: Image.Image) -> dict:
    """416×240 이미지를 새 프리셋으로 저장한다. 이름 중복·개수 한도를 락 안에서 검사한다."""
    async with _presets_lock:
        presets = await asyncio.to_thread(load_presets)

        if any(p["name"].strip() == name for p in presets):
            raise HTTPException(409, f"'{name}' 이름의 프리셋이 이미 있습니다.")
        if len(presets) >= MAX_PRESETS:
            raise HTTPException(409, f"프리셋은 최대 {MAX_PRESETS}개까지 등록할 수 있습니다.")

        preset_id = generate_preset_id({p["id"] for p in presets})
        preset = {
            "id": preset_id,
            "name": name,
            "image_filename": f"{preset_id}.png",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        await asyncio.to_thread(img.save, IMAGES_DIR / preset["image_filename"], "PNG")
        presets.append(preset)
        await asyncio.to_thread(save_presets, presets)

    logger.info(f"📌 프리셋 생성: '{name}' (id={preset_id})")
    return preset


@app.get("/api/presets")
async def list_presets():
    """등록된 모든 프리셋을 저장된 순서대로 반환한다."""
    return await asyncio.to_thread(load_presets)


@app.post("/api/presets", status_code=201)
async def create_preset(
    name: str = Form(..., min_length=1, max_length=MAX_PRESET_NAME_LENGTH,
                     description="프리셋 이름 (단축어에서 이 이름으로 호출)"),
    image: UploadFile = File(..., description="프리셋 이미지 파일"),
):
    """
    이미지로 새 프리셋을 생성한다. 업로드된 이미지는 416×240 크기로 변환되어 저장된다.
    이름은 단축어 API의 키로 쓰이므로 중복을 허용하지 않는다.
    """
    name = clean_preset_name(name)

    # 파일을 청크로 읽어 메모리 사용량을 한도로 묶는다 (Content-Length 검사는 미들웨어에서 선행)
    buffer = bytearray()
    while chunk := await image.read(1024 * 1024):
        buffer.extend(chunk)
        if len(buffer) > MAX_UPLOAD_SIZE:
            raise HTTPException(413, f"파일 크기는 {MAX_UPLOAD_SIZE // (1024 * 1024)}MB 이하여야 합니다.")
    if not buffer:
        raise HTTPException(400, "빈 파일입니다.")

    try:
        img = await asyncio.to_thread(decode_upload, bytes(buffer))
    except ValueError as e:
        raise HTTPException(400, str(e))

    if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
        img = await asyncio.to_thread(resize_with_letterbox, img)

    return await store_new_preset(name, img)


class TextPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_PRESET_NAME_LENGTH)
    # 비우면 이름을 그대로 화면에 쓴다
    text: Optional[str] = Field(default=None, max_length=TEXT_MAX_CHARS)
    font_size: Optional[int] = Field(default=None, ge=TEXT_MIN_FONT_SIZE, le=TEXT_MAX_FONT_SIZE)


class TextPreviewRequest(BaseModel):
    text: str = Field(max_length=TEXT_MAX_CHARS)
    font_size: Optional[int] = Field(default=None, ge=TEXT_MIN_FONT_SIZE, le=TEXT_MAX_FONT_SIZE)


async def _render_text(text: str, font_size: Optional[int]) -> Image.Image:
    cleaned = clean_display_text(text)
    try:
        return await asyncio.to_thread(render_text_image, cleaned, font_size)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/presets/text", status_code=201)
async def create_text_preset(req: TextPresetRequest):
    """
    텍스트로 새 프리셋을 생성한다. 글자를 416×240 이미지로 렌더링해 저장하므로
    이후에는 이미지 프리셋과 똑같이 다뤄진다.
    """
    name = clean_preset_name(req.name)
    img = await _render_text(req.text if req.text and req.text.strip() else name, req.font_size)
    return await store_new_preset(name, img)


@app.post("/api/presets/text/preview")
async def preview_text_preset(req: TextPreviewRequest):
    """텍스트가 E-ink에 어떻게 보일지 PNG 로 돌려준다. 아무것도 저장하지 않는다."""
    img = await _render_text(req.text, req.font_size)
    png_bytes = await asyncio.to_thread(render_preview_png, img)
    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


class ReorderRequest(BaseModel):
    ids: list[str] = Field(max_length=MAX_PRESETS)


@app.put("/api/presets/order")
async def reorder_presets(req: ReorderRequest):
    """
    프리셋 순서를 바꾼다. ids 는 현재 프리셋 ID 전체의 순열이어야 한다.
    이 순서가 Admin 카드 순서이자 단축어 목록 순서다.
    """
    async with _presets_lock:
        presets = await asyncio.to_thread(load_presets)
        by_id = {p["id"]: p for p in presets}
        if len(req.ids) != len(by_id) or set(req.ids) != set(by_id):
            raise HTTPException(409, "프리셋 목록이 바뀌었습니다. 새로고침 후 다시 시도하세요.")
        reordered = [by_id[preset_id] for preset_id in req.ids]
        await asyncio.to_thread(save_presets, reordered)
    return reordered


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_PRESET_NAME_LENGTH)


@app.patch("/api/presets/{preset_id}")
async def rename_preset(preset_id: str, req: RenameRequest):
    """프리셋 이름을 바꾼다. 단축어는 이름으로 호출하므로 다른 프리셋과 겹칠 수 없다."""
    global current_status_text

    name = clean_preset_name(req.name)
    async with _presets_lock:
        presets = await asyncio.to_thread(load_presets)
        target = next((p for p in presets if p["id"] == preset_id), None)
        if not target:
            raise HTTPException(404, "프리셋을 찾을 수 없습니다.")
        if any(p["id"] != preset_id and p["name"].strip() == name for p in presets):
            raise HTTPException(409, f"'{name}' 이름의 프리셋이 이미 있습니다.")
        old_name = target["name"]
        target["name"] = name
        await asyncio.to_thread(save_presets, presets)

    # 활성화와 같은 락 안에서 갱신한다 — 동시에 진행 중인 활성화가 옛 이름으로 덮어쓰지 못하게
    async with _activate_lock:
        if current_preset_id == preset_id:
            current_status_text = name
    logger.info(f"✏️  프리셋 이름 변경: '{old_name}' → '{name}'")
    return target


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    """프리셋과 연결된 이미지 파일을 함께 삭제한다. 패널에 떠 있는 화면은 그대로 둔다."""
    global current_preset_id

    async with _presets_lock:
        presets = await asyncio.to_thread(load_presets)
        target = next((p for p in presets if p["id"] == preset_id), None)
        if not target:
            raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

        presets = [p for p in presets if p["id"] != preset_id]
        await asyncio.to_thread(save_presets, presets)

    # ID 는 load_presets() 가 형식(PRESET_ID_RE)을 검증한 값이다 — URL 입력값을 경로에 쓰지 않는다
    img_path = IMAGES_DIR / f"{target['id']}.png"
    if img_path.exists():
        img_path.unlink()

    # 활성화와 같은 락 안에서 확인한다 — 동시에 진행 중인 활성화는 락 안에서 프리셋을 다시 조회하므로
    # "삭제된 프리셋이 활성 상태로 남는" 경우가 생기지 않는다
    async with _activate_lock:
        was_active = current_preset_id == preset_id
        if was_active:
            current_preset_id = None    # 재시작 시 복원 대상에서 제외
        # 복귀 예약이 지운 프리셋을 가리키면 지킬 수 없으므로 함께 지운다
        revert_dropped = pending_revert is not None and preset_id in (
            pending_revert["revert_to"], pending_revert["expected"])
        if revert_dropped:
            _clear_pending_revert()
    if was_active or revert_dropped:
        await persist_state()

    logger.info(f"🗑️  프리셋 삭제: '{target['name']}'")
    return {"success": True, "message": f"'{target['name']}' 프리셋이 삭제되었습니다."}


async def _send_current_frame(force: bool) -> int:
    """현재 프레임을 모든 디바이스에 보낸다. _activate_lock 을 잡은 상태에서만 호출한다."""
    global _last_push_at

    if force:
        await manager.broadcast_text(FORCE_REFRESH_MESSAGE)
    notified = await manager.broadcast_bytes(current_frame_bytes)
    _last_push_at = time.monotonic()
    return notified


async def _push_later(delay: float) -> None:
    """전송 간격이 지나기를 기다렸다가, 그 시점의 최신 프레임을 한 번 보낸다."""
    global _deferred_force

    await asyncio.sleep(delay)
    async with _activate_lock:
        force, _deferred_force = _deferred_force, False
        if current_frame_bytes is not None:
            notified = await _send_current_frame(force)
            logger.info(f"📤 예약된 프레임 전송: '{current_status_text}' → {notified}개 디바이스")


async def push_current_frame(force: bool) -> tuple[int, bool]:
    """
    현재 프레임을 디바이스로 내보낸다. _activate_lock 을 잡은 상태에서만 호출한다.

    직전 전송 후 MIN_PUSH_INTERVAL 이 지나지 않았으면 바로 보내지 않고 예약한다.
    예약은 하나만 유지되고 실행 시점의 최신 프레임을 보내므로, 연타해도 패널은
    간격당 한 번만 갱신되고 결국 마지막 상태가 표시된다 (E-ink 수명 보호).

    @return  (전송에 성공한 디바이스 수, 예약됐는지)
    """
    global _deferred_push, _deferred_force

    wait = MIN_PUSH_INTERVAL - (time.monotonic() - _last_push_at)
    pending = _deferred_push is not None and not _deferred_push.done()
    # 예약된 전송이 막 깨어나 락을 기다리는 중일 수도 있다 — 그때 여기서 또 보내면 간격 안에 두 번 나간다
    if wait > 0 or pending:
        _deferred_force = _deferred_force or force
        if not pending:
            _deferred_push = asyncio.create_task(_push_later(wait))
        return 0, True

    return await _send_current_frame(force), False


async def _activate_core(
    preset: dict, force: bool, expected_current: Optional[str] = None
) -> tuple[Optional[dict], Optional[str]]:
    """
    activate() 의 본체. (결과, 교체 직전의 current_preset_id) 를 돌려준다.

    expected_current 가 주어졌는데 지금 표시 중인 프리셋이 그것이 아니면 아무것도 바꾸지 않고
    (None, 현재 id) 를 돌려준다 — 복귀 조건 검사와 "직전 프리셋" 기록을 프레임 교체와 같은
    _activate_lock 안에서 해, 동시에 들어온 활성화와 엇갈리지 않게 한다.
    """
    global current_status_text, current_preset_id, current_frame_bytes, current_display_image, pending_revert

    try:
        img = await asyncio.to_thread(load_preset_image, preset["id"])
    except FileNotFoundError:
        raise HTTPException(404, "프리셋의 이미지 파일을 찾을 수 없습니다.")

    frame_bytes = await asyncio.to_thread(image_to_1bit_bytes, img)

    async with _activate_lock:
        if expected_current is not None and current_preset_id != expected_current:
            return None, current_preset_id

        # 이미지를 읽는 사이에 이름이 바뀌었거나 삭제됐을 수 있으므로 락 안에서 다시 조회한다
        preset = await asyncio.to_thread(get_preset, preset["id"])
        if preset is None:
            raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

        previous = current_preset_id
        unchanged = frame_bytes == current_frame_bytes and not force
        push_pending = _deferred_push is not None and not _deferred_push.done()

        current_status_text = preset["name"]
        current_preset_id = preset["id"]
        current_display_image = img
        current_frame_bytes = frame_bytes

        # 제안으로 바꾼 상태에서 벗어나면(직접 다른 프리셋을 고름) 복귀 예약은 지킬 이유가 없다
        if pending_revert is not None and current_preset_id != pending_revert["expected"]:
            _clear_pending_revert()

        if unchanged:
            # 같은 프레임이 아직 전송 대기 중이면 "이미 표시 중"이 아니라 "예약됨"으로 알린다
            notified, deferred, skipped = 0, push_pending, not push_pending
        else:
            skipped = False
            notified, deferred = await push_current_frame(force)

    await persist_state()

    if skipped:
        logger.info(f"⏭️  프리셋 '{preset['name']}' — 화면이 이미 같아 전송 생략")
        message = f"'{preset['name']}' 이미 표시 중 (전송 생략)"
    elif deferred:
        logger.info(f"⏳ 프리셋 '{preset['name']}' — 전송 간격 제한으로 예약")
        message = f"'{preset['name']}' 활성화 — 잠시 후 화면에 반영됩니다"
    else:
        logger.info(f"✅ 프리셋 활성화: '{preset['name']}' → {notified}개 디바이스")
        message = f"'{preset['name']}' 활성화 완료"

    return {
        "success": True,
        "message": message,
        "clients_notified": notified,
        "skipped": skipped,
        "deferred": deferred,
    }, previous


async def activate(preset: dict, force: bool) -> dict:
    """
    프리셋을 현재 화면으로 만들고 디바이스에 푸시한다.

    프레임이 지금 표시 중인 것과 바이트 단위로 같으면 전송을 생략한다
    (E-ink 는 갱신 횟수가 수명이므로 불필요한 리프레시를 피한다).
    force=True 면 force_refresh 메시지를 먼저 보내 펌웨어 쪽 CRC 검사도 건너뛰게 한다.
    실제 전송은 push_current_frame() 의 간격 제한을 따른다.
    """
    result, _ = await _activate_core(preset, force)
    return result


# ──────────────────────────────────────────────
#  일정 제안 — 복귀 예약
# ──────────────────────────────────────────────

def _schedule_revert() -> None:
    """pending_revert 의 시각에 맞춰 복귀 타이머를 다시 건다. 예약이 없으면 타이머만 끈다."""
    global _revert_task
    if _revert_task is not None and not _revert_task.done() and _revert_task is not asyncio.current_task():
        _revert_task.cancel()
    _revert_task = asyncio.create_task(_revert_later(pending_revert)) if pending_revert is not None else None


def _clear_pending_revert() -> None:
    global pending_revert
    pending_revert = None
    _schedule_revert()


async def _revert_later(entry: dict) -> None:
    delay = (entry["at"] - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await _run_revert(entry)
    except Exception as e:      # 타이머 작업의 예외는 아무도 받지 않으므로 여기서 남긴다
        logger.error(f"❌ 일정 종료 복귀 실패: {e!r}")


async def _run_revert(entry: dict) -> None:
    """일정 종료 시각. 그때도 제안한 프리셋이 표시 중일 때만 직전 프리셋으로 되돌린다."""
    global pending_revert, _revert_task
    if pending_revert is not entry:          # 그 사이 취소·교체됐다
        return
    pending_revert = None
    _revert_task = None

    preset = await asyncio.to_thread(get_preset, entry["revert_to"])
    if preset is None:
        logger.info("↩️  일정 종료 — 되돌릴 프리셋이 삭제돼 그대로 둡니다")
    else:
        result, _ = await _activate_core(preset, force=False, expected_current=entry["expected"])
        if result is None:
            logger.info("↩️  일정 종료 — 그 사이 다른 프리셋으로 바뀌어 그대로 둡니다")
        else:
            logger.info(f"↩️  일정 종료 — '{preset['name']}' 으로 복귀")
    await persist_state()


def _revert_info() -> Optional[dict]:
    """Admin·제안 화면에 보여줄 복귀 예약. 없으면 None."""
    entry = pending_revert
    if entry is None:
        return None
    preset = get_preset(entry["revert_to"])
    return {
        "at": entry["at"].isoformat(timespec="seconds"),
        "revert_to_id": entry["revert_to"],
        "revert_to_name": preset["name"] if preset else None,
    }


@app.post("/api/presets/{preset_id}/activate")
async def activate_preset(preset_id: str, force: bool = False):
    """프리셋을 활성화하여 연결된 모든 디스플레이에 푸시한다."""
    preset = await asyncio.to_thread(get_preset, preset_id)
    if not preset:
        raise HTTPException(404, "프리셋을 찾을 수 없습니다.")
    return await activate(preset, force)


class SuggestionApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str = Field(pattern=r"^[0-9a-f]{8}$")
    until: Optional[str] = Field(default=None, max_length=MAX_UNTIL_LENGTH)


@app.post("/api/suggestions/apply")
async def apply_suggestion(req: SuggestionApplyRequest):
    """
    제안 화면의 [바꾸기]. 프리셋을 적용하고, 일정 종료 시각(until)에 바꾸기 직전 프리셋으로
    돌아가도록 예약한다. 연속 일정이면 처음 상태를 복귀 대상으로 유지하고 시각만 바꾼다.
    until 이 없거나 형식이 틀리거나 24시간 넘게 남았으면 적용만 한다. 이미 지났으면 409.
    """
    global pending_revert

    preset = await asyncio.to_thread(get_preset, req.preset_id)
    if not preset:
        raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

    now = datetime.now(timezone.utc)
    until = parse_until(req.until) if req.until else None
    if until is not None and until <= now:
        raise HTTPException(409, "이미 끝난 일정입니다.")

    old = pending_revert                     # 적용하면서 지워질 수 있으므로 먼저 떠 둔다
    result, previous = await _activate_core(preset, force=False)
    baseline = old["revert_to"] if old is not None and previous == old["expected"] else previous

    if (until is not None and until <= now + timedelta(seconds=MAX_REVERT_AHEAD)
            and baseline is not None and baseline != preset["id"]):
        pending_revert = {"revert_to": baseline, "expected": preset["id"], "at": until}
        _schedule_revert()
        await persist_state()
        logger.info(f"📅 제안 적용: '{preset['name']}' — {until.isoformat(timespec='minutes')} 에 복귀 예약")
    else:
        logger.info(f"📅 제안 적용: '{preset['name']}' (복귀 예약 없음)")

    return {**result, "revert": await asyncio.to_thread(_revert_info)}


@app.delete("/api/suggestions/revert")
async def cancel_revert():
    """일정 종료 복귀 예약을 취소한다 (Admin 의 [취소])."""
    cancelled = pending_revert is not None
    if cancelled:
        _clear_pending_revert()
        await persist_state()
        logger.info("📅 복귀 예약 취소")
    return {"cancelled": cancelled}


@app.get("/api/presets/{preset_id}/preview.png")
async def preset_preview(preset_id: str):
    """
    프리셋이 활성화되었을 때 E-ink에 표시될 화면의 미리보기를 PNG로 반환한다.
    프리셋 이미지는 생성 후 바뀌지 않으므로 하루 동안 브라우저 캐시를 허용한다.
    """
    preset = await asyncio.to_thread(get_preset, preset_id)
    if not preset:
        raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

    try:
        img = await asyncio.to_thread(load_preset_image, preset_id)
    except FileNotFoundError:
        raise HTTPException(404, "프리셋의 이미지 파일을 찾을 수 없습니다.")

    png_bytes = await asyncio.to_thread(render_preview_png, img)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


# ──────────────────────────────────────────────
#  Apple 단축어 API  (세션 또는 X-API-Key)
# ──────────────────────────────────────────────

@app.get("/api/shortcuts/names", response_class=PlainTextResponse)
async def list_shortcut_names():
    """
    프리셋 이름 목록을 줄바꿈으로 구분된 일반 텍스트로 반환한다.
    단축어 앱의 JSON 파싱이 이모지 포함 문자열에서 불안정하므로 텍스트를 쓴다.
    """
    presets = await asyncio.to_thread(load_presets)
    return "\n".join(p["name"].strip() for p in presets)


class ShortcutActivateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_PRESET_NAME_LENGTH)
    force: bool = False


@app.post("/api/shortcuts/activate")
async def activate_shortcut_by_name(req: ShortcutActivateRequest):
    """
    이름으로 프리셋을 찾아 활성화한다.
    URL 인코딩 문제를 피하기 위해 JSON Body를 사용한다.
    """
    name = req.name.strip()

    presets = await asyncio.to_thread(load_presets)
    preset = next((p for p in presets if p["name"].strip() == name), None)
    if not preset:
        logger.warning(f"❌ 단축어 요청 실패: {name!r} 프리셋 없음")
        raise HTTPException(404, f"'{name}' 프리셋을 찾을 수 없습니다.")

    return await activate(preset, req.force)


# ──────────────────────────────────────────────
#  직접 실행 시 Uvicorn으로 서버 기동
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    logger.info(f"Starting uvicorn on {SERVER_HOST}:{SERVER_PORT} (reload={SERVER_RELOAD})")
    uvicorn.run(
        "main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="info",
        reload=SERVER_RELOAD,
        ws_max_size=64 * 1024,          # ESP32는 서버로 큰 메시지를 보낼 일이 없다 — 플러딩 방지
        proxy_headers=True,             # Caddy 가 넘겨주는 X-Forwarded-* 로 실제 클라이언트 IP·스킴 복원
        forwarded_allow_ips="127.0.0.1",
    )

"""
============================================================
 E-ink Status Board — FastAPI 백엔드 서버 v2.1
============================================================

 ESP32-S3 CrowPanel 3.7인치 E-paper 디스플레이로
 상태 이미지를 실시간 푸시(Push)하는 WebSocket 서버.

 실행 방법:
   python main.py                     # SERVER_PORT / SERVER_RELOAD 환경변수 참조
   uvicorn main:app --host 0.0.0.0 --port 5000

 API:
   GET    /                                  — /admin 으로 리다이렉트
   GET    /admin                             — 관리자 페이지
   GET    /status                            — 현재 상태·연결 수 조회
   GET    /current-preview.png               — 현재 표시 중인 화면 미리보기
   WS     /ws                                — ESP32 WebSocket 연결
   GET    /api/presets                       — 프리셋 목록
   POST   /api/presets                       — 프리셋 생성 (multipart: name, image)
   DELETE /api/presets/{id}                  — 프리셋 삭제
   POST   /api/presets/{id}/activate         — 프리셋 활성화 (디스플레이 푸시)
   GET    /api/presets/{id}/preview.png      — 프리셋 미리보기 (1-bit 디더링 결과)
   GET    /api/shortcuts/names               — Apple 단축어용 이름 목록 (Plain Text)
   POST   /api/shortcuts/activate            — Apple 단축어용 이름 기반 활성화 (JSON)

 기술 스택:
   FastAPI, Uvicorn, WebSockets, Pillow (PIL)
============================================================
"""

import asyncio
import json
import logging
import os
import uuid as uuid_lib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    UploadFile, File, Form, HTTPException,
)
from fastapi.responses import (
    JSONResponse, FileResponse, PlainTextResponse, Response, RedirectResponse,
)
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

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

# ──────────────────────────────────────────────
#  디스플레이 상수
# ──────────────────────────────────────────────

# 전자잉크 패널 해상도 (가로 모드, Landscape)
DISPLAY_WIDTH = 416
DISPLAY_HEIGHT = 240

# 1-bit 이미지의 바이트 크기: 416 × 240 ÷ 8 = 12,480 바이트
FRAME_BUFFER_SIZE = (DISPLAY_WIDTH * DISPLAY_HEIGHT) // 8

# ──────────────────────────────────────────────
#  업로드 제한
# ──────────────────────────────────────────────

# 업로드 파일 최대 크기 (5MB)
#  - Content-Length 가 이보다 크면 미들웨어가 본문을 받기 전에 413 으로 끊는다
#  - 그 뒤 엔드포인트는 파일을 청크로 읽어 메모리 사용량을 이 값으로 묶는다
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
# multipart 경계·필드 오버헤드 허용치
UPLOAD_OVERHEAD = 64 * 1024

# 디코딩 허용 최대 픽셀 수 (약 24MP). 작은 파일이 거대한 해상도로 부풀어
# 메모리를 고갈시키는 "decompression bomb"을 헤더 단계에서 차단한다.
MAX_IMAGE_PIXELS = 24_000_000

# 프리셋 이름 길이 제한
MAX_PRESET_NAME_LENGTH = 100

# 프리셋 최대 개수 (디스크 채우기 방지)
MAX_PRESETS = 200

# WebSocket 전송 타임아웃(초) — 읽지 않는 클라이언트 하나가 전체 푸시를 막지 못하게 한다
WS_SEND_TIMEOUT = 5.0

# ──────────────────────────────────────────────
#  서버 실행 설정 (환경변수)
# ──────────────────────────────────────────────

SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")   # 리버스 프록시 뒤에서는 127.0.0.1
SERVER_PORT = int(os.environ.get("SERVER_PORT", 5000))
# 코드 변경 시 자동 재시작 — 개발용. 운영(systemd)에서는 반드시 false 유지.
SERVER_RELOAD = os.environ.get("SERVER_RELOAD", "false").lower() in ("1", "true", "yes")

# ──────────────────────────────────────────────
#  파일 경로 설정
# ──────────────────────────────────────────────

BASE_DIR = Path(__file__).parent                # server/
DATA_DIR = BASE_DIR / "data"                    # server/data/
IMAGES_DIR = DATA_DIR / "images"                # server/data/images/
PRESETS_FILE = DATA_DIR / "presets.json"        # server/data/presets.json
STATIC_DIR = BASE_DIR / "static"                # server/static/

# ──────────────────────────────────────────────
#  전역 상태
# ──────────────────────────────────────────────
#  단일 asyncio 이벤트 루프에서만 갱신된다. 아래 세 값의 교체와 브로드캐스트는
#  activate_preset()이 _activate_lock 안에서 한 단위로 수행한다.

# 현재 표시 중인 프리셋 이름
current_status_text: str = ""

# 현재 렌더링된 프레임 바이너리 (새 클라이언트 접속 시 즉시 전송용)
current_frame_bytes: Optional[bytes] = None

# 현재 렌더링된 디스플레이 이미지 (미리보기 생성용)
current_display_image: Optional[Image.Image] = None

# presets.json 읽기-수정-쓰기 직렬화용 락
# (동시에 생성/삭제 요청이 들어오면 한쪽 변경이 유실되는 것을 방지)
_presets_lock = asyncio.Lock()

# 활성화 직렬화용 락 — 전역 상태 교체와 브로드캐스트를 한 단위로 묶어
# 동시 활성화 시 "미리보기는 A, 실제 패널은 B"가 되는 것을 방지
_activate_lock = asyncio.Lock()

# ──────────────────────────────────────────────
#  데이터 디렉토리 초기화
# ──────────────────────────────────────────────

def ensure_directories():
    """서버 구동에 필요한 디렉토리와 기본 파일을 생성한다."""
    DATA_DIR.mkdir(exist_ok=True)
    IMAGES_DIR.mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)
    if not PRESETS_FILE.exists():
        PRESETS_FILE.write_text("[]", encoding="utf-8")


# ──────────────────────────────────────────────
#  프리셋 데이터 영속화 (JSON 파일 기반)
# ──────────────────────────────────────────────

def load_presets() -> list[dict]:
    """presets.json에서 프리셋 목록을 읽어온다. 파일이 없거나 깨졌으면 빈 목록."""
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_presets(presets: list[dict]):
    """
    프리셋 목록을 presets.json에 저장한다.
    임시 파일에 쓴 뒤 os.replace로 교체하므로, 쓰는 도중 프로세스가 죽어도
    기존 파일이 반쪽짜리로 남지 않는다.
    """
    tmp_path = PRESETS_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(presets, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(tmp_path, PRESETS_FILE)


def get_preset(preset_id: str) -> Optional[dict]:
    """ID로 프리셋을 조회한다. 없으면 None 반환."""
    return next((p for p in load_presets() if p["id"] == preset_id), None)


def generate_preset_id(existing_ids: set[str]) -> str:
    """8자리 hex ID를 생성한다. 기존 ID와 겹치면 다시 뽑는다."""
    while True:
        candidate = uuid_lib.uuid4().hex[:8]
        if candidate not in existing_ids:
            return candidate


# ──────────────────────────────────────────────
#  WebSocket 클라이언트 관리자
# ──────────────────────────────────────────────

class ConnectionManager:
    """
    연결된 WebSocket 클라이언트(ESP32 등)의 목록을 관리한다.
    클라이언트 추가, 제거, 전체 브로드캐스트 기능을 제공한다.
    """

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """새 클라이언트의 WebSocket 연결을 수락하고 목록에 추가한다."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(
            f"✅ 새 클라이언트 연결: {websocket.client}  "
            f"(현재 연결 수: {len(self.active_connections)})"
        )

    async def disconnect(self, websocket: WebSocket):
        """클라이언트를 목록에서 제거한다. 이미 제거된 경우 아무 일도 하지 않는다."""
        async with self._lock:
            if websocket not in self.active_connections:
                return
            self.active_connections.remove(websocket)
        logger.info(
            f"⚠️  클라이언트 연결 해제: {websocket.client}  "
            f"(현재 연결 수: {len(self.active_connections)})"
        )

    async def broadcast_bytes(self, data: bytes) -> int:
        """
        연결된 모든 클라이언트에게 바이너리 데이터를 동시에 전송한다.
        전송 실패한 클라이언트는 목록에서 자동 제거한다.

        @return  전송에 성공한 클라이언트 수
        """
        async with self._lock:
            connections = list(self.active_connections)

        if not connections:
            return 0

        async def _send(conn: WebSocket):
            await asyncio.wait_for(conn.send_bytes(data), timeout=WS_SEND_TIMEOUT)

        results = await asyncio.gather(
            *(_send(conn) for conn in connections),
            return_exceptions=True,
        )

        disconnected = [
            conn for conn, result in zip(connections, results)
            if isinstance(result, BaseException)
        ]
        for conn, result in zip(connections, results):
            if isinstance(result, BaseException):
                logger.warning(f"❌ 전송 실패 ({conn.client}): {result}")

        if disconnected:
            async with self._lock:
                for conn in disconnected:
                    if conn in self.active_connections:
                        self.active_connections.remove(conn)
            # 타임아웃으로 쫓아낸 소켓은 아직 살아 있을 수 있으므로 명시적으로 닫아
            # 클라이언트(ESP32)가 자신의 재연결 루프를 돌게 한다
            for conn in disconnected:
                try:
                    await conn.close(code=1011)
                except Exception:
                    pass
            logger.info(f"🧹 비정상 클라이언트 {len(disconnected)}개 정리 완료")

        return len(connections) - len(disconnected)

    @property
    def client_count(self) -> int:
        """현재 연결된 클라이언트 수를 반환한다."""
        return len(self.active_connections)


# 전역 연결 관리자 인스턴스
manager = ConnectionManager()

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
#  FastAPI 애플리케이션
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작/종료 시 실행되는 lifespan 이벤트 핸들러"""
    ensure_directories()
    logger.info("========================================")
    logger.info("  E-ink Status Board Server v2.1 시작")
    logger.info("========================================")
    logger.info(f"디스플레이 해상도: {DISPLAY_WIDTH}×{DISPLAY_HEIGHT}")
    logger.info(f"프레임 버퍼 크기: {FRAME_BUFFER_SIZE} bytes")
    logger.info(f"데이터 디렉토리: {DATA_DIR}")
    logger.info(f"Admin 페이지: http://localhost:{SERVER_PORT}/admin")
    logger.info(f"📌 등록된 프리셋: {len(load_presets())}개")
    logger.info("========================================")
    yield


app = FastAPI(
    title="E-ink Status Board Server",
    description="ESP32 전자잉크 디스플레이를 위한 실시간 상태 푸시 서버 (Admin + Presets)",
    version="2.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def reject_oversized_uploads(request, call_next):
    """
    프리셋 업로드의 Content-Length 를 본문 수신 전에 검사한다.
    Starlette 의 multipart 파서는 엔드포인트가 실행되기 전에 본문 전체를
    임시 파일로 받아버리므로, 여기서 끊지 않으면 거대한 POST 가 디스크를 채운다.
    """
    if request.method == "POST" and request.url.path == "/api/presets":
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_UPLOAD_SIZE + UPLOAD_OVERHEAD:
            return JSONResponse(
                status_code=413,
                content={"detail": f"파일 크기는 {MAX_UPLOAD_SIZE // (1024 * 1024)}MB 이하여야 합니다."},
            )
    return await call_next(request)


# ──────────────────────────────────────────────
#  페이지 라우트
# ──────────────────────────────────────────────

@app.get("/")
async def root():
    """루트 경로 접속 시 관리자 페이지로 리다이렉트"""
    return RedirectResponse(url="/admin")


@app.get("/admin")
async def admin_page():
    """관리자 페이지(admin.html)를 서빙한다."""
    html_path = STATIC_DIR / "admin.html"
    if not html_path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": "admin.html 파일을 찾을 수 없습니다."},
        )
    return FileResponse(html_path, media_type="text/html")


# ──────────────────────────────────────────────
#  상태 API
# ──────────────────────────────────────────────

@app.get("/status")
async def get_status():
    """현재 표시 중인 프리셋 이름과 연결 정보를 조회한다."""
    return {
        "text": current_status_text,
        "connected_clients": manager.client_count,
        "frame_ready": current_frame_bytes is not None,
        "frame_size_bytes": len(current_frame_bytes) if current_frame_bytes else 0,
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


# ──────────────────────────────────────────────
#  WebSocket 엔드포인트
# ──────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    ESP32가 연결을 맺고 유지하는 WebSocket 엔드포인트.

    연결 흐름:
      1. 연결 수락 및 관리 목록에 추가
      2. 이미 렌더링된 프레임이 있으면 즉시 전송 (최신 상태 동기화)
      3. 연결 유지 — 클라이언트 메시지를 수신만 하며 로그로 남긴다
         (텍스트/바이너리 어느 쪽이 와도 연결을 끊지 않는다)
      4. 연결 종료 시 목록에서 제거

    프레임 푸시는 활성화 API가 manager.broadcast_bytes()로 수행한다.
    """
    await manager.connect(websocket)

    try:
        if current_frame_bytes is not None:
            logger.info(f"📤 기존 프레임을 새 클라이언트에게 전송: {websocket.client}")
            await websocket.send_bytes(current_frame_bytes)

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") is not None:
                logger.info(f"📨 텍스트 수신 ({websocket.client}): {message['text'][:200]}")
            elif message.get("bytes") is not None:
                logger.info(f"📨 바이너리 수신 ({websocket.client}): {len(message['bytes'])} bytes")

        logger.info(f"👋 클라이언트 정상 종료: {websocket.client}")

    except WebSocketDisconnect:
        logger.info(f"👋 클라이언트 정상 종료: {websocket.client}")

    except Exception as e:
        logger.error(f"❌ 클라이언트 비정상 종료 ({websocket.client}): {e}")

    finally:
        await manager.disconnect(websocket)


# ──────────────────────────────────────────────
#  프리셋 API
# ──────────────────────────────────────────────

@app.get("/api/presets")
async def list_presets():
    """등록된 모든 프리셋을 목록으로 반환한다."""
    return await asyncio.to_thread(load_presets)


@app.post("/api/presets", status_code=201)
async def create_preset(
    name: str = Form(..., min_length=1, max_length=MAX_PRESET_NAME_LENGTH,
                     description="프리셋 이름 (단축어에서 이 이름으로 호출)"),
    image: UploadFile = File(..., description="프리셋 이미지 파일"),
):
    """
    새 프리셋을 생성한다. 업로드된 이미지는 416×240 크기로 변환되어 저장된다.
    이름은 단축어 API의 키로 쓰이므로 중복을 허용하지 않는다.
    """
    name = name.strip()
    if not name:
        raise HTTPException(422, "프리셋 이름을 입력하세요.")
    # 줄바꿈·제어문자는 단축어용 줄바꿈 목록과 로그를 깨뜨리므로 거부
    if any(ord(ch) < 32 or ch == "\x7f" for ch in name):
        raise HTTPException(422, "프리셋 이름에 줄바꿈이나 제어 문자를 쓸 수 없습니다.")

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
        logger.info(f"🔄 이미지 리사이즈: {img.size} → ({DISPLAY_WIDTH}, {DISPLAY_HEIGHT})")
        img = await asyncio.to_thread(resize_with_letterbox, img)

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


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    """프리셋과 연결된 이미지 파일을 함께 삭제한다."""
    async with _presets_lock:
        presets = await asyncio.to_thread(load_presets)
        target = next((p for p in presets if p["id"] == preset_id), None)
        if not target:
            raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

        presets = [p for p in presets if p["id"] != preset_id]
        await asyncio.to_thread(save_presets, presets)

    # 파일명은 JSON에 저장된 값만 사용 (URL 입력값을 경로에 쓰지 않음)
    if target.get("image_filename"):
        img_path = IMAGES_DIR / target["image_filename"]
        if img_path.exists():
            img_path.unlink()

    logger.info(f"🗑️  프리셋 삭제: '{target['name']}'")
    return {"success": True, "message": f"'{target['name']}' 프리셋이 삭제되었습니다."}


@app.post("/api/presets/{preset_id}/activate")
async def activate_preset(preset_id: str):
    """
    프리셋을 활성화하여 연결된 모든 디스플레이에 푸시한다.
    단축어 API도 내부적으로 이 함수를 호출한다.
    """
    global current_status_text, current_frame_bytes, current_display_image

    preset = await asyncio.to_thread(get_preset, preset_id)
    if not preset:
        raise HTTPException(404, "프리셋을 찾을 수 없습니다.")

    try:
        img = await asyncio.to_thread(load_preset_image, preset_id)
    except FileNotFoundError:
        raise HTTPException(404, "프리셋의 이미지 파일을 찾을 수 없습니다.")

    frame_bytes = await asyncio.to_thread(image_to_1bit_bytes, img)

    async with _activate_lock:
        current_status_text = preset["name"]
        current_display_image = img
        current_frame_bytes = frame_bytes
        notified = await manager.broadcast_bytes(frame_bytes)
    logger.info(f"✅ 프리셋 활성화: '{preset['name']}' → {notified}개 디바이스")
    return {
        "success": True,
        "message": f"'{preset['name']}' 활성화 완료",
        "clients_notified": notified,
    }


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
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ──────────────────────────────────────────────
#  Apple 단축어 API
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
    name: str


@app.post("/api/shortcuts/activate")
async def activate_shortcut_by_name(req: ShortcutActivateRequest):
    """
    이름으로 프리셋을 찾아 활성화한다.
    URL 인코딩 문제를 피하기 위해 JSON Body를 사용한다.
    """
    name = req.name.strip()
    logger.info(f"📱 단축어 활성화 요청: '{name}'")

    presets = await asyncio.to_thread(load_presets)
    preset = next((p for p in presets if p["name"].strip() == name), None)
    if not preset:
        logger.warning(f"❌ 단축어 요청 실패: '{name}' 프리셋 없음")
        raise HTTPException(404, f"'{name}' 프리셋을 찾을 수 없습니다.")

    return await activate_preset(preset["id"])


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
        ws_max_size=64 * 1024,   # ESP32는 서버로 큰 메시지를 보낼 일이 없다 — 플러딩 방지
    )

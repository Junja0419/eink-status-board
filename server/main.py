"""
============================================================
 E-ink Status Board — FastAPI 백엔드 서버 v2.0
============================================================

 ESP32-S3 CrowPanel 3.7인치 E-paper 디스플레이로
 상태 텍스트/이미지를 실시간 푸시(Push)하는 WebSocket 서버.

 실행 방법:
   uvicorn main:app --host 0.0.0.0 --port 5000

 API:
   GET  /admin                              — 관리자 페이지
   POST /status                             — 텍스트 상태 업데이트 및 푸시
   GET  /status                             — 현재 상태 조회
   WS   /ws                                 — ESP32 WebSocket 연결
   GET  /api/presets                      — 상태 프리셋 목록 조회
   POST /api/presets                      — 상태 프리셋 생성 (텍스트/이미지)
   DELETE /api/presets/{id}               — 상태 프리셋 삭제
   POST /api/presets/{id}/activate        — 상태 프리셋 활성화 (디스플레이 푸시)
   GET  /api/presets/{id}/preview.png     — 상태 프리셋 미리보기 이미지
   GET  /api/presets/{id}/export-preset — Apple 단축어 파일 다운로드

 기술 스택:
   FastAPI, Uvicorn, WebSockets, Pillow (PIL)
============================================================
"""

import asyncio
import json
import logging

import uuid as uuid_lib
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect,
    UploadFile, File, Form, HTTPException,
)
from fastapi.responses import JSONResponse, FileResponse, Response, RedirectResponse
from pydantic import BaseModel
from PIL import Image
import os
from dotenv import load_dotenv

# .env 파일 로드
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

# 사용할 한글 폰트 경로 (시스템 폰트 또는 로컬 .ttf 파일)
# .env 파일에서 FONT_PATH를 가져오며, 기본값으로 macOS 시스템 폰트를 사용
FONT_PATH = os.environ.get("FONT_PATH", "/System/Library/Fonts/AppleSDGothicNeo.ttc")

# 기본 폰트 크기
FONT_SIZE = int(os.environ.get("FONT_SIZE", 36))

# ──────────────────────────────────────────────
#  파일 경로 설정
# ──────────────────────────────────────────────

BASE_DIR = Path(__file__).parent                # server/
DATA_DIR = BASE_DIR / "data"                    # server/data/
IMAGES_DIR = DATA_DIR / "images"                # server/data/images/
PRESETS_FILE = DATA_DIR / "presets.json"    # server/data/presets.json
STATIC_DIR = BASE_DIR / "static"                # server/static/

# ──────────────────────────────────────────────
#  전역 상태
# ──────────────────────────────────────────────

# 현재 표시 중인 상태 텍스트
current_status_text: str = ""

# 현재 렌더링된 프레임 바이너리 (새 클라이언트 접속 시 즉시 전송용)
current_frame_bytes: Optional[bytes] = None

# 현재 렌더링된 디스플레이 이미지 (미리보기 생성용)
current_display_image: Optional[Image.Image] = None

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
#  상태 프리셋 데이터 영속화 (JSON 파일 기반)
# ──────────────────────────────────────────────

def load_presets() -> list[dict]:
    """presets.json에서 상태 프리셋 목록을 읽어온다."""
    try:
        return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_presets(presets: list[dict]):
    """상태 프리셋 목록을 presets.json에 저장한다."""
    PRESETS_FILE.write_text(
        json.dumps(presets, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def get_preset(preset_id: str) -> Optional[dict]:
    """ID로 상태 프리셋를 조회한다. 없으면 None 반환."""
    return next((s for s in load_presets() if s["id"] == preset_id), None)


# ──────────────────────────────────────────────
#  WebSocket 클라이언트 관리자
# ──────────────────────────────────────────────

class ConnectionManager:
    """
    연결된 WebSocket 클라이언트(ESP32 등)의 목록을 관리한다.
    클라이언트 추가, 제거, 전체 브로드캐스트 기능을 제공한다.
    """

    def __init__(self):
        # 현재 연결된 WebSocket 클라이언트 목록
        self.active_connections: list[WebSocket] = []
        # 동시 접근 방지를 위한 비동기 락
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
        """클라이언트를 목록에서 제거한다."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(
            f"⚠️  클라이언트 연결 해제: {websocket.client}  "
            f"(현재 연결 수: {len(self.active_connections)})"
        )

    async def broadcast_bytes(self, data: bytes):
        """
        연결된 모든 클라이언트에게 바이너리 데이터를 전송한다.
        전송 실패한 클라이언트는 목록에서 자동 제거한다.
        """
        # 전송 중 목록 변경을 방지하기 위해 스냅샷 생성
        async with self._lock:
            connections = list(self.active_connections)

        # 비정상 종료된 클라이언트를 추적
        disconnected: list[WebSocket] = []

        for connection in connections:
            try:
                await connection.send_bytes(data)
            except Exception as e:
                logger.warning(f"❌ 전송 실패 ({connection.client}): {e}")
                disconnected.append(connection)

        # 전송 실패한 클라이언트 정리
        if disconnected:
            async with self._lock:
                for conn in disconnected:
                    if conn in self.active_connections:
                        self.active_connections.remove(conn)
            logger.info(f"🧹 비정상 클라이언트 {len(disconnected)}개 정리 완료")

    @property
    def client_count(self) -> int:
        """현재 연결된 클라이언트 수를 반환한다."""
        return len(self.active_connections)


# 전역 연결 관리자 인스턴스
manager = ConnectionManager()

# ──────────────────────────────────────────────
#  이미지 렌더링 함수
# ──────────────────────────────────────────────





def load_preset_image(preset_id: str) -> Image.Image:
    """
    상태 프리셋 ID에 해당하는 업로드된 이미지를 로드한다.
    이미지 크기가 디스플레이와 다르면 레터박스로 리사이즈한다.

    @param preset_id  상태 프리셋 고유 ID
    @return             416×240 크기의 PIL Image 객체
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

    @param img  원본 이미지
    @return     416×240 크기로 리사이즈된 이미지
    """
    target_w, target_h = DISPLAY_WIDTH, DISPLAY_HEIGHT
    ratio = min(target_w / img.width, target_h / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)

    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    result = Image.new("RGB", (target_w, target_h), (255, 255, 255))

    offset_x = (target_w - new_w) // 2
    offset_y = (target_h - new_h) // 2
    result.paste(resized, (offset_x, offset_y))

    return result


def image_to_1bit_bytes(img: Image.Image) -> bytes:
    """
    RGB 이미지를 1-bit 흑백 이미지로 변환한 뒤,
    ESP32가 바로 읽을 수 있는 순수 바이트 배열로 패킹한다.

    1-bit 이미지에서는 각 픽셀이 1비트로 표현되므로,
    8개 픽셀이 1바이트에 패킹된다.
    총 크기: 416 × 240 ÷ 8 = 12,480 바이트

    Pillow의 '1' 모드 변환 시 디더링이 적용될 수 있으므로,
    먼저 'L' (그레이스케일)로 변환한 뒤 임계값(threshold) 기반으로
    깔끔하게 이진화한다.

    @param img  변환할 PIL Image 객체
    @return     12,480 바이트의 bytes 객체
    """
    # 1단계 & 2단계: 1-bit 변환 (Floyd-Steinberg 디더링 기본 적용)
    # 컬러 이미지나 이모지 등을 업로드했을 때, 단순 임계값(Threshold)으로 자르면
    # 디테일이 날아가지만, 디더링을 적용하면 명암을 점의 밀도로 표현하여 원본의 형태를 보존합니다.
    binary = img.convert("1")

    # 3단계: 하드웨어 해상도(240x416 세로)에 맞게 회전 및 좌우 반전
    # E-paper 컨트롤러 메모리 스캔 방향(하드웨어적 특성)에 맞추기 위해
    # 90도 회전(ROTATE_90) 후 좌우 반전(FLIP_LEFT_RIGHT)을 거치면 완벽하게 방향이 맞춰집니다.
    binary = binary.transpose(Image.Transpose.ROTATE_90)
    binary = binary.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    # 4단계: 1-bit 이미지 데이터를 바이트 배열로 패킹
    # Pillow의 tobytes()는 '1' 모드에서 각 바이트에 8픽셀을 패킹한다.
    # MSB(최상위 비트)가 첫 번째 픽셀에 해당한다.
    raw_bytes = binary.tobytes()

    # 크기 검증
    if len(raw_bytes) != FRAME_BUFFER_SIZE:
        logger.warning(
            f"⚠️  바이트 배열 크기 불일치: 예상 {FRAME_BUFFER_SIZE}, 실제 {len(raw_bytes)}"
        )
        # Pillow 내부 패딩으로 인해 크기가 다를 수 있으므로 수동 패킹 수행
        raw_bytes = _manual_pack_1bit(binary)

    logger.info(f"📦 1-bit 변환 완료: {len(raw_bytes)} 바이트")
    return raw_bytes


def _manual_pack_1bit(img: Image.Image) -> bytes:
    """
    Pillow의 tobytes()가 예상과 다른 크기를 반환할 경우,
    수동으로 픽셀 데이터를 1-bit 바이트 배열로 패킹한다.

    @param img  1-bit 또는 L 모드의 PIL Image 객체
    @return     12,480 바이트의 bytes 객체
    """
    # 픽셀 접근을 위해 '1' 모드로 확실히 변환
    if img.mode != "1":
        img = img.convert("1")

    pixels = img.load()
    byte_array = bytearray(FRAME_BUFFER_SIZE)

    for y in range(img.height):
        for x in range(img.width):
            # 픽셀 인덱스 (좌→우, 상→하 순서)
            pixel_index = y * img.width + x
            byte_index = pixel_index // 8
            bit_index = 7 - (pixel_index % 8)  # MSB first

            # 픽셀 값: 0(검은색) 또는 255/1(흰색)
            # 전자잉크에서는 보통 1 = 흰색, 0 = 검은색
            if pixels[x, y]:
                byte_array[byte_index] |= (1 << bit_index)

    return bytes(byte_array)


def render_preview_png(img: Image.Image) -> bytes:
    """
    이미지를 1-bit 변환 결과 그대로 PNG로 렌더링한다.
    브라우저 미리보기용 — E-ink에 실제 표시될 흑백 결과를 보여준다.

    @param img  변환할 PIL Image 객체
    @return     PNG 형식의 bytes 객체
    """
    # E-ink와 동일하게 디더링된 1-bit 흑백 이미지로 변환하여 미리보기를 제공한다.
    bw = img.convert("1")
    buf = BytesIO()
    bw.save(buf, format="PNG")
    return buf.getvalue()



# ──────────────────────────────────────────────
#  FastAPI 애플리케이션
# ──────────────────────────────────────────────

app = FastAPI(
    title="E-ink Status Board Server",
    description="ESP32 전자잉크 디스플레이를 위한 실시간 상태 푸시 서버 (Admin + Presets)",
    version="2.0.0",
)


# ──────────────────────────────────────────────
#  요청/응답 모델
# ──────────────────────────────────────────────




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
#  상태 API (기존 호환)
# ──────────────────────────────────────────────


@app.get("/status")
async def get_status():
    """현재 상태 텍스트와 연결 정보를 조회한다."""
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
    if current_display_image is not None:
        png_bytes = render_preview_png(current_display_image)
    else:
        blank = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), (255, 255, 255))
        png_bytes = render_preview_png(blank)
    return Response(content=png_bytes, media_type="image/png")


# ──────────────────────────────────────────────
#  WebSocket 엔드포인트
# ──────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    ESP32가 연결을 맺고 유지하는 WebSocket 엔드포인트.

    연결 흐름:
      1. 클라이언트 연결 수락 및 관리 목록에 추가
      2. 이미 렌더링된 프레임이 있으면 즉시 전송 (최신 상태 동기화)
      3. 연결 유지 — 클라이언트로부터의 메시지 대기 (keep-alive)
      4. 연결 종료 시 목록에서 제거

    ESP32 측에서는 이 엔드포인트에 연결을 유지하며,
    서버가 POST /status로 상태 업데이트를 받을 때
    broadcast_bytes()를 통해 바이너리 데이터를 수신한다.
    """
    # 1. 연결 수락 및 등록
    await manager.connect(websocket)

    try:
        # 2. 기존 프레임이 있으면 즉시 전송 (새로 접속한 ESP32에 최신 화면 동기화)
        if current_frame_bytes is not None:
            logger.info(f"📤 기존 프레임을 새 클라이언트에게 전송: {websocket.client}")
            await websocket.send_bytes(current_frame_bytes)

        # 3. 연결 유지 — 클라이언트의 메시지를 계속 대기
        #    ESP32는 주로 수신만 하지만, ping/pong 등의 제어 메시지를 처리하기 위해
        #    receive 루프를 유지한다.
        while True:
            # 클라이언트로부터 메시지 수신 대기
            # WebSocket이 끊어지면 WebSocketDisconnect 예외가 발생한다.
            data = await websocket.receive_text()
            logger.info(f"📨 클라이언트 메시지 수신 ({websocket.client}): {data}")

    except WebSocketDisconnect:
        # 4-a. 정상적인 연결 종료 (클라이언트가 close 프레임 전송)
        await manager.disconnect(websocket)
        logger.info(f"👋 클라이언트 정상 종료: {websocket.client}")

    except Exception as e:
        # 4-b. 비정상적인 연결 종료 (네트워크 오류, 타임아웃 등)
        await manager.disconnect(websocket)
        logger.error(f"❌ 클라이언트 비정상 종료 ({websocket.client}): {e}")


# ──────────────────────────────────────────────
#  상태 프리셋 (Presets) API
# ──────────────────────────────────────────────

@app.get("/api/presets")
async def list_presets():
    """등록된 모든 상태 프리셋를 목록으로 반환한다."""
    return load_presets()


from fastapi.responses import PlainTextResponse

@app.get("/api/shortcuts/names", response_class=PlainTextResponse)
async def list_shortcut_names():
    """
    Apple 단축어 전용: 프리셋 이름 목록을 줄바꿈으로 구분된 일반 텍스트로 반환한다.
    단축어의 예측 불가능한 JSON 파싱 오류를 원천 차단합니다.
    """
    names = [p["name"].strip() for p in load_presets()]
    return "\n".join(names)

class ShortcutActivateRequest(BaseModel):
    name: str

@app.post("/api/shortcuts/activate")
async def activate_shortcut_by_name(req: ShortcutActivateRequest):
    """
    Apple 단축어 전용: 이름으로 프리셋을 찾아 활성화한다.
    URL 인코딩 문제를 피하기 위해 JSON Body를 사용합니다.
    """
    name = req.name.strip() if req.name else ""
    logger.info(f"📱 단축어로부터 활성화 요청 수신: 이름='{name}'")
    
    presets = load_presets()
    preset = next((p for p in presets if p["name"].strip() == name), None)
    
    if not preset:
        available_names = [p["name"] for p in presets]
        logger.error(f"❌ 단축어 요청 실패: '{name}'에 해당하는 프리셋이 없습니다. (현재 등록된 이름들: {available_names})")
        raise HTTPException(status_code=400, detail=f"'{name}' 프리셋을 찾을 수 없습니다.")
        
    return await activate_preset(preset["id"])


@app.post("/api/presets")
async def create_preset(
    name: str = Form(..., description="상태 프리셋 이름"),
    image: UploadFile = File(..., description="상태 프리셋의 이미지 파일"),
):
    """
    새 상태 프리셋을 생성한다. 업로드된 이미지는 416×240 크기로 변환되어 저장된다.
    """
    # 고유 ID 생성 (짧은 UUID — 8자)
    preset_id = str(uuid_lib.uuid4())[:8]

    preset = {
        "id": preset_id,
        "name": name.strip(),
        "image_filename": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # 업로드된 파일 읽기
    content = await image.read()
    try:
        img = Image.open(BytesIO(content)).convert("RGB")
    except Exception:
        raise HTTPException(400, "유효하지 않은 이미지 파일입니다.")

    # 416×240이 아니면 레터박스로 리사이즈
    if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
        logger.info(
            f"🔄 이미지 리사이즈: {img.size} → ({DISPLAY_WIDTH}, {DISPLAY_HEIGHT})"
        )
        img = resize_with_letterbox(img)

    # 이미지 저장
    img.save(IMAGES_DIR / f"{preset_id}.png", "PNG")
    preset["image_filename"] = f"{preset_id}.png"

    # JSON에 저장
    presets = load_presets()
    presets.append(preset)
    save_presets(presets)

    logger.info(f"📌 상태 프리셋 생성: '{preset['name']}'")
    return preset


@app.delete("/api/presets/{preset_id}")
async def delete_preset(preset_id: str):
    """
    상태 프리셋를 삭제한다.
    이미지 상태 프리셋인 경우, 관련 이미지 파일도 함께 삭제한다.
    """
    presets = load_presets()
    target = next((s for s in presets if s["id"] == preset_id), None)

    if not target:
        raise HTTPException(404, "상태 프리셋를 찾을 수 없습니다.")

    # 이미지 파일 삭제
    if target.get("image_filename"):
        img_path = IMAGES_DIR / target["image_filename"]
        if img_path.exists():
            img_path.unlink()
            logger.info(f"🗑️  이미지 파일 삭제: {img_path}")

    # JSON에서 제거
    presets = [s for s in presets if s["id"] != preset_id]
    save_presets(presets)

    logger.info(f"🗑️  상태 프리셋 삭제: '{target['name']}'")
    return {"success": True, "message": f"'{target['name']}' 상태 프리셋가 삭제되었습니다."}


@app.post("/api/presets/{preset_id}/activate")
async def activate_preset(preset_id: str):
    """
    상태 프리셋을 활성화하여 디스플레이에 푸시한다.
    저장된 이미지를 1-bit 변환하여 전송한다.
    Apple 프리셋에서 호출할 때도 이 엔드포인트를 사용한다.
    """
    global current_status_text, current_frame_bytes, current_display_image

    preset = get_preset(preset_id)
    if not preset:
        raise HTTPException(404, "상태 프리셋을 찾을 수 없습니다.")

    try:
        img = load_preset_image(preset_id)
    except FileNotFoundError:
        raise HTTPException(404, "상태 프리셋의 이미지 파일을 찾을 수 없습니다.")
    current_status_text = f"[이미지] {preset['name']}"

    # ── 전역 상태 업데이트 ──
    current_display_image = img
    current_frame_bytes = image_to_1bit_bytes(img)

    # ── 연결된 클라이언트에게 브로드캐스트 ──
    count = manager.client_count
    if count > 0:
        logger.info(f"📡 상태 프리셋 '{preset['name']}' → {count}개 클라이언트에게 전송")
        await manager.broadcast_bytes(current_frame_bytes)

    logger.info(f"✅ 상태 프리셋 활성화: '{preset['name']}'")
    return {
        "success": True,
        "message": f"'{preset['name']}' 활성화 완료",
        "clients_notified": count,
    }


@app.get("/api/presets/{preset_id}/preview.png")
async def preset_preview(preset_id: str):
    """
    상태 프리셋이 활성화되었을 때 E-ink에 표시될 화면의 미리보기를 PNG로 반환한다.
    """
    preset = get_preset(preset_id)
    if not preset:
        raise HTTPException(404, "상태 프리셋을 찾을 수 없습니다.")

    try:
        img = load_preset_image(preset_id)
    except FileNotFoundError:
        raise HTTPException(404, "상태 프리셋의 이미지 파일을 찾을 수 없습니다.")

    png_bytes = render_preview_png(img)
    return Response(content=png_bytes, media_type="image/png")




# ──────────────────────────────────────────────
#  서버 이벤트
# ──────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    """서버 시작 시 초기 설정 및 상태 출력"""
    ensure_directories()
    logger.info("========================================")
    logger.info("  E-ink Status Board Server v2.0 시작")
    logger.info("========================================")
    logger.info(f"디스플레이 해상도: {DISPLAY_WIDTH}×{DISPLAY_HEIGHT}")
    logger.info(f"프레임 버퍼 크기: {FRAME_BUFFER_SIZE} bytes")
    logger.info(f"데이터 디렉토리: {DATA_DIR}")
    logger.info(f"Admin 페이지: http://localhost:5000/admin")
    logger.info("========================================")

    # 기존 상태 프리셋 수 출력
    presets = load_presets()
    logger.info(f"📌 등록된 상태 프리셋: {len(presets)}개")


# ──────────────────────────────────────────────
#  직접 실행 시 Uvicorn으로 서버 기동
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port_num = int(os.environ.get("SERVER_PORT", 5000))
    logger.info(f"Starting uvicorn server on port {port_num}...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port_num,
        log_level="info",
        reload=True,  # 개발 중 코드 변경 시 자동 재시작
    )

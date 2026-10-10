#!/usr/bin/env python3
"""
파비콘 생성기 — server/static/ 의 favicon.svg, favicon.ico, apple-touch-icon.png 를 한 번에 만든다.

    python3 tools/make_icons.py      # Pillow 필요 (server/requirements.txt 에 포함)

도형은 아래 16x16 격자 한 곳에서만 정의한다. 좌표가 정수라 16px 에서도 선이 번지지 않는다.
라벤더 바탕 위에 흰 전자잉크 패널, 그 안에 글자 두 줄.
"""
from pathlib import Path

from PIL import Image, ImageDraw

STATIC_DIR = Path(__file__).resolve().parent.parent / "server" / "static"

BRAND = "#5e6ad2"   # Admin 테마의 --primary
PAPER = "#f7f8f8"   # --ink
INK = "#010102"     # --canvas

GRID = 16
BACKGROUND_RADIUS = 3
# (x, y, w, h, 모서리 반지름, 색)
SHAPES = [
    (2, 4, 12, 8, 1, PAPER),   # 패널
    (4, 6, 8, 1, 0, INK),      # 첫 줄
    (4, 9, 5, 1, 0, INK),      # 둘째 줄
]
SUPERSAMPLE = 8


def svg() -> str:
    parts = [f'<rect width="{GRID}" height="{GRID}" rx="{BACKGROUND_RADIUS}" fill="{BRAND}"/>']
    for x, y, w, h, r, color in SHAPES:
        radius = f' rx="{r}"' if r else ""
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{radius} fill="{color}"/>')
    body = "\n  ".join(parts)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}">\n  {body}\n</svg>\n'


def raster(size: int, rounded: bool = True) -> Image.Image:
    """size px 아이콘. rounded=False 면 바탕을 꽉 채운다 (iOS 가 직접 모서리를 깎는 홈 화면 아이콘용)."""
    big = size * SUPERSAMPLE
    unit = big / GRID
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if rounded:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=BACKGROUND_RADIUS * unit, fill=BRAND)
    else:
        draw.rectangle((0, 0, big - 1, big - 1), fill=BRAND)
    for x, y, w, h, r, color in SHAPES:
        box = (x * unit, y * unit, (x + w) * unit - 1, (y + h) * unit - 1)
        draw.rounded_rectangle(box, radius=r * unit, fill=color)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    (STATIC_DIR / "favicon.svg").write_text(svg(), encoding="utf-8")

    frames = [raster(size) for size in (16, 32, 48)]
    frames[-1].save(
        STATIC_DIR / "favicon.ico",
        format="ICO",
        sizes=[frame.size for frame in frames],
        append_images=frames[:-1],
    )

    # 홈 화면 아이콘은 투명 영역이 있으면 iOS 가 검게 채우므로 불투명 RGB 로 저장한다
    raster(180, rounded=False).convert("RGB").save(STATIC_DIR / "apple-touch-icon.png", optimize=True)

    for name in ("favicon.svg", "favicon.ico", "apple-touch-icon.png"):
        print(f"{name}: {(STATIC_DIR / name).stat().st_size} bytes")


if __name__ == "__main__":
    main()

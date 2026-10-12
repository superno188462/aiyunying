"""把 AI 提炼的文字渲染为可下载的小红书 PNG 卡片。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


FONT_PATHS = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
)
BOLD_FONT_PATHS = (
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (*BOLD_FONT_PATHS, *FONT_PATHS) if bold else FONT_PATHS:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise RuntimeError("缺少中文字体；请安装 Noto Sans CJK 或文泉驿微米黑")


def _lines(draw: ImageDraw.ImageDraw, content: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    result: list[str] = []
    for paragraph in content.splitlines():
        current = ""
        for char in paragraph:
            if draw.textlength(current + char, font=font) > max_width and current:
                result.append(current)
                current = char
            else:
                current += char
        result.append(current)
    return result


def save_background(workflow_id: int, index: int, artwork: Image.Image, output_root: Path) -> Path:
    """保存 Seedream 原图，后续修改文案时可以重复使用同一视觉。"""
    target_dir = output_root / str(workflow_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"background-{index + 1}.png"
    artwork.convert("RGB").save(target, "PNG")
    return target


def _fit_lines(draw: ImageDraw.ImageDraw, content: str, max_width: int, max_height: int, sizes: tuple[int, ...], *, bold: bool = False) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in sizes:
        font = _font(size, bold=bold)
        lines = _lines(draw, content.strip(), font, max_width)
        line_height = round(size * 1.42)
        if len(lines) * line_height <= max_height:
            return font, lines, line_height
    size = sizes[-1]
    font = _font(size, bold=bold)
    line_height = round(size * 1.42)
    max_lines = max(1, max_height // line_height)
    lines = _lines(draw, content.strip(), font, max_width)[:max_lines]
    if lines and len(_lines(draw, content.strip(), font, max_width)) > max_lines:
        lines[-1] = lines[-1].rstrip("…") + "…"
    return font, lines, line_height


def render_card(workflow_id: int, index: int, headline: str, body: str, output_root: Path) -> Path:
    """把 AI 视觉和已审核的中文文案合成为 1080×1440 PNG。"""
    target_dir = output_root / str(workflow_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"card-{index + 1}.png"
    image = Image.new("RGB", (1080, 1440), "#f3efe8")
    draw = ImageDraw.Draw(image)
    hero_box = (48, 48, 1032, 835)
    background = target_dir / f"background-{index + 1}.png"
    if background.is_file():
        with Image.open(background) as source:
            hero = ImageOps.fit(source.convert("RGB"), (984, 787), method=Image.Resampling.LANCZOS, centering=(0.5, 0.42))
    else:
        hero = Image.new("RGB", (984, 787), "#b8c9c0")
        hero_draw = ImageDraw.Draw(hero)
        hero_draw.ellipse((440, -180, 1150, 530), fill="#e2a880")
        hero_draw.ellipse((-220, 220, 510, 950), fill="#5e7f8a")
    mask = Image.new("L", hero.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 983, 786), radius=42, fill=255)
    image.paste(hero, hero_box[:2], mask)

    draw.rounded_rectangle((78, 82, 282, 138), radius=24, fill="#fffaf2")
    draw.text((108, 96), "内容灵感", font=_font(24, bold=True), fill="#26353c")
    draw.rounded_rectangle((78, 682, 1008, 1396), radius=44, fill="#ddd9d1")
    draw.rounded_rectangle((72, 670, 1008, 1384), radius=44, fill="#ffffff")
    draw.rounded_rectangle((116, 718, 204, 731), radius=7, fill="#d9684c")

    title_font, title_lines, title_height = _fit_lines(draw, headline, 840, 225, (70, 66, 62, 58, 54, 50, 46), bold=True)
    y = 775
    for line in title_lines:
        draw.text((116, y), line, font=title_font, fill="#202c32")
        y += title_height
    y += 30
    body_font, body_lines, body_height = _fit_lines(draw, body, 840, max(95, 1300 - y), (42, 40, 38, 36, 34, 32))
    for line in body_lines:
        draw.text((116, y), line, font=body_font, fill="#43545a")
        y += body_height
    draw.line((116, 1320, 962, 1320), fill="#e9e7e2", width=2)
    draw.text((116, 1336), "内容策划", font=_font(25), fill="#718087")
    draw.text((905, 1334), f"{index + 1:02d}", font=_font(30, bold=True), fill="#d9684c")
    image.save(target, "PNG")
    return target

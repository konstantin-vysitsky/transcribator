"""Значок трея: готовый рисунок микрофона из набора Feather.

Рисунок не рисуется кодом, а взят готовым: `assets/tray-mic.svg`. Из него
делается картинка при запуске. Доли счёта — из описания значка (24 на 24),
поэтому мелкие размеры выходят так же, как у остальных значков в трее.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

_ASSET = Path(__file__).resolve().parent.parent / "assets" / "tray-mic.svg"
# Цвет значка на панели задач. Другой цвет — другой файл или правка здесь.
_BLUE = "#4285F4"


def _render_svg(svg: str, size: int) -> Image.Image:
    """Нарисовать сообщение SVG в картинку нужного размера."""

    try:
        import resvg_py  # тяжёлая библиотека: берём её только при надобности
    except ImportError:
        # Системная политика подписей может запретить загрузку resvg_py.
        # Значок тогда рисуем сами: трей не должен падать из-за картинки.
        return _fallback_icon(size)

    data = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def _fallback_icon(size: int) -> Image.Image:
    """Микрофон, нарисованный вручную: капсула, дужка и подставка."""

    scale = 4  # рисуем крупнее и уменьшаем — края получаются ровными
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = _BLUE
    width = max(1, round(big * 0.075))

    capsule_w = big * 0.30
    capsule_h = big * 0.46
    left = (big - capsule_w) / 2
    top = big * 0.14
    draw.rounded_rectangle(
        (left, top, left + capsule_w, top + capsule_h),
        radius=capsule_w / 2,
        fill=color,
    )

    arc_h = big * 0.42
    arc_w = big * 0.56
    arc_box = (
        (big - arc_w) / 2,
        big * 0.40,
        (big + arc_w) / 2,
        big * 0.40 + arc_h,
    )
    draw.arc(arc_box, start=0, end=180, fill=color, width=width)

    stem_top = big * 0.40 + arc_h
    draw.line((big / 2, stem_top, big / 2, big * 0.88), fill=color, width=width)
    base_w = big * 0.44
    draw.line(
        ((big - base_w) / 2, big * 0.88, (big + base_w) / 2, big * 0.88),
        fill=color,
        width=width,
    )

    return img.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=8)
def make_icon(size: int = 64) -> Image.Image:
    """Значок нужного размера. Картинка одна на размер: она не меняется."""
    svg = _ASSET.read_text(encoding="utf-8").replace("currentColor", _BLUE)
    for color in ("#212121", "#000000"):
        svg = svg.replace(f'fill="{color}"', f'fill="{_BLUE}"')
    return _render_svg(svg, size)

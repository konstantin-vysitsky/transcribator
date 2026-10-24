"""Значок трея: готовый рисунок микрофона из набора Feather.

Рисунок не рисуется кодом, а взят готовым: `assets/tray-mic.svg`. Из него
делается картинка при запуске. Доли счёта — из описания значка (24 на 24),
поэтому мелкие размеры выходят так же, как у остальных значков в трее.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image

_ASSET = Path(__file__).resolve().parent.parent / "assets" / "tray-mic.svg"
# Цвет значка на панели задач. Другой цвет — другой файл или правка здесь.
_BLUE = "#4285F4"


def _render_svg(svg: str, size: int) -> Image.Image:
    """Нарисовать сообщение SVG в картинку нужного размера."""

    import resvg_py  # тяжёлая библиотека: берём её только при надобности

    data = resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size)
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


@lru_cache(maxsize=8)
def make_icon(size: int = 64) -> Image.Image:
    """Значок нужного размера. Картинка одна на размер: она не меняется."""
    svg = _ASSET.read_text(encoding="utf-8").replace("currentColor", _BLUE)
    for color in ("#212121", "#000000"):
        svg = svg.replace(f'fill="{color}"', f'fill="{_BLUE}"')
    return _render_svg(svg, size)

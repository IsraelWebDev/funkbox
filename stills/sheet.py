"""Contact sheets: a grid of images with a small caption under each (Pillow only)."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

BG = (24, 24, 28)
FG = (225, 225, 230)


def _font(px: int):
    try:
        return ImageFont.load_default(size=px)
    except TypeError:                      # Pillow < 10.1
        return ImageFont.load_default()


def contact_sheet(images: list[Image.Image], labels: list[str] | None = None, tile: int = 360,
                  cols: int | None = None, pad: int = 12) -> Image.Image:
    """Grid of ``images`` (each fitted into a ``tile`` x ``tile`` cell, aspect kept), captions below."""
    if not images:
        raise ValueError("contact sheet needs at least one image")
    n = len(images)
    cols = cols or (n if n <= 4 else 3 if n in (6, 9) else 4)
    cols = max(1, min(cols, n))
    rows = -(-n // cols)
    cap = 26 if labels else 0
    cw, ch = tile, tile + cap
    sheet = Image.new("RGB", (pad + cols * (cw + pad), pad + rows * (ch + pad)), BG)
    d = ImageDraw.Draw(sheet)
    f = _font(15)
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        x, y = pad + c * (cw + pad), pad + r * (ch + pad)
        t = im.convert("RGB").copy()
        t.thumbnail((cw, tile), Image.LANCZOS)
        sheet.paste(t, (x + (cw - t.width) // 2, y + (tile - t.height) // 2))
        if labels:
            d.text((x + 2, y + tile + 5), str(labels[i])[:60], font=f, fill=FG)
    return sheet

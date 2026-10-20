"""One still per shot (at its midpoint) + the end card, as a contact sheet with the shot ID,
duration and copy IDs printed BELOW each frame (never on it). The agent iterates on this."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from funkbox.media import font as _font

from . import render as R
from .timeline import ShotSpan

PAD = 10
LABEL_LINES = 3


def _labels(plan: R.Plan, sp: ShotSpan) -> list[str]:
    if sp.kind == "end":
        ec = plan.reel.end_card
        assert ec is not None
        cp = ",".join(f"{k}:{v}" for k, v in ec.copy.items())
        return [f"end_card  {sp.duration:.2f}s", f"{ec.template}", cp]
    sh = plan.reel.shots[sp.index]
    mid = sh.motion.type + ("+product" if sh.product else "")
    cap = f"cap:{sh.caption.copy_id}/{sh.caption.reveal}" if sh.caption else "cap:-"
    vo = f" vo:{sh.vo}" if sh.vo else ""
    return [f"{sh.id}  {sp.duration:.2f}s  {sh.source.kind}", f"{cap}{vo}", f"{mid}  in:{sh.transition_in}"]


def storyboard(plan: R.Plan, out: str | Path | None = None, tile_w: int = 270, cols: int | None = None,
               depth_mode: str = "cached") -> Image.Image:
    """Contact sheet PNG. Frames are taken at each shot's midpoint."""
    reel = plan.reel
    tile_h = round(tile_w * reel.settings.height / reel.settings.width) // 2 * 2
    n = len(plan.spans)
    cols = cols or (4 if n > 8 else min(n, 4))
    rows = -(-n // cols)
    fs = max(11, tile_w // 20)
    font = _font(fs)
    label_h = LABEL_LINES * (fs + 4) + 8
    sheet = Image.new("RGB", (cols * (tile_w + PAD) + PAD, rows * (tile_h + label_h + PAD) + PAD), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    # Render at a real ad size and shrink, rather than rendering tiny: caption layout has an absolute 8 px floor,
    # so at thumbnail size a line that fits the actual ad can fail to fit (seen on Windows at 100 px tiles).
    rw = min(reel.settings.width, max(tile_w, 540))
    rw -= rw % 2
    rh = round(rw * reel.settings.height / reel.settings.width) // 2 * 2
    r = R.Renderer(reel, plan.deck, plan.spans, width=rw, height=rh, depth_mode=depth_mode)
    try:
        for i, sp in enumerate(plan.spans):
            mid = sp.start_frame + (sp.end_frame - sp.start_frame) // 2
            fr = r.frame(mid)
            if (rw, rh) != (tile_w, tile_h):
                fr = np.asarray(Image.fromarray(fr).resize((tile_w, tile_h), Image.LANCZOS))
            x = PAD + (i % cols) * (tile_w + PAD)
            y = PAD + (i // cols) * (tile_h + label_h + PAD)
            sheet.paste(Image.fromarray(fr), (x, y))
            for k, line in enumerate(_labels(plan, sp)):
                d.text((x + 2, y + tile_h + 4 + k * (fs + 4)), line, font=font, fill=(235, 235, 235) if k == 0 else (170, 175, 185))
    finally:
        r.release()
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
    return sheet

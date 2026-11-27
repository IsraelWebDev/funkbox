"""Frame review sheet (spec 3.6): the first and last frame of every shot of a rendered reel, plus the final frame.

Look at this before reporting a render as done. It shows a cut that starts on the wrong frame, a caption cut off at
the edge, or an end card that never finished. ``review_sheet`` only reads frames; it does not change the render.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from funkbox.media import ffmpeg_exe, font

EDGE_S = 0.04  # sample this far inside each shot's start, so the frame belongs to that shot
END_S = 0.1  # the last frame is sampled this far before the end (a few frames: the encoder's last frame may be short)


class ReviewError(RuntimeError):
    pass


def _grab(ff: str, mp4: Path, t: float) -> Image.Image:
    """One frame at time ``t`` (seconds) from ``mp4``, as an RGB image."""
    cmd = [
        ff, "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(mp4),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
    ]  # fmt: skip
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise ReviewError(f"ffmpeg could not read a frame at {t:.2f}s of {mp4.name}")
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def review_sheet(
    reel_path: str | Path, render_mp4: str | Path | None = None, out_dir: str | Path | None = None, tile_w: int = 180
) -> Path:
    """Write ``<out>/review/<reel>-review.png``: one row per shot (start frame, end frame) and a final row (the last
    frame). ``render_mp4`` defaults to ``out/<reel>.mp4`` beside the reel."""
    from funkbox.reel import render as R  # heavy import: only when a sheet is asked for

    reel_path = Path(reel_path)
    plan = R.prepare(str(reel_path))
    name = reel_path.name.split(".")[0]
    mp4 = Path(render_mp4) if render_mp4 else reel_path.parent / "out" / f"{name}.mp4"
    if not mp4.exists():
        raise ReviewError(f"no render at {mp4}; render the reel first (`funkbox reel render`)")
    ff = ffmpeg_exe()
    if not ff:
        raise ReviewError("ffmpeg is needed for the frame review (pip install imageio-ffmpeg)")
    total = plan.spans[-1].end if plan.spans else 0.0
    rows: list[tuple[str, Image.Image | None, Image.Image | None]] = []
    for sp in plan.spans:
        first = _grab(ff, mp4, sp.start + EDGE_S)
        last = _grab(ff, mp4, sp.end - END_S)
        rows.append((f"{sp.id}  {sp.duration:.2f}s", first, last))
    rows.append(("final frame", _grab(ff, mp4, total - END_S), None))

    sample = rows[0][1]
    tile_h = round(tile_w * sample.height / sample.width)
    fnt = font(12)
    label_w = 150
    width = label_w + 2 * tile_w + 3 * 8
    height = len(rows) * (tile_h + 8) + 8
    sheet = Image.new("RGB", (width, height), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, (label, a, b) in enumerate(rows):
        y = 8 + i * (tile_h + 8)
        d.text((8, y + tile_h // 2 - 6), label, font=fnt, fill=(235, 235, 235))
        for j, im in enumerate((a, b)):
            x = label_w + j * (tile_w + 8)
            if im is None:
                continue
            sheet.paste(im.resize((tile_w, tile_h), Image.LANCZOS), (x, y))
    out = Path(out_dir) if out_dir else reel_path.parent / "out"
    out = out / "review"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"{name}-review.png"
    sheet.save(dest)
    return dest

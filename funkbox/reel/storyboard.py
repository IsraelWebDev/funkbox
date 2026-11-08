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


def _reason(e: Exception) -> str:
    """One short line for why the beat grid could not be made. A ``cannot decode <path>: ...`` message
    (decode_mono) loses the path and keeps the ffmpeg error after it (harmless labels such as "Error opening
    input file: ..." stay); an OSError keeps the first label and the file name. If the ffmpeg error is empty,
    the exception class name plus "cannot decode <file name>" stands in. What is left is cut to 80 characters."""
    text = " ".join(str(e).split())
    if isinstance(e, OSError):
        if e.filename:
            name, label = Path(str(e.filename)).name, e.strerror or type(e).__name__
        else:  # no filename: the path sits in the message instead (config.resolve_path: "file not found: <path>")
            tail = text.rpartition(": ")[2].strip("'\"")
            name, label = (Path(tail).name, text.rpartition(": ")[0]) if tail else ("", text)
        if name:
            text = f"{label}: {name}"
    if text.startswith("cannot decode "):
        path, _, ffmpeg = text[len("cannot decode ") :].partition(": ")
        if ffmpeg.startswith(path + ": "):  # decode_mono echoes ffmpeg's own "<path>: <error>" line
            ffmpeg = ffmpeg[len(path) + 2 :]
        if ffmpeg:  # drop the path; keep the ffmpeg error the way ffmpeg phrased it
            text = ffmpeg
        else:  # no ffmpeg error after the path: the exception name stands in, with the file name only
            text = f"{type(e).__name__}: cannot decode {Path(path).name}"
    return text[:80] or type(e).__name__


def beat_grid_lines(plan: R.Plan) -> list[str]:
    """The beat grid the reel is cut against: tempo, beat count (up to the end of the reel), the first 8 beats, and
    for every cut the distance in ms to the nearest beat. ``["beat grid: none"]`` when the reel has no grid."""
    from . import audio, timeline

    try:
        beats = audio.beat_grid_for(plan.reel)
    except (ValueError, RuntimeError, OSError) as e:  # missing, undecodable, short or silent music: still prints
        return [f"beat grid: none ({_reason(e)})"]
    if not beats or len(beats) < 2:
        return ["beat grid: none"]
    total = plan.spans[-1].end if plan.spans else 0.0
    n = sum(1 for b in beats if b <= total + 1e-6)
    bpm = 60.0 / timeline.beat_period(beats)
    lines = [
        f"beat grid: {bpm:.1f} BPM, {n} beats in {total:.1f}s",
        "first beats (s): " + ", ".join(f"{b:.3f}" for b in beats[:8]),
    ]
    for sp in plan.spans[1:]:  # a cut is where a shot starts, after the first
        d = min(abs(sp.start - b) for b in beats)
        lines.append(f"cut {sp.id} at {sp.start:.2f}s: {d * 1000:.0f} ms from the nearest beat")
    return lines


def print_beat_grid(plan: R.Plan, file=None) -> None:
    """Print ``beat_grid_lines`` (the CLI's storyboard report; the MCP tool must not print to stdout)."""
    for line in beat_grid_lines(plan):
        print(line, file=file)


def storyboard(
    plan: R.Plan, out: str | Path | None = None, tile_w: int = 270, cols: int | None = None, depth_mode: str = "cached"
) -> Image.Image:
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
                d.text(
                    (x + 2, y + tile_h + 4 + k * (fs + 4)),
                    line,
                    font=font,
                    fill=(235, 235, 235) if k == 0 else (170, 175, 185),
                )
    finally:
        r.release()
    if out is not None:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
    return sheet

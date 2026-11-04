"""Grid source geometry and timing: where each cell sits, when it arrives, how it drops in.

Pure functions, shared by the validator and the renderer so both see the same cells.
"""
from __future__ import annotations

import dataclasses

from . import captions, framing
from .schema import Grid, Motion, Source

ARRIVE_SECONDS = 0.25          # a cell fades in (and drops into place) over this long
DROP_FRACTION = 0.10           # a cell starts this fraction of its height above its place
DEFAULT_STAGGER = 0.25         # seconds between cells when the reel has no beat grid
MIN_CELL = 16                  # px: a smaller cell is not a picture anyone can see


def geometry_error(cols: int, rows: int, gap: float, W: int, H: int) -> str | None:
    """Why this grid cannot be laid out in a W x H frame (the gaps leave no room, or a cell would be smaller than
    MIN_CELL pixels), or None when it can. ``cell_rects`` refuses such a grid instead of clamping it."""
    gp = int(round(gap * W))
    if (cols - 1) * gp >= W or (rows - 1) * gp >= H:
        return (f"the gap ({gp} px) leaves no room for {cols} x {rows} cells in a {W}x{H} frame; "
                f"use a smaller gap or fewer cells")
    cw = (W - (cols - 1) * gp) // cols
    ch = (H - (rows - 1) * gp) // rows
    if cw < MIN_CELL or ch < MIN_CELL:
        return (f"each cell would be {cw}x{ch} px in a {W}x{H} frame (the minimum is {MIN_CELL} px); "
                f"use a smaller gap or fewer cells")
    return None


def cell_rects(cols: int, rows: int, gap: float, W: int, H: int) -> list[tuple[int, int, int, int]]:
    """Row-major (x, y, w, h) of every cell. ``gap`` is a fraction of the output width and applies between
    columns and between rows; cells are equal in size and the block is centred (no border). Raises ValueError
    when the grid cannot be laid out (see ``geometry_error``)."""
    err = geometry_error(cols, rows, gap, W, H)
    if err:
        raise ValueError(err)
    gp = int(round(gap * W))
    cw = (W - (cols - 1) * gp) // cols
    ch = (H - (rows - 1) * gp) // rows
    ox = (W - (cols * cw + (cols - 1) * gp)) // 2
    oy = (H - (rows * ch + (rows - 1) * gp)) // 2
    return [(ox + c * (cw + gp), oy + r * (ch + gp), cw, ch) for r in range(rows) for c in range(cols)]


def stagger_seconds(grid: Grid, beat_period: float | None) -> float:
    """Seconds between two cells' arrivals: the given seconds, ``beats:N`` times the beat period, or the
    default (one beat with a beat grid, else 0.25 s)."""
    st = grid.stagger
    if st is None:
        return beat_period if beat_period else DEFAULT_STAGGER
    if isinstance(st, str):
        if not beat_period:
            raise ValueError(f"grid stagger {st!r} needs a beat grid (set audio.bpm, or audio.music to detect one, "
                             f"or use seconds)")
        return float(st[6:]) * beat_period
    return float(st)


def arrival(grid: Grid, index: int, stagger: float) -> float:
    """Local shot time (seconds) at which cell ``index`` arrives."""
    return index * stagger if grid.reveal == "sequence" else 0.0


def pose(grid: Grid, since: float, cell_h: int) -> tuple[float, int] | None:
    """(alpha, dy) of a cell ``since`` seconds after its arrival, or None while it is not drawn. ``dy`` is the
    vertical offset in pixels (<= 0: above its place)."""
    if since < 0:
        return None
    if grid.reveal != "sequence":
        return 1.0, 0
    if since <= 1e-9:                                  # the arrival frame itself (alpha 0), with float tolerance
        return None
    u = min(1.0, since / ARRIVE_SECONDS)
    return u, -int(round((1.0 - framing.ease(grid.ease, u)) * DROP_FRACTION * cell_h))


def arrives_in_time(arrive: float, duration: float, fps: float) -> bool:
    """True when a cell arriving at ``arrive`` has fully arrived (drawn, label included) before its shot of
    ``duration`` seconds ends. The cell is first drawn one frame after ``arrive``, so a cell that arrives within the
    last frames is never drawn at all and its label cannot be audited; one that cannot finish its fade-in is cut."""
    return arrive + max(ARRIVE_SECONDS, 2.0 / max(fps, 1.0)) <= duration + 1e-6


def layout_label(copy_id: str, text: str, style_name: str, fonts, brand, rect: tuple[int, int, int, int],
                 W: int, H: int, safe_area: str) -> captions.Layout:
    """Caption layout of a cell's label: laid out at the cell's size (the bottom of the cell) by the caption path,
    then moved up where needed so the box stays out of the reel's platform-UI bands (``safe_area`` is the reel's,
    measured on the whole frame). Raises CaptionFitError when it cannot sit inside its cell and the safe area."""
    x, y, cw, ch = rect
    lay = captions.layout_block(copy_id, text, captions.load_style(style_name), fonts, brand, cw, ch,
                                anchor="bottom", safe_area="none")
    if safe_area == "none" or lay.style.y is not None:
        return lay
    top, bot = ((captions.SAFE_TOP, 1.0 - captions.SAFE_BOTTOM) if safe_area == "reels"
                else (captions.LAND_TOP, 1.0 - captions.LAND_BOTTOM))
    y0 = min(lay.y0, int(round(bot * H - y - lay.box_h)))
    if y0 < 0 or y + y0 < int(round(top * H)):
        raise captions.CaptionFitError(
            f"copy {copy_id!r}: the label does not fit in its cell inside the {safe_area} safe area "
            f"(cell {cw}x{ch} at y={y}; use fewer rows, a shorter line or safe_area \"none\")")
    return dataclasses.replace(lay, y0=y0)


def motion_crops(motion: Motion, box: tuple[float, float, float, float], W: int, H: int,
                 seconds: float) -> bool:
    """True when the shot's own motion (applied to the whole composed grid) ever shows a window that does not
    contain ``box`` (x, y, w, h in output pixels): the box (a label) would be cut off at the frame edge."""
    if motion.type not in ("kenburns", "frame"):
        return False
    from . import motion as motionmod                  # GL module: only needed when there is motion to check
    x, y, w, h = box
    for k in range(21):
        u = k / 20.0
        cam = motionmod.camera(motion, u, "kenburns") if motion.type == "kenburns" else None
        win = framing.window_for(motion, Source(), u, seconds, W, H, W, H, cam)
        if x < win.x0 - 0.5 or y < win.y0 - 0.5 or x + w > win.x1 + 0.5 or y + h > win.y1 + 0.5:
            return True
    return False

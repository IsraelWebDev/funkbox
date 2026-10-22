"""Framing: which part of a source each output frame shows, and drawing it (crop + scale only).

Used for video sources (any motion, speed ramps) and for ``motion.type == "frame"`` on stills. Nothing here
changes pixel content: a frame is a window of the source, resampled to the output size, with padding (a flat
colour or a blurred copy of the same frame) only where the window reaches past the source edge.

Coordinates: a *rect* is (x, y, w, h) in fractions of the source; a *window* is (x0, y0, x1, y1) in source
pixels and always has the output aspect ratio.
"""
from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from funkbox.media import require_ffmpeg

from .schema import FrameKey, Motion, Source

FULL = (0.0, 0.0, 1.0, 1.0)
MAX_DECODE_SIDE = 3840              # decode big sources at most this large (long side)
BLUR_DOWNSCALE = 8


# --------------------------------------------------------------------------- #
# Probing
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float | None


@lru_cache(maxsize=64)
def _probe(path: str, mtime: int) -> VideoInfo:
    r = subprocess.run([require_ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True, timeout=30)
    err = r.stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if not m:
        raise ValueError(f"{path}: no video stream found")
    w, h = int(m.group(1)), int(m.group(2))
    rot = re.search(r"rotate\s*:\s*(-?\d+)|rotation of (-?\d+)", err)
    if rot and abs(int(rot.group(1) or rot.group(2))) % 180 == 90:
        w, h = h, w
    f = re.search(r"(\d+(?:\.\d+)?) fps", err)
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", err)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else None
    return VideoInfo(w, h, float(f.group(1)) if f else 30.0, dur)


def probe_video(path: Path) -> VideoInfo:
    return _probe(str(path), Path(path).stat().st_mtime_ns)


def decode_size(info: VideoInfo) -> tuple[int, int]:
    """Size frames are decoded at: native, unless the long side is over MAX_DECODE_SIDE (even numbers)."""
    k = min(1.0, MAX_DECODE_SIDE / max(info.width, info.height))
    return max(2, int(info.width * k) // 2 * 2), max(2, int(info.height * k) // 2 * 2)


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
def source_time(t: float, shot_seconds: float, speed: tuple[float, float]) -> float:
    """Seconds into the source after ``t`` seconds of the shot, with speed ramping linearly from speed[0] at the
    shot start to speed[1] at its end (the integral of the speed)."""
    a, b = speed
    d = max(shot_seconds, 1e-6)
    t = max(0.0, t)
    return a * t + (b - a) * t * t / (2.0 * d)


def decode_fps(fps: int, speed: tuple[float, float]) -> int:
    """Decode rate so that every output frame maps onto a decoded frame, also at the fastest speed."""
    return int(fps * max(1, math.ceil(max(speed))))


# --------------------------------------------------------------------------- #
# Where the camera looks
# --------------------------------------------------------------------------- #
def _ease(kind: str, u: float) -> float:
    u = min(1.0, max(0.0, u))
    if kind == "linear":
        return u
    if kind == "in":
        return u * u
    if kind == "out":
        return 1.0 - (1.0 - u) ** 2
    if kind == "hold":
        return 1.0 if u >= 1.0 else 0.0
    return u * u * (3.0 - 2.0 * u)


def key_times(keys: tuple[FrameKey, ...], shot_seconds: float) -> list[float]:
    """Key times as fractions of the shot (``s`` seconds are converted; past the shot end clamps to 1)."""
    d = max(shot_seconds, 1e-6)
    return [min(1.0, k.t if k.t is not None else (k.s or 0.0) / d) for k in keys]


def rect_at(keys: tuple[FrameKey, ...], u: float, shot_seconds: float) -> tuple[float, float, float, float]:
    """The framed source rect at shot fraction ``u``: centre moves linearly, size changes geometrically (so a
    push-in feels even), each segment eased by the arriving key's ``ease``."""
    if not keys:
        return FULL
    ts = key_times(keys, shot_seconds)
    if u <= ts[0]:
        return keys[0].rect
    for i in range(1, len(keys)):
        if u <= ts[i]:
            span = ts[i] - ts[i - 1]
            e = _ease(keys[i].ease, (u - ts[i - 1]) / span if span > 0 else 1.0)
            (x0, y0, w0, h0), (x1, y1, w1, h1) = keys[i - 1].rect, keys[i].rect
            cx = (x0 + w0 / 2) + ((x1 + w1 / 2) - (x0 + w0 / 2)) * e
            cy = (y0 + h0 / 2) + ((y1 + h1 / 2) - (y0 + h0 / 2)) * e
            w = w0 * (w1 / w0) ** e
            h = h0 * (h1 / h0) ** e
            return (cx - w / 2, cy - h / 2, w, h)
    return keys[-1].rect


@dataclass(frozen=True)
class Window:
    x0: float
    y0: float
    x1: float
    y1: float
    cut: float = 0.0          # fraction of the wanted rect's area that could not be shown (cover fit only)

    @property
    def inside(self) -> bool:
        return self.x0 >= -1e-6 and self.y0 >= -1e-6

    def box(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


def window(rect: tuple[float, float, float, float], src_w: int, src_h: int, out_w: int, out_h: int, *,
           fit: str = "cover", centre: tuple[float, float] | None = None) -> Window:
    """Smallest output-aspect window containing ``rect``, centred on it (or on ``centre``, fractions of the
    source).

    ``cover``: the window stays inside the source; when the rect is too big for the output aspect the window
    shrinks to the largest one that fits (``cut`` says how much of the rect was lost).
    ``contain``: the rect is never cut; where the window reaches past the source edge it is padded.
    """
    x, y, w, h = rect
    rw, rh = w * src_w, h * src_h
    aspect = out_w / out_h
    if rw / rh > aspect:
        ww, wh = rw, rw / aspect
    else:
        ww, wh = rh * aspect, rh
    cx = (centre[0] if centre else x + w / 2) * src_w
    cy = (centre[1] if centre else y + h / 2) * src_h
    cut = 0.0
    if fit == "cover":
        k = min(1.0, src_w / ww, src_h / wh)
        if k < 1.0:
            ww, wh = ww * k, wh * k
            cut = max(0.0, 1.0 - (min(ww, rw) * min(wh, rh)) / (rw * rh))
        cx = min(max(cx, ww / 2), src_w - ww / 2)
        cy = min(max(cy, wh / 2), src_h - wh / 2)
    else:
        cx = src_w / 2 if ww >= src_w else min(max(cx, ww / 2), src_w - ww / 2)
        cy = src_h / 2 if wh >= src_h else min(max(cy, wh / 2), src_h - wh / 2)
    return Window(cx - ww / 2, cy - wh / 2, cx + ww / 2, cy + wh / 2, cut)


def window_for(motion: Motion, source: Source, u: float, shot_seconds: float, src_w: int, src_h: int,
               out_w: int, out_h: int, kenburns_cam=None) -> Window:
    """The window of one frame: ``frame`` keys; Ken Burns (zoom/pan inside the base framing); or the base
    framing alone (cover crop centred on ``source.focus``, or contain)."""
    if motion.type == "frame" and motion.keys:
        return window(rect_at(motion.keys, u, shot_seconds), src_w, src_h, out_w, out_h, fit=source.fit)
    base = window(FULL, src_w, src_h, out_w, out_h, fit=source.fit, centre=source.focus)
    if kenburns_cam is None or (kenburns_cam.zoom == 1.0 and kenburns_cam.pan == (0.0, 0.0)):
        return base
    bw, bh = base.x1 - base.x0, base.y1 - base.y0
    z = kenburns_cam.zoom
    cx = (base.x0 + base.x1) / 2 + kenburns_cam.pan[0] * bw
    cy = (base.y0 + base.y1) / 2 + kenburns_cam.pan[1] * bh
    return Window(cx - bw / z / 2, cy - bh / z / 2, cx + bw / z / 2, cy + bh / z / 2)


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def _hex(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _blurred_cover(im: Image.Image, out_w: int, out_h: int) -> Image.Image:
    """The whole frame, cover-scaled to the output, blurred and dimmed: a backdrop for padded areas."""
    sw, sh = max(4, out_w // BLUR_DOWNSCALE), max(4, out_h // BLUR_DOWNSCALE)
    k = max(sw / im.width, sh / im.height)
    small = im.resize((max(sw, round(im.width * k)), max(sh, round(im.height * k))), Image.BILINEAR)
    l, t = (small.width - sw) // 2, (small.height - sh) // 2
    small = small.crop((l, t, l + sw, t + sh)).filter(ImageFilter.GaussianBlur(3))
    arr = (np.asarray(small, np.float32) * 0.55).astype(np.uint8)
    return Image.fromarray(arr).resize((out_w, out_h), Image.BILINEAR)


def draw(src: np.ndarray | Image.Image, win: Window, out_w: int, out_h: int, pad: str = "#000000") -> np.ndarray:
    """Resample the window of ``src`` to out_w x out_h (bicubic, with box pre-reduction when shrinking a lot)."""
    im = src if isinstance(src, Image.Image) else Image.fromarray(np.ascontiguousarray(src))
    sw, sh = im.size
    scale = out_w / max(1e-6, win.x1 - win.x0)
    gap = 2.0 if scale < 0.5 else None
    if win.x0 >= -1e-6 and win.y0 >= -1e-6 and win.x1 <= sw + 1e-6 and win.y1 <= sh + 1e-6:
        box = (max(0.0, win.x0), max(0.0, win.y0), min(float(sw), win.x1), min(float(sh), win.y1))
        return np.asarray(im.resize((out_w, out_h), Image.BICUBIC, box=box, reducing_gap=gap))
    canvas = _blurred_cover(im, out_w, out_h) if pad == "blur" else Image.new("RGB", (out_w, out_h), _hex(pad))
    ix0, iy0 = max(0.0, win.x0), max(0.0, win.y0)
    ix1, iy1 = min(float(sw), win.x1), min(float(sh), win.y1)
    if ix1 > ix0 and iy1 > iy0:
        dx0, dy0 = (ix0 - win.x0) * scale, (iy0 - win.y0) * scale
        dw, dh = max(1, round((ix1 - ix0) * scale)), max(1, round((iy1 - iy0) * scale))
        part = im.resize((dw, dh), Image.BICUBIC, box=(ix0, iy0, ix1, iy1), reducing_gap=gap)
        canvas.paste(part, (round(dx0), round(dy0)))
    return np.asarray(canvas)


def needs_windowing(source: Source, motion: Motion) -> bool:
    """True when a video source cannot use the plain ffmpeg scale/crop path (kept for speed when it can)."""
    return (motion.type in ("frame", "kenburns") or source.speed != (1.0, 1.0) or source.focus is not None
            or (source.fit == "contain" and source.pad != "#000000"))


def needs_windowing_image(source: Source, motion: Motion) -> bool:
    """True when a still uses the CPU framing path instead of the GL camera (``frame`` motion, a focus point, or
    a padded contain fit). Parallax always stays on the GL path."""
    if source.kind != "image" or motion.type == "parallax":
        return False
    return (motion.type == "frame" or source.focus is not None
            or (source.fit == "contain" and source.pad != "#000000"))

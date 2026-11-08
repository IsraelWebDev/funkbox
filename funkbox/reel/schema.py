"""Reel dataclasses + strict JSON parsing/validation.

The schema has NO free-text fields. Text reaches pixels only through the copy deck
(``reel.copydeck``) by ID. Any unknown key is an error, and keys that look like free text
(``text``, ``label``, ``caption_text`` ...) get a dedicated message.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SAFE_AREAS = ("reels", "landscape", "none")
MOTION_TYPES = ("none", "kenburns", "parallax", "frame")
EASES = ("smooth", "linear", "in", "out", "hold")
REVEALS = ("none", "typewriter", "words", "pop")
GRID_REVEALS = ("none", "sequence")  # grid source: all cells at once | one after another
ANCHORS = ("top", "center", "bottom", "auto")  # "auto": the renderer picks from the frame (else bottom)
TRANSITIONS = ("cut", "flash", "push", "crossfade", "zoom-punch", "auto")  # "auto": resolved per shot, see timeline
END_AUDIO = ("silence", "keep")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
BEATS_RE = re.compile(r"^beats:([0-9]{1,3}(?:\.[0-9]{1,3})?)$")  # beats:4, beats:1.5, beats:0.5

#: format presets: id -> (width, height, safe_area)
FORMAT_PRESETS = {
    "9x16": (1080, 1920, "reels"),
    "16x9": (1920, 1080, "landscape"),
    "1x1": (1080, 1080, "landscape"),
    "4x5": (1080, 1350, "landscape"),
}
#: what a shot may override per format (motion is replaced whole; the others are merged key by key)
SHOT_FORMAT_KEYS = {
    "motion": None,
    "caption": ("anchor",),
    "product": ("x", "y", "height"),
    "source": ("focus", "fit", "pad"),
}

# keys that would smuggle literal copy into a reel; always rejected (except brand.text colour)
FORBIDDEN_KEYS = (
    "text",
    "label",
    "caption_text",
    "title",
    "subtitle",
    "headline_text",
    "string",
    "content",
    "copy_text",
    "message",
    "say",
    "body",
    "name_text",
)
# the one place `label` is allowed: a grid cell's label is a copy ID (checked against the deck), not text
GRID_LABEL_PATH_RE = re.compile(r"^shots\[\d+\]\.source\.grid\.cells\[\d+\]\.label$")


class ReelError(ValueError):
    """Schema/validation failure. ``errors`` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    safe_area: str = "reels"


@dataclass
class Brand:
    primary: str = "#c8102e"
    secondary: str = "#1a1a1a"
    text: str = "#ffffff"  # a COLOUR (the one allowed `text` key); see validate_forbidden
    font_caption: str = "fonts/Oswald-SemiBold.ttf"
    font_display: str = "fonts/DMSerifDisplay-Regular.ttf"
    logo: str | None = None
    font_fallback: str | None = "fonts/Rubik-Bold.ttf"  # draws text the caption/display font cannot (Hebrew, Cyrillic)
    brand_from: str | None = None  # "from": image/mp4 the colours were resolved from (None = explicit colours)


@dataclass
class Audio:
    music: str | None = None
    bpm: float | None = None  # fixed beat grid: beats:N and snapping need no music detection
    beat_offset: float = 0.0  # seconds of the first beat on that grid
    music_gain_db: float = -14.0
    voiceover: bool = False
    voice: str | None = None  # "kokoro:af_heart" | "kokoro:am_michael" | "kokoro:bm_george" | "piper"
    voice_speed: float = 1.0
    duck_db: float = -10.0
    snap_cuts_to_beats: bool = False
    loudness_lufs: float = -14.0


@dataclass(frozen=True)
class GridCell:
    video: str | None = None  # a path (video only: image cells are not supported)
    fit: str = "contain"  # cover | contain
    label: str | None = None  # a copy ID (never text), drawn at the bottom of the cell
    label_style: str = "bold-pop"
    label_index: bool = False  # {"index": true}: draws the cell's 1-based number (the variant number)


@dataclass
class Grid:
    """``source.grid``: clips side by side, ``cells`` row-major (``cols * rows`` of them)."""

    cols: int = 2
    rows: int = 2
    gap: float = 0.02  # of the output width
    reveal: str = "none"  # none | sequence
    stagger: float | str | None = None  # seconds | "beats:N" | None (= beats:1 with a beat grid, else 0.25 s)
    ease: str = "out"
    cells: tuple[GridCell, ...] = ()


@dataclass
class Source:
    kind: str = "color"  # image | video | color | funk2 | grid
    value: str = "#000000"  # path or hex colour ("" for a grid)
    trim: tuple[float, float] | None = None
    loop: bool = False
    fit: str = "cover"
    focus: tuple[float, float] | None = None  # cover-crop centre, fractions of the source
    pad: str = "#000000"  # fit=contain / framing past the edge: #hex or "blur"
    speed: tuple[float, float] = (1.0, 1.0)  # video playback speed at shot start / end (ramp)
    grid: Grid | None = None  # kind "grid" only


@dataclass
class Motion:
    type: str = "none"
    zoom: tuple[float, float] = (1.0, 1.0)
    pan: tuple[tuple[float, float], tuple[float, float]] = ((0.0, 0.0), (0.0, 0.0))
    push: float = 0.0
    orbit_deg: float = 0.0
    dof: float = 0.0
    keys: tuple["FrameKey", ...] = ()  # type "frame": source rectangles over the shot


@dataclass(frozen=True)
class FrameKey:
    """One framing keyframe: show ``rect`` (x, y, w, h as fractions of the SOURCE) at time ``t``
    (fraction of the shot, 0..1) or ``s`` (seconds from the shot start). The output window is the smallest
    rectangle of the output aspect that contains ``rect``; between keys it moves with ``ease``."""

    rect: tuple[float, float, float, float]
    t: float | None = None
    s: float | None = None
    ease: str = "smooth"


@dataclass
class ProductSpec:
    image: str = ""
    x: float = 0.5
    y: float = 0.62
    height: float = 0.4
    depth: float = 0.8
    shadow: float = 0.6
    match_color: float = 0.0


@dataclass
class CaptionSpec:
    copy_id: str = ""
    style: str = "red-box"
    reveal: str = "none"
    anchor: str = "bottom"
    highlight: tuple | None = None  # (colour, radius) of the word pill; see parse_highlight


@dataclass
class Shot:
    id: str = ""
    source: Source = field(default_factory=Source)
    motion: Motion = field(default_factory=Motion)
    product: ProductSpec | None = None
    caption: CaptionSpec | None = None
    vo: str | None = None  # copy id spoken during the shot
    duration: float | str = "auto"  # seconds | "auto" | "beats:N"
    transition_in: str = "cut"


@dataclass
class EndCard:
    template: str = "brand-split"
    duration: float = 5.0
    audio: str = "silence"
    copy: dict[str, str] = field(default_factory=dict)  # slot -> copy id
    product: str | None = None
    headline_reveal: str = "typewriter"


@dataclass(frozen=True)
class Format:
    id: str
    width: int
    height: int
    safe_area: str


@dataclass
class Reel:
    version: int = 3
    settings: Settings = field(default_factory=Settings)
    brand: Brand = field(default_factory=Brand)
    copy: str = ""
    audio: Audio = field(default_factory=Audio)
    shots: list[Shot] = field(default_factory=list)
    end_card: EndCard | None = None
    base_dir: str | None = None  # folder of the reel file; paths resolve against it
    formats: list[Format] = field(default_factory=list)  # extra output formats (see materialize)
    format_id: str | None = None  # set when this Reel is one format's variant
    preset: str | None = None  # the templates/types preset this reel was built with, if any
    hook: bool = False  # shot 1's caption is a large title box for the first HOOK_SECONDS
    shot_target_s: float | None = None  # pacing: the target length of auto shots, in seconds
    warnings: list[str] = field(default_factory=list)  # non-fatal notes from parsing (e.g. brand.from fallback)

    def shot(self, shot_id: str) -> Shot:
        for s in self.shots:
            if s.id == shot_id:
                return s
        raise KeyError(f"no shot {shot_id!r}; have {[s.id for s in self.shots]}")


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
class _R:
    """Reads a dict with unknown-key tracking and error collection."""

    def __init__(self, d: Any, path: str, errs: list[str]):
        self.path, self.errs = path, errs
        if not isinstance(d, dict):
            errs.append(f"{path or 'reel'}: must be a JSON object")
            d = {}
        self.d, self.used = d, set()

    def _p(self, k: str) -> str:
        return f"{self.path}.{k}" if self.path else k

    def has(self, k: str) -> bool:
        return k in self.d

    def raw(self, k: str, default=None):
        self.used.add(k)
        return self.d.get(k, default)

    def num(
        self, k: str, default: float, lo: float | None = None, hi: float | None = None, integer: bool = False
    ) -> float:
        v = self.raw(k, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (integer and int(v) != v):
            self.errs.append(f"{self._p(k)}: expected {'an integer' if integer else 'a number'}, got {v!r}")
            return default
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            self.errs.append(f"{self._p(k)}: {v} out of range [{lo}, {hi}]")
            return default
        return int(v) if integer else float(v)

    def s(
        self,
        k: str,
        default: str | None,
        choices: tuple[str, ...] | None = None,
        required: bool = False,
        pattern: re.Pattern | None = None,
    ) -> str | None:
        if required and k not in self.d:
            self.errs.append(f"{self._p(k)}: required")
            return default
        v = self.raw(k, default)
        if v is None:
            return None
        if not isinstance(v, str):
            self.errs.append(f"{self._p(k)}: expected a string, got {v!r}")
            return default
        if choices and v not in choices:
            self.errs.append(f"{self._p(k)}: {v!r} not one of {list(choices)}")
            return default
        if pattern and not pattern.match(v):
            self.errs.append(f"{self._p(k)}: {v!r} is not valid")
            return default
        return v

    def b(self, k: str, default: bool) -> bool:
        v = self.raw(k, default)
        if not isinstance(v, bool):
            self.errs.append(f"{self._p(k)}: expected true/false, got {v!r}")
            return default
        return v

    def pair(self, k: str, default: tuple, lo: float | None = None, hi: float | None = None):
        v = self.raw(k, None)
        if v is None:
            return default
        if (
            not isinstance(v, (list, tuple))
            or len(v) != 2
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v)
        ):
            self.errs.append(f"{self._p(k)}: expected [a, b] numbers, got {v!r}")
            return default
        if any((lo is not None and x < lo) or (hi is not None and x > hi) for x in v):
            self.errs.append(f"{self._p(k)}: values out of range [{lo}, {hi}]")
            return default
        return (float(v[0]), float(v[1]))

    def sub(self, k: str) -> "_R":
        return _R(self.raw(k, {}), self._p(k), self.errs)

    def finish(self) -> None:
        for k in self.d:
            if k in self.used:
                continue
            if k in FORBIDDEN_KEYS:
                self.errs.append(
                    f"{self._p(k)}: free text is not allowed in a reel; reference a copy line by ID (copy.json) instead"
                )
            else:
                self.errs.append(f"{self._p(k)}: unknown key")


def scan_forbidden(obj: Any, path: str = "") -> list[str]:
    """Reject literal-text keys anywhere in the raw JSON (the one exception is the colour
    ``brand.text``, which must be a #rrggbb string)."""
    errs: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if k in FORBIDDEN_KEYS:
                if p == "brand.text" and isinstance(v, str) and HEX_RE.match(v):
                    continue
                if GRID_LABEL_PATH_RE.match(p) and isinstance(v, str) and ID_RE.match(v):
                    continue  # a grid cell's label is a copy id
                if GRID_LABEL_PATH_RE.match(p) and isinstance(v, dict) and set(v) == {"index"} and v["index"] is True:
                    continue  # a grid cell's label is its number
                errs.append(f"{p}: free text is not allowed in a reel; reference a copy line by ID (copy.json) instead")
            errs += scan_forbidden(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            errs += scan_forbidden(v, f"{path}[{i}]")
    return errs


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #
def _parse_grid_cell(r: _R) -> GridCell:
    """One grid cell: a ``video`` path (image cells are not supported), ``fit``, an optional ``label`` copy id."""
    if not r.has("video"):
        r.errs.append(
            f"{r.path}: needs a video (a path); a cell with neither video nor image is not allowed "
            f"(image cells are not supported)"
        )
    raw_label = r.raw("label")
    label, label_index = None, False
    if isinstance(raw_label, dict):
        label_index = set(raw_label) == {"index"} and raw_label["index"] is True
        if not label_index:
            r.errs.append(
                f'{r._p("label")}: expected a copy id, or {{"index": true}} for the cell number, got {raw_label!r}'
            )
    elif raw_label is not None:
        label = r.s("label", None, pattern=ID_RE)
    c = GridCell(
        video=r.s("video", None),
        fit=r.s("fit", "contain", ("cover", "contain")) or "contain",
        label=label,
        label_index=label_index,
        label_style=r.s("label_style", "bold-pop", pattern=ID_RE) or "bold-pop",
    )
    r.finish()
    return c


def _parse_grid(r: _R, shot_id: str) -> Grid:
    """``source.grid``: cols/rows (required), gap, reveal, stagger, ease and row-major ``cells``."""
    dims_before = len(r.errs)
    for k in ("cols", "rows"):
        if not r.has(k):
            r.errs.append(f"{r._p(k)}: required")
    cols, rows = r.num("cols", 2, 1, 8, integer=True), r.num("rows", 2, 1, 8, integer=True)
    dims_ok = len(r.errs) == dims_before  # a bad cols/rows is reported once, not again as a count error
    g = Grid(
        cols=cols,
        rows=rows,
        gap=r.num("gap", 0.02, 0.0, 0.2),
        reveal=r.s("reveal", "none", GRID_REVEALS) or "none",
        ease=r.s("ease", "out", EASES) or "out",
    )
    if r.has("stagger"):
        st = r.raw("stagger")
        if isinstance(st, bool) or not (
            (isinstance(st, (int, float)) and 0.0 <= st <= 10.0)
            or (isinstance(st, str) and BEATS_RE.match(st) and float(st[6:]) > 0)
        ):
            r.errs.append(f'{r._p("stagger")}: expected seconds (0..10) or "beats:N" (N > 0), got {st!r}')
        else:
            g.stagger = float(st) if isinstance(st, (int, float)) else st
    raw = r.raw("cells")
    if not isinstance(raw, list):
        r.errs.append(f"{r._p('cells')}: required, a list of {{video, fit, label, label_style}}, row-major")
        raw = []
    elif dims_ok and len(raw) != g.cols * g.rows:
        r.errs.append(
            f"{r.path}.cells: shot {shot_id!r}: a {g.cols} x {g.rows} grid needs exactly "
            f"{g.cols * g.rows} cells (cols * rows), got {len(raw)}"
        )
    g.cells = tuple(_parse_grid_cell(_R(cd, f"{r.path}.cells[{j}]", r.errs)) for j, cd in enumerate(raw))
    r.finish()
    return g


def _parse_pad(r: _R, src: Source) -> None:
    pad = r.raw("pad", "#000000")
    if not (isinstance(pad, str) and (pad == "blur" or HEX_RE.match(pad))):
        r.errs.append(f'{r.path}.pad: expected "#rrggbb" or "blur", got {pad!r}')
    else:
        src.pad = pad


def _parse_source(r: _R, shot_id: str = "") -> Source:
    kinds = [k for k in ("image", "video", "color", "funk2", "grid") if r.has(k)]
    if len(kinds) != 1:
        r.errs.append(f"{r.path}: needs exactly one of image, video, color, funk2, grid (got {kinds})")
        r.finish()
        return Source()
    kind = kinds[0]
    if kind == "grid":  # pad (the gap and contain colour) is the only other key
        src = Source("grid", "", grid=_parse_grid(r.sub("grid"), shot_id))
        _parse_pad(r, src)
        r.finish()
        return src
    val = r.s(kind, "", required=True, pattern=HEX_RE if kind == "color" else None) or ""
    trim = r.pair("trim", None, lo=0.0)
    if trim is not None and trim[1] <= trim[0]:
        r.errs.append(f"{r.path}.trim: end must be after start")
    src = Source(kind, val, trim, r.b("loop", False), r.s("fit", "cover", ("cover", "contain")) or "cover")
    if r.has("focus"):
        src.focus = r.pair("focus", None, lo=0.0, hi=1.0)
    _parse_pad(r, src)
    if r.has("speed"):
        sp = r.raw("speed")
        if isinstance(sp, (int, float)) and not isinstance(sp, bool):
            sp = [sp, sp]
        if (
            isinstance(sp, list)
            and len(sp) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0.1 <= x <= 8.0 for x in sp)
        ):
            src.speed = (float(sp[0]), float(sp[1]))
        else:
            r.errs.append(f"{r.path}.speed: expected a number or [start, end] between 0.1 and 8 (a ramp), got {sp!r}")
        if kind != "video":
            r.errs.append(f"{r.path}.speed: only a video source has a speed")
    r.finish()
    return src


def _parse_motion(r: _R) -> Motion:
    m = Motion(type=r.s("type", "none", MOTION_TYPES) or "none")
    m.zoom = r.pair("zoom", (1.0, 1.0), lo=1.0, hi=3.0)
    pan = r.raw("pan", None)
    if pan is not None:
        ok = (
            isinstance(pan, list)
            and len(pan) == 2
            and all(
                isinstance(p, list)
                and len(p) == 2
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) and -0.5 <= x <= 0.5 for x in p)
                for p in pan
            )
        )
        if ok:
            m.pan = ((float(pan[0][0]), float(pan[0][1])), (float(pan[1][0]), float(pan[1][1])))
        else:
            r.errs.append(f"{r.path}.pan: expected [[x0,y0],[x1,y1]] with values in [-0.5, 0.5]")
    m.push = r.num("push", 0.0, 0.0, 0.5)
    m.orbit_deg = r.num("orbit_deg", 0.0, -30.0, 30.0)
    m.dof = r.num("dof", 0.0, 0.0, 1.0)
    has_keys = any(r.has(k) for k in ("from", "to", "keys"))
    if m.type == "frame":
        m.keys = _parse_frame_keys(r)
    elif has_keys:
        r.errs.append(f'{r.path}: from/to/keys belong to motion type "frame" (got type {m.type!r})')
        for k in ("from", "to", "keys", "at", "ease"):
            r.raw(k)
    r.finish()
    return m


def _rect(v, where: str, errs: list[str]) -> tuple[float, float, float, float] | None:
    ok = isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
    if ok:
        x, y, w, h = (float(t) for t in v)
        ok = 0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001
    if not ok:
        errs.append(f"{where}: expected [x, y, w, h] as fractions of the source (inside 0..1), got {v!r}")
        return None
    return (float(v[0]), float(v[1]), float(v[2]), float(v[3]))


def _parse_frame_keys(r: _R) -> tuple[FrameKey, ...]:
    """``from``/``to`` (+ ``at`` = [t0, t1] fractions of the shot, ``ease``) or ``keys``: [{rect, t|s, ease}]."""
    FULL = (0.0, 0.0, 1.0, 1.0)
    if r.has("keys"):
        if any(r.has(k) for k in ("from", "to", "at")):
            r.errs.append(f"{r.path}: use either keys or from/to/at, not both")
        ease_default = r.s("ease", "smooth", EASES) or "smooth"
        raw = r.raw("keys")
        if not isinstance(raw, list) or not raw:
            r.errs.append(f"{r.path}.keys: expected a non-empty list of {{rect, t | s, ease}}")
            return ()
        out: list[FrameKey] = []
        for i, kd in enumerate(raw):
            k = _R(kd, f"{r.path}.keys[{i}]", r.errs)
            rect = _rect(k.raw("rect"), f"{k.path}.rect", r.errs) if k.has("rect") else None
            if rect is None and not k.has("rect"):
                r.errs.append(f"{k.path}.rect: required")
            has_t, has_s = k.has("t"), k.has("s")
            if has_t == has_s:
                r.errs.append(f"{k.path}: give exactly one of t (fraction of the shot) or s (seconds)")
            t = k.num("t", 0.0, 0.0, 1.0) if has_t else None
            sec = k.num("s", 0.0, 0.0, 30.0) if has_s else None
            ease = k.s("ease", ease_default, EASES) or ease_default
            k.finish()
            if rect is not None:
                out.append(FrameKey(rect, t, sec, ease))
        ts = [k.t for k in out if k.t is not None]
        ss = [k.s for k in out if k.s is not None]
        if ts and ss:
            r.errs.append(f"{r.path}.keys: use t on every key or s on every key, not a mix")
        elif any(b < a for a, b in zip((ts or ss), (ts or ss)[1:])):
            r.errs.append(f"{r.path}.keys: times must not decrease")
        return tuple(out)
    if not r.has("to"):
        r.errs.append(
            f'{r.path}: motion type "frame" needs `to` (or `keys`): the source rectangle [x, y, w, h] to end on'
        )
    frm = _rect(r.raw("from"), f"{r.path}.from", r.errs) if r.has("from") else FULL
    to = _rect(r.raw("to"), f"{r.path}.to", r.errs) if r.has("to") else None
    at = r.pair("at", (0.0, 1.0), lo=0.0, hi=1.0)
    if at[1] < at[0]:
        r.errs.append(f"{r.path}.at: end must not be before start")
    ease = r.s("ease", "smooth", EASES) or "smooth"
    if frm is None or to is None:
        return ()
    return (FrameKey(frm, at[0], None, "smooth"), FrameKey(to, at[1], None, ease))


def _parse_product(r: _R) -> ProductSpec:
    p = ProductSpec(
        image=r.s("image", "", required=True) or "",
        x=r.num("x", 0.5, 0.0, 1.0),
        y=r.num("y", 0.62, 0.0, 1.0),
        height=r.num("height", 0.4, 0.02, 1.0),
        depth=r.num("depth", 0.8, 0.0, 1.0),
        shadow=r.num("shadow", 0.6, 0.0, 1.0),
        match_color=r.num("match_color", 0.0, 0.0, 1.0),
    )
    r.finish()
    return p


def parse_highlight(v: Any, where: str) -> tuple[str, float] | None:
    """A word highlight: {"color": primary|secondary|text|#rrggbb, "radius": 0..0.5} -> (color, radius). None = off."""
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ValueError(f'{where}: must be an object like {{"color": "primary", "radius": 0.3}}')
    bad = sorted(set(v) - {"color", "radius"})
    if bad:
        raise ValueError(f"{where}: unknown keys {bad}")
    color = v.get("color", "secondary")  # dark on the default red box; primary would vanish into it
    if color not in ("primary", "secondary", "text") and not HEX_RE.match(str(color)):
        raise ValueError(f"{where}.color={color!r} must be primary|secondary|text or #rrggbb")
    radius = v.get("radius", 0.3)
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 0 <= radius <= 0.5:
        raise ValueError(f"{where}.radius={radius!r} must be a number from 0 to 0.5")
    return (str(color), float(radius))


def _parse_caption(r: _R) -> CaptionSpec:
    c = CaptionSpec(
        copy_id=r.s("copy_id", "", required=True) or "",
        style=r.s("style", "red-box", pattern=ID_RE) or "red-box",
        reveal=r.s("reveal", "none", REVEALS) or "none",
        anchor=r.s("anchor", "bottom", ANCHORS) or "bottom",
    )
    if r.raw("highlight", None) is not None:
        try:
            c.highlight = parse_highlight(r.raw("highlight"), f"{r.path}.highlight")
        except ValueError as e:
            r.errs.append(str(e))
    r.finish()
    return c


def _parse_shot(r: _R, idx: int) -> Shot:
    sh = Shot(id=r.s("id", f"s{idx + 1}", pattern=ID_RE) or f"s{idx + 1}")
    sh.source = _parse_source(r.sub("source"), sh.id)
    sh.motion = _parse_motion(r.sub("motion"))
    if r.has("product"):
        sh.product = _parse_product(r.sub("product"))
    if r.has("caption"):
        sh.caption = _parse_caption(r.sub("caption"))
    vo = r.raw("vo", None)
    if vo is not None and not isinstance(vo, str):
        r.errs.append(f"{r.path}.vo: expected a copy id string")
    else:
        sh.vo = vo
    dur = r.raw("duration", "auto")
    if isinstance(dur, bool) or not (
        (isinstance(dur, (int, float)) and 0.2 <= dur <= 30)
        or (isinstance(dur, str) and (dur == "auto" or (BEATS_RE.match(dur) and float(dur[6:]) > 0)))
    ):
        r.errs.append(
            f'{r.path}.duration: expected seconds (0.2..30), "auto" or "beats:N" (N > 0, halves ok), got {dur!r}'
        )
    else:
        sh.duration = float(dur) if isinstance(dur, (int, float)) else dur
    sh.transition_in = r.s("transition_in", "cut", TRANSITIONS) or "cut"
    if r.has("formats"):
        _check_shot_formats(r.raw("formats"), f"{r.path}.formats", r.errs)
    if sh.source.kind != "image" and sh.motion.type == "parallax":
        r.errs.append(f"{r.path}.motion: parallax needs a still image (use kenburns or frame on {sh.source.kind})")
    if sh.source.kind in ("color", "funk2") and sh.motion.type in ("kenburns", "frame"):
        r.errs.append(f"{r.path}.motion: {sh.motion.type} needs an image or video source")
    r.finish()
    return sh


def _check_shot_formats(v: Any, where: str, errs: list[str]) -> None:
    if not isinstance(v, dict):
        errs.append(f"{where}: expected {{format_id: {{motion | caption | product | source overrides}}}}")
        return
    for fid, ov in v.items():
        if not isinstance(ov, dict):
            errs.append(f"{where}.{fid}: expected an object")
            continue
        for k, sub in ov.items():
            if k not in SHOT_FORMAT_KEYS:
                errs.append(f"{where}.{fid}.{k}: a format can override only {sorted(SHOT_FORMAT_KEYS)}")
                continue
            allowed = SHOT_FORMAT_KEYS[k]
            if not isinstance(sub, dict):
                errs.append(f"{where}.{fid}.{k}: expected an object")
            elif allowed is not None:
                bad = sorted(set(sub) - set(allowed))
                if bad:
                    errs.append(f"{where}.{fid}.{k}: only {list(allowed)} can change per format (got {bad})")


def _parse_end_card(r: _R) -> EndCard:
    e = EndCard(
        template=r.s("template", "brand-split", pattern=ID_RE) or "brand-split",
        duration=r.num("duration", 5.0, 0.5, 30.0),
        audio=r.s("audio", "silence", END_AUDIO) or "silence",
        product=r.s("product", None),
        headline_reveal=r.s("headline_reveal", "typewriter", REVEALS) or "typewriter",
    )
    cp = r.raw("copy", {})
    if not isinstance(cp, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in cp.items()):
        r.errs.append(f"{r.path}.copy: expected {{slot: copy_id}}")
    else:
        e.copy = dict(cp)
    r.finish()
    return e


def _resolve_brand_from(frm: str, base_dir: str | None, warnings: list[str]) -> dict | None:
    """``brand.from``: resolve the palette from an image (or one frame of an mp4) with the helpers in
    reel.brand, resolved like every other reel asset path. Returns None — and appends a warning naming
    the file — when the file is missing or has no usable colours, so the explicit colours stay as fallback."""
    from funkbox.config import resolve_path

    from . import brand as brand_mod

    try:
        p = resolve_path(frm, base_dir)
    except (ValueError, FileNotFoundError) as e:
        warnings.append(f"brand.from {frm!r}: {e}; keeping the explicit brand colours")
        return None
    chosen = brand_mod.brand_from_file(p)
    if chosen is None:
        warnings.append(f"brand.from {frm!r}: no usable colours in that file; keeping the explicit brand colours")
        return None
    return chosen


#: Type presets: templates/types/<name>.json. A preset sets the fields below only where the reel leaves them unset.
TYPES_DIR = Path(__file__).resolve().parents[2] / "templates" / "types"
TYPE_BRAND_KEYS = ("font_caption", "font_display", "font_fallback")
TYPE_AUDIO_KEYS = ("music_gain_db", "duck_db", "snap_cuts_to_beats")
TYPE_TOP_KEYS = ("hook", "shot_target_s")  # reel-level fields a preset may set
TYPE_KEYS = (*TYPE_BRAND_KEYS, *TYPE_AUDIO_KEYS, *TYPE_TOP_KEYS, "caption_style")
TYPE_META_KEYS = ("description", "hebrew_test")  # documentation only; never applied to a reel


def list_types() -> list[str]:
    return sorted(p.stem for p in TYPES_DIR.glob("*.json"))


def load_type(name: str, errs: list[str]) -> dict | None:
    """The preset ``name``, or None with the problem appended to ``errs``."""
    have = list_types()
    if name not in have:
        errs.append(f"type: no such type {name!r}; have {have}")
        return None
    try:
        preset = json.loads((TYPES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errs.append(f"templates/types/{name}.json: {e}")
        return None
    if not isinstance(preset, dict):
        errs.append(f"templates/types/{name}.json: must be a JSON object")
        return None
    bad = sorted(set(preset) - set(TYPE_KEYS) - set(TYPE_META_KEYS))
    if bad:
        errs.append(f"templates/types/{name}.json: unknown key(s) {bad}; a preset may set {list(TYPE_KEYS)}")
        return None
    return preset


def _fill(out: dict, section: str, key: str, value: Any) -> None:
    sec = out.setdefault(section, {})
    if isinstance(sec, dict) and sec.get(key) is None:  # an explicit reel field always wins; null means unset
        sec[key] = value


def apply_type(d: Any, errs: list[str]) -> Any:
    """The reel with its ``type`` preset filled in. A reel without a type comes back as the same object, so it is
    unchanged; otherwise the input is copied, never modified."""
    if not isinstance(d, dict) or not isinstance(d.get("type"), str):
        return d
    preset = load_type(d["type"], errs)
    if preset is None:
        return d
    out = copy.deepcopy(d)
    for k in TYPE_BRAND_KEYS:
        if k in preset:
            _fill(out, "brand", k, preset[k])
    for k in TYPE_AUDIO_KEYS:
        if k in preset:
            _fill(out, "audio", k, preset[k])
    for k in TYPE_TOP_KEYS:
        if k in preset and out.get(k) is None:
            out[k] = preset[k]
    shots = out.get("shots")
    if "caption_style" in preset and isinstance(shots, list):
        for sh in shots:
            if isinstance(sh, dict) and isinstance(sh.get("caption"), dict):
                if sh["caption"].get("style") is None:
                    sh["caption"]["style"] = preset["caption_style"]
    return out


def parse_reel(d: Any, base_dir: str | None = None) -> Reel:
    """Parse a reel dict. Raises ReelError listing every problem."""
    errs: list[str] = []
    d = apply_type(d, errs)
    errs += scan_forbidden(d)
    r = _R(d, "", errs)
    reel = Reel(base_dir=base_dir)
    reel.preset = r.s("type", None, pattern=ID_RE)
    reel.hook = r.b("hook", False) if r.raw("hook", None) is not None else False
    if r.raw("shot_target_s", None) is not None:
        reel.shot_target_s = r.num("shot_target_s", 3.0, 0.5, 10.0)
    reel.version = int(r.num("version", 3, 3, 3, integer=True))
    s = r.sub("settings")
    reel.settings = Settings(
        width=int(s.num("width", 1080, 64, 4320, integer=True)),
        height=int(s.num("height", 1920, 64, 7680, integer=True)),
        fps=int(s.num("fps", 30, 1, 60, integer=True)),
        safe_area=s.s("safe_area", "reels", SAFE_AREAS) or "reels",
    )
    s.finish()
    b = r.sub("brand")
    brand_from = b.s("from", None)
    reel.brand = Brand(
        primary=b.s("primary", "#c8102e", pattern=HEX_RE) or "#c8102e",
        secondary=b.s("secondary", "#1a1a1a", pattern=HEX_RE) or "#1a1a1a",
        text=b.s("text", "#ffffff", pattern=HEX_RE) or "#ffffff",
        font_caption=b.s("font_caption", "fonts/Oswald-SemiBold.ttf") or "",
        font_display=b.s("font_display", "fonts/DMSerifDisplay-Regular.ttf") or "",
        logo=b.s("logo", None),
        font_fallback=b.s("font_fallback", "fonts/Rubik-Bold.ttf"),
    )
    if brand_from:
        reel.brand.brand_from = brand_from
        chosen = _resolve_brand_from(brand_from, base_dir, reel.warnings)
        if chosen:
            reel.brand.primary = chosen["primary"]
            reel.brand.secondary = chosen["secondary"]
            reel.brand.text = chosen["text"]
    b.finish()
    reel.copy = r.s("copy", "", required=True) or ""
    a = r.sub("audio")
    reel.audio = Audio(
        music=a.s("music", None),
        music_gain_db=a.num("music_gain_db", -14.0, -60.0, 12.0),
        voiceover=a.b("voiceover", False),
        duck_db=a.num("duck_db", -10.0, -40.0, 0.0),
        snap_cuts_to_beats=a.b("snap_cuts_to_beats", False),
        loudness_lufs=a.num("loudness_lufs", -14.0, -40.0, -5.0),
    )
    reel.audio.voice = a.s("voice", None, pattern=re.compile(r"^(piper|kokoro:[a-z]{2}_[a-z]+)$"))
    reel.audio.voice_speed = a.num("voice_speed", 1.0, 0.7, 1.4)
    if a.has("bpm"):
        reel.audio.bpm = a.num("bpm", 120.0, 30.0, 300.0)
    reel.audio.beat_offset = a.num("beat_offset", 0.0, 0.0, 10.0)
    if a.has("beat_offset") and not a.has("bpm"):
        errs.append("audio.beat_offset: needs audio.bpm (the offset of that fixed grid)")
    a.finish()
    shots = r.raw("shots", None)
    if not isinstance(shots, list) or not shots:
        errs.append("shots: expected a non-empty list")
        shots = []
    seen: set[str] = set()
    for i, sd in enumerate(shots):
        sh = _parse_shot(_R(sd, f"shots[{i}]", errs), i)
        if sh.id in seen:
            errs.append(f"shots[{i}].id: duplicate shot id {sh.id!r}")
        seen.add(sh.id)
        reel.shots.append(sh)
    if r.has("end_card") and r.raw("end_card") is not None:
        reel.end_card = _parse_end_card(r.sub("end_card"))
    if r.has("formats"):
        reel.formats = parse_formats(r.raw("formats"), errs)
    known = {f.id for f in reel.formats}
    for i, sd in enumerate(shots):
        for fid in (sd.get("formats") or {}) if isinstance(sd, dict) and isinstance(sd.get("formats"), dict) else {}:
            if fid not in known:
                errs.append(f"shots[{i}].formats.{fid}: no such format (reel formats: {sorted(known) or 'none'})")
    r.finish()
    if errs:
        raise ReelError(sorted(set(errs), key=errs.index))
    return reel


# --------------------------------------------------------------------------- #
# Formats: one edit, several output sizes
# --------------------------------------------------------------------------- #
def parse_formats(v: Any, errs: list[str]) -> list[Format]:
    """``formats``: a list of preset ids ("9x16", "16x9", "1x1", "4x5") and/or {id, width, height, safe_area}."""
    out: list[Format] = []
    if not isinstance(v, list) or not v:
        errs.append(f"formats: expected a non-empty list of presets {list(FORMAT_PRESETS)} or {{id, width, height}}")
        return out
    for i, f in enumerate(v):
        if isinstance(f, str):
            if f not in FORMAT_PRESETS:
                errs.append(
                    f"formats[{i}]: unknown preset {f!r}; have {list(FORMAT_PRESETS)} (or give {{id, width, height}})"
                )
                continue
            w, h, sa = FORMAT_PRESETS[f]
            out.append(Format(f, w, h, sa))
            continue
        r = _R(f, f"formats[{i}]", errs)
        fid = r.s("id", "", required=True, pattern=ID_RE) or ""
        pw, ph, psa = FORMAT_PRESETS.get(fid, (None, None, "none"))
        w = int(r.num("width", pw or 1080, 64, 4320, integer=True)) if (r.has("width") or pw is None) else pw
        h = int(r.num("height", ph or 1920, 64, 7680, integer=True)) if (r.has("height") or ph is None) else ph
        sa = r.s("safe_area", psa, SAFE_AREAS) or psa
        r.finish()
        if fid:
            out.append(Format(fid, w, h, sa))
    ids = [f.id for f in out]
    if len(ids) != len(set(ids)):
        errs.append(f"formats: duplicate ids in {ids}")
    return out


def format_ids(raw: Any) -> list[str]:
    """Format ids declared by a raw reel dict (no validation; [] when none)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("formats"), list):
        return []
    out = []
    for f in raw["formats"]:
        if isinstance(f, str):
            out.append(f)
        elif isinstance(f, dict) and isinstance(f.get("id"), str):
            out.append(f["id"])
    return out


def materialize(raw: dict, fmt: str) -> dict:
    """The plain reel dict of one format: its size and safe area in ``settings``, every shot's overrides for
    that format applied (``motion`` replaced whole; ``caption`` / ``product`` / ``source`` merged), and no
    ``formats`` keys left. Raises ReelError for an unknown format id."""
    import copy

    errs: list[str] = []
    fmts = {f.id: f for f in parse_formats(raw.get("formats"), errs)} if "formats" in raw else {}
    if fmt not in fmts:
        raise ReelError(errs or [f"format {fmt!r}: not declared in the reel's formats (have {sorted(fmts) or 'none'})"])
    f = fmts[fmt]
    d = copy.deepcopy(raw)
    d.pop("formats", None)
    st = d.setdefault("settings", {})
    if isinstance(st, dict):
        st["width"], st["height"], st["safe_area"] = f.width, f.height, f.safe_area
    for sh in d.get("shots") or []:
        if not isinstance(sh, dict):
            continue
        fo = sh.pop("formats", None)
        ov = fo.get(fmt) if isinstance(fo, dict) else None
        for k, v in (ov or {}).items():
            if k == "motion":
                sh["motion"] = copy.deepcopy(v)
            elif isinstance(sh.get(k), dict) and isinstance(v, dict):
                sh[k].update(copy.deepcopy(v))
    return d

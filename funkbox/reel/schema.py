"""Reel dataclasses + strict JSON parsing/validation.

The schema has NO free-text fields. Text reaches pixels only through the copy deck
(``reel.copydeck``) by ID. Any unknown key is an error, and keys that look like free text
(``text``, ``label``, ``caption_text`` ...) get a dedicated message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SAFE_AREAS = ("reels", "landscape", "none")
MOTION_TYPES = ("none", "kenburns", "parallax", "frame")
EASES = ("smooth", "linear", "in", "out", "hold")
REVEALS = ("none", "typewriter", "words", "pop")
ANCHORS = ("top", "center", "bottom")
TRANSITIONS = ("cut", "flash", "push", "crossfade", "zoom-punch")
END_AUDIO = ("silence", "keep")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
BEATS_RE = re.compile(r"^beats:([0-9]{1,3}(?:\.[0-9]{1,3})?)$")      # beats:4, beats:1.5, beats:0.5

#: format presets: id -> (width, height, safe_area)
FORMAT_PRESETS = {"9x16": (1080, 1920, "reels"), "16x9": (1920, 1080, "landscape"),
                  "1x1": (1080, 1080, "landscape"), "4x5": (1080, 1350, "landscape")}
#: what a shot may override per format (motion is replaced whole; the others are merged key by key)
SHOT_FORMAT_KEYS = {"motion": None, "caption": ("anchor",), "product": ("x", "y", "height"),
                    "source": ("focus", "fit", "pad")}

# keys that would smuggle literal copy into a reel; always rejected (except brand.text colour)
FORBIDDEN_KEYS = ("text", "label", "caption_text", "title", "subtitle", "headline_text",
                  "string", "content", "copy_text", "message", "say", "body", "name_text")


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
    text: str = "#ffffff"           # a COLOUR (the one allowed `text` key); see validate_forbidden
    font_caption: str = "fonts/Oswald-SemiBold.ttf"
    font_display: str = "fonts/DMSerifDisplay-Regular.ttf"
    logo: str | None = None


@dataclass
class Audio:
    music: str | None = None
    bpm: float | None = None                # fixed beat grid: beats:N and snapping need no music detection
    beat_offset: float = 0.0                # seconds of the first beat on that grid
    music_gain_db: float = -14.0
    voiceover: bool = False
    duck_db: float = -10.0
    snap_cuts_to_beats: bool = False
    loudness_lufs: float = -14.0


@dataclass
class Source:
    kind: str = "color"             # image | video | color | funk2
    value: str = "#000000"          # path or hex colour
    trim: tuple[float, float] | None = None
    loop: bool = False
    fit: str = "cover"
    focus: tuple[float, float] | None = None        # cover-crop centre, fractions of the source
    pad: str = "#000000"                            # fit=contain / framing past the edge: #hex or "blur"
    speed: tuple[float, float] = (1.0, 1.0)         # video playback speed at shot start / end (ramp)


@dataclass
class Motion:
    type: str = "none"
    zoom: tuple[float, float] = (1.0, 1.0)
    pan: tuple[tuple[float, float], tuple[float, float]] = ((0.0, 0.0), (0.0, 0.0))
    push: float = 0.0
    orbit_deg: float = 0.0
    dof: float = 0.0
    keys: tuple["FrameKey", ...] = ()        # type "frame": source rectangles over the shot


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


@dataclass
class Shot:
    id: str = ""
    source: Source = field(default_factory=Source)
    motion: Motion = field(default_factory=Motion)
    product: ProductSpec | None = None
    caption: CaptionSpec | None = None
    vo: str | None = None                   # copy id spoken during the shot
    duration: float | str = "auto"          # seconds | "auto" | "beats:N"
    transition_in: str = "cut"


@dataclass
class EndCard:
    template: str = "brand-split"
    duration: float = 5.0
    audio: str = "silence"
    copy: dict[str, str] = field(default_factory=dict)   # slot -> copy id
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
    base_dir: str | None = None             # folder of the reel file; paths resolve against it
    formats: list[Format] = field(default_factory=list)     # extra output formats (see materialize)
    format_id: str | None = None            # set when this Reel is one format's variant

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

    def num(self, k: str, default: float, lo: float | None = None, hi: float | None = None,
            integer: bool = False) -> float:
        v = self.raw(k, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (integer and int(v) != v):
            self.errs.append(f"{self._p(k)}: expected {'an integer' if integer else 'a number'}, got {v!r}")
            return default
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            self.errs.append(f"{self._p(k)}: {v} out of range [{lo}, {hi}]")
            return default
        return int(v) if integer else float(v)

    def s(self, k: str, default: str | None, choices: tuple[str, ...] | None = None,
          required: bool = False, pattern: re.Pattern | None = None) -> str | None:
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
        if (not isinstance(v, (list, tuple)) or len(v) != 2
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v)):
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
                self.errs.append(f"{self._p(k)}: free text is not allowed in a reel; reference a copy "
                                 f"line by ID (copy.json) instead")
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
                errs.append(f"{p}: free text is not allowed in a reel; reference a copy line by ID "
                            f"(copy.json) instead")
            errs += scan_forbidden(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            errs += scan_forbidden(v, f"{path}[{i}]")
    return errs


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #
def _parse_source(r: _R) -> Source:
    kinds = [k for k in ("image", "video", "color", "funk2") if r.has(k)]
    if len(kinds) != 1:
        r.errs.append(f"{r.path}: needs exactly one of image, video, color, funk2 (got {kinds})")
        r.finish()
        return Source()
    kind = kinds[0]
    val = r.s(kind, "", required=True, pattern=HEX_RE if kind == "color" else None) or ""
    trim = r.pair("trim", None, lo=0.0)
    if trim is not None and trim[1] <= trim[0]:
        r.errs.append(f"{r.path}.trim: end must be after start")
    src = Source(kind, val, trim, r.b("loop", False), r.s("fit", "cover", ("cover", "contain")) or "cover")
    if r.has("focus"):
        src.focus = r.pair("focus", None, lo=0.0, hi=1.0)
    pad = r.raw("pad", "#000000")
    if not (isinstance(pad, str) and (pad == "blur" or HEX_RE.match(pad))):
        r.errs.append(f"{r.path}.pad: expected \"#rrggbb\" or \"blur\", got {pad!r}")
    else:
        src.pad = pad
    if r.has("speed"):
        sp = r.raw("speed")
        if isinstance(sp, (int, float)) and not isinstance(sp, bool):
            sp = [sp, sp]
        if (isinstance(sp, list) and len(sp) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                           and 0.1 <= x <= 8.0 for x in sp)):
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
        ok = (isinstance(pan, list) and len(pan) == 2 and all(
            isinstance(p, list) and len(p) == 2 and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) and -0.5 <= x <= 0.5 for x in p)
            for p in pan))
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
        r.errs.append(f"{r.path}: from/to/keys belong to motion type \"frame\" (got type {m.type!r})")
        for k in ("from", "to", "keys", "at", "ease"):
            r.raw(k)
    r.finish()
    return m


def _rect(v, where: str, errs: list[str]) -> tuple[float, float, float, float] | None:
    ok = (isinstance(v, list) and len(v) == 4
          and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v))
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
        r.errs.append(f"{r.path}: motion type \"frame\" needs `to` (or `keys`): the source rectangle [x, y, w, h] to end on")
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
    p = ProductSpec(image=r.s("image", "", required=True) or "",
                    x=r.num("x", 0.5, 0.0, 1.0), y=r.num("y", 0.62, 0.0, 1.0),
                    height=r.num("height", 0.4, 0.02, 1.0), depth=r.num("depth", 0.8, 0.0, 1.0),
                    shadow=r.num("shadow", 0.6, 0.0, 1.0), match_color=r.num("match_color", 0.0, 0.0, 1.0))
    r.finish()
    return p


def _parse_caption(r: _R) -> CaptionSpec:
    c = CaptionSpec(copy_id=r.s("copy_id", "", required=True) or "",
                    style=r.s("style", "red-box", pattern=ID_RE) or "red-box",
                    reveal=r.s("reveal", "none", REVEALS) or "none",
                    anchor=r.s("anchor", "bottom", ANCHORS) or "bottom")
    r.finish()
    return c


def _parse_shot(r: _R, idx: int) -> Shot:
    sh = Shot(id=r.s("id", f"s{idx + 1}", pattern=ID_RE) or f"s{idx + 1}")
    sh.source = _parse_source(r.sub("source"))
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
            or (isinstance(dur, str) and (dur == "auto" or (BEATS_RE.match(dur) and float(dur[6:]) > 0)))):
        r.errs.append(f"{r.path}.duration: expected seconds (0.2..30), \"auto\" or \"beats:N\" (N > 0, halves ok), "
                      f"got {dur!r}")
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
    e = EndCard(template=r.s("template", "brand-split", pattern=ID_RE) or "brand-split",
                duration=r.num("duration", 5.0, 0.5, 30.0),
                audio=r.s("audio", "silence", END_AUDIO) or "silence",
                product=r.s("product", None), headline_reveal=r.s("headline_reveal", "typewriter", REVEALS) or "typewriter")
    cp = r.raw("copy", {})
    if not isinstance(cp, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in cp.items()):
        r.errs.append(f"{r.path}.copy: expected {{slot: copy_id}}")
    else:
        e.copy = dict(cp)
    r.finish()
    return e


def parse_reel(d: Any, base_dir: str | None = None) -> Reel:
    """Parse a reel dict. Raises ReelError listing every problem."""
    errs: list[str] = scan_forbidden(d)
    r = _R(d, "", errs)
    reel = Reel(base_dir=base_dir)
    reel.version = int(r.num("version", 3, 3, 3, integer=True))
    s = r.sub("settings")
    reel.settings = Settings(width=int(s.num("width", 1080, 64, 4320, integer=True)),
                             height=int(s.num("height", 1920, 64, 7680, integer=True)),
                             fps=int(s.num("fps", 30, 1, 60, integer=True)),
                             safe_area=s.s("safe_area", "reels", SAFE_AREAS) or "reels")
    s.finish()
    b = r.sub("brand")
    reel.brand = Brand(primary=b.s("primary", "#c8102e", pattern=HEX_RE) or "#c8102e",
                       secondary=b.s("secondary", "#1a1a1a", pattern=HEX_RE) or "#1a1a1a",
                       text=b.s("text", "#ffffff", pattern=HEX_RE) or "#ffffff",
                       font_caption=b.s("font_caption", "fonts/Oswald-SemiBold.ttf") or "",
                       font_display=b.s("font_display", "fonts/DMSerifDisplay-Regular.ttf") or "",
                       logo=b.s("logo", None))
    b.finish()
    reel.copy = r.s("copy", "", required=True) or ""
    a = r.sub("audio")
    reel.audio = Audio(music=a.s("music", None), music_gain_db=a.num("music_gain_db", -14.0, -60.0, 12.0),
                       voiceover=a.b("voiceover", False), duck_db=a.num("duck_db", -10.0, -40.0, 0.0),
                       snap_cuts_to_beats=a.b("snap_cuts_to_beats", False),
                       loudness_lufs=a.num("loudness_lufs", -14.0, -40.0, -5.0))
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
                errs.append(f"formats[{i}]: unknown preset {f!r}; have {list(FORMAT_PRESETS)} (or give {{id, width, height}})")
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

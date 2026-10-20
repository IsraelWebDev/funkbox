"""Reel dataclasses + strict JSON parsing/validation.

The schema has NO free-text fields. Text reaches pixels only through the copy deck
(``reel.copydeck``) by ID. Any unknown key is an error, and keys that look like free text
(``text``, ``label``, ``caption_text`` ...) get a dedicated message.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SAFE_AREAS = ("reels", "none")
MOTION_TYPES = ("none", "kenburns", "parallax")
REVEALS = ("none", "typewriter", "words", "pop")
ANCHORS = ("top", "center", "bottom")
TRANSITIONS = ("cut", "flash", "push", "crossfade", "zoom-punch")
END_AUDIO = ("silence", "keep")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
BEATS_RE = re.compile(r"^beats:([1-9][0-9]{0,2})$")

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


@dataclass
class Motion:
    type: str = "none"
    zoom: tuple[float, float] = (1.0, 1.0)
    pan: tuple[tuple[float, float], tuple[float, float]] = ((0.0, 0.0), (0.0, 0.0))
    push: float = 0.0
    orbit_deg: float = 0.0
    dof: float = 0.0


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
    r.finish()
    return m


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
            or (isinstance(dur, str) and (dur == "auto" or BEATS_RE.match(dur)))):
        r.errs.append(f"{r.path}.duration: expected seconds (0.2..30), \"auto\" or \"beats:N\", got {dur!r}")
    else:
        sh.duration = float(dur) if isinstance(dur, (int, float)) else dur
    sh.transition_in = r.s("transition_in", "cut", TRANSITIONS) or "cut"
    r.finish()
    return sh


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
    r.finish()
    if errs:
        raise ReelError(sorted(set(errs), key=errs.index))
    return reel

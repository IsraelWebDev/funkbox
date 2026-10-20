"""Recipe v2: dataclasses, JSON IO, path safety and the v1 sidecar adapter."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import funk  # v1 effects, until C2 retires the CPU engine

from funkbox.config import resolve_path

from .shader import ShaderSource, sample_params

BLEND_MODES = ["normal", "add", "screen", "multiply", "overlay", "difference", "softlight", "hue"]
LAYER_TYPES = ["shader", "image", "v1"]


# Inigo Quilez cosine palettes  col = a + b*cos(2pi*(c*x + d)); the recipe's palette name picks one.
# Time is added outside c as an integer number of cycles per loop, so loops stay exact.
PALETTES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "acid":   ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), (0.00, 0.33, 0.67)),
    "vapor":  ((0.6, 0.4, 0.7), (0.4, 0.4, 0.3), (1.0, 1.0, 1.0), (0.00, 0.45, 0.25)),
    "sunset": ((0.5, 0.3, 0.3), (0.5, 0.4, 0.3), (1.0, 1.0, 1.0), (0.00, 0.10, 0.25)),
    "toxic":  ((0.4, 0.6, 0.3), (0.4, 0.4, 0.4), (1.0, 1.0, 1.0), (0.30, 0.10, 0.60)),
    "candy":  ((0.8, 0.5, 0.6), (0.3, 0.5, 0.4), (1.0, 1.0, 1.0), (0.00, 0.30, 0.55)),
    "ice":    ((0.4, 0.6, 0.8), (0.3, 0.3, 0.3), (1.0, 1.0, 1.0), (0.55, 0.60, 0.65)),
    "mono":   ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), (0.00, 0.00, 0.00)),
}


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class Settings2:
    width: int = 540
    height: int = 540
    fps: int = 30
    seconds: float = 4.0
    seed: int = 1
    audio: str | None = None

    @property
    def frames(self) -> int:
        return max(1, round(self.fps * self.seconds))


@dataclass
class Layer:
    type: str = "shader"
    blend: str = "normal"
    opacity: float = 1.0
    shader: str | None = None          # path to a .frag
    source: str | None = None          # inline GLSL (wins over `shader`)
    source_sha256: str | None = None
    params: dict | None = None         # overrides; resolved values after resolve_recipe
    path: str | None = None            # image layers
    fit: str = "cover"
    uv_warp: float = 0.0
    effect: str | None = None          # v1 layers
    channels: list[str] = field(default_factory=list)   # image paths -> iChannel1..3
    extra: dict = field(default_factory=dict)

    _OPT = ("shader", "source", "source_sha256", "params", "path", "effect")

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"type": self.type}
        for k in self._OPT:
            v = getattr(self, k)
            if v is not None:
                d[k] = copy.deepcopy(v)
        if self.type == "image":
            d["fit"] = self.fit
            if self.uv_warp:
                d["uv_warp"] = self.uv_warp
        if self.channels:
            d["channels"] = list(self.channels)
        d["blend"] = self.blend
        d["opacity"] = self.opacity
        d.update(copy.deepcopy(self.extra))
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Layer":
        known = {f.name for f in fields(cls)} - {"extra"}
        kw = {k: copy.deepcopy(v) for k, v in d.items() if k in known}
        extra = {k: copy.deepcopy(v) for k, v in d.items() if k not in known}
        lay = cls(**kw, extra=extra)
        if lay.type not in LAYER_TYPES:
            raise ValueError(f"unknown layer type {lay.type!r}; have {LAYER_TYPES}")
        if lay.blend not in BLEND_MODES:
            raise ValueError(f"unknown blend mode {lay.blend!r}; have {BLEND_MODES}")
        return lay


@dataclass
class PostPass:
    name: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, **self.params}

    @classmethod
    def from_dict(cls, d: dict) -> "PostPass":
        d = dict(d)
        return cls(name=d.pop("name"), params=d)


@dataclass
class TextSpec:
    text: str = ""
    size: float = 0.13
    wobble: float = 0.0
    color: str = "#ffffff"
    shadow: str = "#ff2bd6"

    def to_dict(self) -> dict:
        return {"text": self.text, "size": self.size, "wobble": self.wobble,
                "color": self.color, "shadow": self.shadow}


@dataclass
class Recipe2:
    settings: Settings2 = field(default_factory=Settings2)
    palette: str = "acid"
    cycles: int = 1
    layers: list[Layer] = field(default_factory=list)
    post: list[PostPass] = field(default_factory=list)
    text: TextSpec | None = None
    version: int = 2
    base_dir: str | None = field(default=None, compare=False, repr=False)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "version": 2,
            "settings": {k: v for k, v in vars(self.settings).items()
                         if not (k == "audio" and v is None)},
            "palette": {"name": self.palette, "cycles": self.cycles},
            "layers": [l.to_dict() for l in self.layers],
            "post": [p.to_dict() for p in self.post],
        }
        if self.text and self.text.text:
            d["text"] = self.text.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict, base_dir: str | Path | None = None) -> "Recipe2":
        if "recipe" in d and "effect" in d.get("recipe", {}):   # v1 sidecar
            return recipe_from_v1(d)
        if d.get("version", 2) != 2:
            raise ValueError(f"unsupported recipe version {d.get('version')!r}")
        s = d.get("settings", {})
        sk = {f.name for f in fields(Settings2)}
        bad = set(s) - sk
        if bad:
            raise ValueError(f"unknown settings keys {sorted(bad)}")
        pal = d.get("palette", {})
        if isinstance(pal, str):
            pal = {"name": pal}
        if pal.get("name", "acid") not in PALETTES:
            raise ValueError(f"unknown palette {pal.get('name')!r}; have {sorted(PALETTES)}")
        t = d.get("text")
        text = None
        if t:
            tk = {f.name for f in fields(TextSpec)}
            text = TextSpec(**{k: v for k, v in t.items() if k in tk})
        settings = Settings2(**s)
        if settings.audio:
            settings.audio = Path(settings.audio).as_posix()
            if "seconds" not in s:        # audio set: the clip defaults to the audio's length
                from .audio import wav_duration
                settings.seconds = round(wav_duration(resolve_path(settings.audio, base_dir)), 3)
        return cls(settings=settings, palette=pal.get("name", "acid"),
                   cycles=int(pal.get("cycles", 1)),
                   layers=[Layer.from_dict(x) for x in d.get("layers", [])],
                   post=[PostPass.from_dict(x) for x in d.get("post", [])],
                   text=text, base_dir=str(base_dir) if base_dir else None)


# --------------------------------------------------------------------------- #
# v1 adapter
# --------------------------------------------------------------------------- #
def recipe_from_v1(v1: dict) -> Recipe2:
    """Convert a v1 sidecar ({"recipe": ..., "settings": ...}) or bare v1 recipe dict."""
    rc = dict(v1.get("recipe", v1))
    st = dict(v1.get("settings", {}))
    effect = rc["effect"]
    if effect not in funk.EFFECTS:
        raise ValueError(f"unknown v1 effect {effect!r}")
    layer = Layer(type="v1", effect=effect, params=copy.deepcopy(rc.get("params")),
                  extra={"cycles": int(rc.get("cycles", 1)), "spin": int(rc.get("spin", 0)),
                         "zoom": float(rc.get("zoom", 0.0)), "kaleido": int(rc.get("kaleido", 0)),
                         "palette": rc.get("palette", "acid")})
    post: list[PostPass] = []
    if int(rc.get("posterize", 0)) >= 2:
        post.append(PostPass("posterize", {"levels": int(rc["posterize"])}))
    if int(rc.get("halftone", 0)) >= 3:
        post.append(PostPass("halftone", {"cell": int(rc["halftone"])}))
    if int(rc.get("chroma", 0)):
        post.append(PostPass("chroma", {"px": int(rc["chroma"])}))
    if rc.get("scanlines"):
        post.append(PostPass("scanlines", {}))
    if float(rc.get("vignette", 0.0)):
        post.append(PostPass("vignette", {"amount": float(rc["vignette"])}))
    if float(rc.get("grain", 0.0)):
        post.append(PostPass("grain", {"amount": float(rc["grain"])}))
    text = None
    if rc.get("text"):
        text = TextSpec(text=rc["text"], size=float(rc.get("text_size", 0.13)),
                        wobble=float(rc.get("text_wobble", 0.0)),
                        color=rc.get("text_color", "#ffffff"), shadow=rc.get("shadow", "#ff2bd6"))
    sk = {f.name for f in fields(Settings2)}
    settings = Settings2(**{k: v for k, v in st.items() if k in sk})
    return Recipe2(settings=settings, palette=rc.get("palette", "acid"),
                   cycles=int(rc.get("cycles", 1)), layers=[layer], post=post, text=text)


# --------------------------------------------------------------------------- #
# IO
# --------------------------------------------------------------------------- #
def load_recipe(src: str | Path | dict) -> Recipe2:
    """Accept a v2 dict/JSON file or a v1 sidecar (dict or file)."""
    if isinstance(src, dict):
        return Recipe2.from_dict(src)
    p = Path(src)
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    return Recipe2.from_dict(data, base_dir=p.resolve().parent)


def layer_source(layer: Layer, base_dir: str | Path | None) -> str:
    if layer.source is not None:
        return layer.source
    if layer.shader:
        return resolve_path(layer.shader, base_dir).read_text(encoding="utf-8-sig")
    raise ValueError("shader layer needs `shader` (path) or `source` (inline GLSL)")


def resolve_recipe(recipe: Recipe2) -> Recipe2:
    """Return a copy in which shader layers carry inline source + sha256 + fully resolved
    params (seed-sampled, overridden by explicit values). Safe to save: it reproduces the
    render even if the .frag changes later."""
    r = copy.deepcopy(recipe)
    for i, lay in enumerate(r.layers):
        if lay.type == "shader":
            src = layer_source(lay, r.base_dir)
            sh = ShaderSource(src)
            lay.source = src
            lay.source_sha256 = hashlib.sha256(src.encode("utf-8")).hexdigest()
            lay.params = sample_params(sh.params, layer_seed(r.settings.seed, i), lay.params)
        elif lay.type == "v1":
            if lay.effect not in funk.EFFECTS:
                raise ValueError(f"unknown v1 effect {lay.effect!r}; have {sorted(funk.EFFECTS)}")
            if lay.params is None:
                lay.params = funk.EFFECTS[lay.effect].sample(
                    __import__("numpy").random.default_rng(r.settings.seed))
        elif lay.type == "image":
            if not lay.path:
                raise ValueError("image layer needs `path`")
            resolve_path(lay.path, r.base_dir)    # validates + enforces roots
            if lay.fit not in ("cover", "contain", "stretch"):
                raise ValueError(f"unknown fit {lay.fit!r}; use cover|contain|stretch")
    return r


def layer_seed(seed: int, index: int) -> int:
    """Layer 0 uses the recipe seed exactly; later layers get a decorrelated offset."""
    return seed + 7919 * index


def save_recipe(recipe: Recipe2, path: str | Path, resolve: bool = True,
                outputs: dict | None = None) -> Recipe2:
    r = resolve_recipe(recipe) if resolve else recipe
    d = r.to_dict()
    if outputs:
        d["outputs"] = {k: Path(v).as_posix() for k, v in outputs.items()}
    for lay in d["layers"]:
        for k in ("shader", "path"):
            if isinstance(lay.get(k), str):
                lay[k] = Path(lay[k]).as_posix()
        if "channels" in lay:
            lay["channels"] = [Path(c).as_posix() for c in lay["channels"]]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(d, indent=2), encoding="utf-8")
    return r


def recipe_digest(recipe: Recipe2) -> str:
    return hashlib.sha256(json.dumps([recipe.to_dict(), recipe.base_dir],
                                     sort_keys=True).encode("utf-8")).hexdigest()

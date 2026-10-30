"""Poster documents: strict validation and normalisation of ``*.poster.json``.

A poster is plain JSON (see docs/stills.md). ``check(doc)`` returns every problem at once;
``parse(doc)`` raises ``PosterError`` carrying the whole list, or returns a ``Doc`` with the
canvas resolved and every layer given an id.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from funkbox.gl.recipe import BLEND_MODES

PRESETS: dict[str, tuple[int, int]] = {
    "square": (1080, 1080),
    "insta-portrait": (1080, 1350),
    "story": (1080, 1920),
    "landscape": (1920, 1080),
    "twitter": (1600, 900),
    "a4-150dpi": (1240, 1754),
}
MAX_SIDE = 4096
MIN_SIDE = 64
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
BRAND_RE = re.compile(r"^brand\.(primary|secondary|text)$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

LAYER_TYPES = ("image", "solid", "gradient", "shader", "product", "text", "group", "border", "shape", "svg")
FITS = ("cover", "contain", "stretch")
EXPORTS = ("png", "jpg", "webp", "ora")
MAX_LAYERS = 64
MAX_DEPTH = 8

DOC_KEYS = {"version", "name", "canvas", "project", "casual", "copy", "brand", "seed", "layers", "post", "export"}
COMMON = {"id", "type", "blend", "opacity", "mask", "adjust", "filters", "visible", "clip"}
TYPE_KEYS: dict[str, set[str]] = {
    "image": {"path", "fit", "x", "y", "scale", "rotate_deg", "pre"},
    "solid": {"color"},
    "gradient": {"stops", "kind", "angle", "cx", "cy", "radius", "dither"},
    "shader": {"shader", "source", "source_sha256", "recipe", "t", "palette", "cycles", "params", "seed"},
    "product": {"image", "x", "y", "height", "shadow", "match_color", "effects"},
    "text": {"copy_id", "text", "style", "size", "x", "y", "max_width", "color", "align", "line_height", "effects",
             "arc", "rotate_deg", "skew_x", "skew_y", "wrap"},
    "group": {"layers"},
    "border": {"px", "color"},
    "shape": {"kind", "points", "fill", "stroke", "stroke_px", "width", "cap", "d", "fill_rule", "units"},
    "svg": {"path", "x", "y", "width", "height"},
}
ADJUST_KEYS = {"brightness", "contrast", "saturation", "hue", "levels", "temperature", "invert"}
FILTER_KEYS = ("blur", "sharpen", "posterize", "halftone", "duotone", "pixelate", "chroma", "vignette")
EFFECT_KEYS = {"shadow", "stroke", "glow"}
MASK_KEYS = {"none": set(), "radial": {"cx", "cy", "radius", "feather", "invert"},
             "linear": {"angle", "position", "feather", "invert"}, "image": {"path", "invert"}}
PRE_OPS = ("crop", "resize", "rotate", "flip", "remove_background", "trim_even")


class PosterError(ValueError):
    """Validation failure; ``errors`` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


@dataclass
class Doc:
    raw: dict                         # deep copy, ids filled in
    width: int
    height: int
    background: str
    seed: int
    project: str | None
    casual: bool
    copy_path: str | None
    brand: dict
    name: str
    exports: list[str]
    jpg_quality: int
    post: list[dict] = field(default_factory=list)

    @property
    def layers(self) -> list[dict]:
        return self.raw["layers"]


def is_color(v: Any, alpha: bool = True) -> bool:
    if not isinstance(v, str):
        return False
    if BRAND_RE.match(v):
        return True
    return bool(HEX_RE.match(v)) and (alpha or len(v) == 7)


def _num(v: Any, lo: float | None = None, hi: float | None = None) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def canvas_size(canvas: Any, errs: list[str] | None = None) -> tuple[int, int]:
    errs = errs if errs is not None else []
    if not isinstance(canvas, dict):
        errs.append("canvas: must be an object like {\"preset\": \"insta-portrait\", \"background\": \"#101014\"}")
        return 0, 0
    bad = set(canvas) - {"preset", "width", "height", "background"}
    if bad:
        errs.append(f"canvas: unknown keys {sorted(bad)}")
    if "preset" in canvas:
        if canvas["preset"] not in PRESETS:
            errs.append(f"canvas.preset {canvas['preset']!r}: use one of {', '.join(PRESETS)} or give width and height")
            return 0, 0
        if "width" in canvas or "height" in canvas:
            errs.append("canvas: give either `preset` or `width`+`height`, not both")
        return PRESETS[canvas["preset"]]
    w, h = canvas.get("width"), canvas.get("height")
    if not (isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool) and not isinstance(h, bool)):
        errs.append("canvas: needs a `preset` or integer `width` and `height`")
        return 0, 0
    if not (MIN_SIZE_OK(w) and MIN_SIZE_OK(h)):
        errs.append(f"canvas: width and height must be {MIN_SIDE}..{MAX_SIDE} (got {w}x{h})")
        return 0, 0
    return w - w % 2, h - h % 2          # the GPU engine works on even sizes


def MIN_SIZE_OK(v: int) -> bool:
    return MIN_SIDE <= v <= MAX_SIDE


# --------------------------------------------------------------------------- #
def _check_mask(m: Any, where: str, errs: list[str]) -> None:
    if m is None:
        return
    if not isinstance(m, dict) or m.get("type") not in MASK_KEYS:
        errs.append(f"{where}.mask: must be {{\"type\": none|radial|linear|image, ...}}")
        return
    t = m["type"]
    bad = set(m) - {"type"} - MASK_KEYS[t]
    if bad:
        errs.append(f"{where}.mask: unknown keys {sorted(bad)} for type {t!r}; have {sorted(MASK_KEYS[t])}")
    for k in ("cx", "cy", "radius", "position"):
        if k in m and not _num(m[k], -2, 4):
            errs.append(f"{where}.mask.{k}: must be a number")
    if "feather" in m and not _num(m["feather"], 0, 1):
        errs.append(f"{where}.mask.feather: must be 0..1")
    if "angle" in m and not _num(m["angle"], -720, 720):
        errs.append(f"{where}.mask.angle: degrees")
    if "invert" in m and not isinstance(m["invert"], bool):
        errs.append(f"{where}.mask.invert: must be true or false")
    if t == "image" and not isinstance(m.get("path"), str):
        errs.append(f"{where}.mask: type image needs `path`")


def _check_adjust(a: Any, where: str, errs: list[str]) -> None:
    if a is None:
        return
    if not isinstance(a, dict):
        errs.append(f"{where}.adjust: must be an object")
        return
    bad = set(a) - ADJUST_KEYS
    if bad:
        errs.append(f"{where}.adjust: unknown keys {sorted(bad)}; have {sorted(ADJUST_KEYS)}")
    rng = {"brightness": (-1, 1), "contrast": (-1, 1), "saturation": (-1, 2), "hue": (-360, 360),
           "temperature": (-1, 1)}
    for k, (lo, hi) in rng.items():
        if k in a and not _num(a[k], lo, hi):
            errs.append(f"{where}.adjust.{k}: must be a number in {lo}..{hi}")
    if "invert" in a and not isinstance(a["invert"], bool):
        errs.append(f"{where}.adjust.invert: must be true or false")
    if "levels" in a:
        lv = a["levels"]
        if not (isinstance(lv, list) and len(lv) == 3 and all(_num(x) for x in lv)
                and 0 <= lv[0] < lv[1] <= 1 and 0.1 <= lv[2] <= 10):
            errs.append(f"{where}.adjust.levels: must be [black, white, gamma] with 0 <= black < white <= 1, gamma 0.1..10")


def _check_filters(f: Any, where: str, errs: list[str]) -> None:
    if f is None:
        return
    if not isinstance(f, dict):
        errs.append(f"{where}.filters: must be an object")
        return
    bad = set(f) - set(FILTER_KEYS)
    if bad:
        errs.append(f"{where}.filters: unknown keys {sorted(bad)}; have {list(FILTER_KEYS)}")
    rng = {"blur": (0, 64), "sharpen": (0, 3), "posterize": (2, 16), "halftone": (3, 40), "pixelate": (2, 128),
           "chroma": (0, 24), "vignette": (0, 1.5)}
    for k, (lo, hi) in rng.items():
        if k in f and not _num(f[k], lo, hi):
            errs.append(f"{where}.filters.{k}: must be a number in {lo}..{hi}")
    if "duotone" in f:
        d = f["duotone"]
        if not (isinstance(d, list) and len(d) == 2 and all(is_color(c, alpha=False) for c in d)):
            errs.append(f"{where}.filters.duotone: must be [dark, light] as #rrggbb")


def _check_effects(e: Any, where: str, errs: list[str]) -> None:
    if e is None:
        return
    if not isinstance(e, dict):
        errs.append(f"{where}.effects: must be an object")
        return
    bad = set(e) - EFFECT_KEYS
    if bad:
        errs.append(f"{where}.effects: unknown keys {sorted(bad)}; have {sorted(EFFECT_KEYS)}")
    for k in EFFECT_KEYS & set(e):
        v = e[k]
        if isinstance(v, dict):
            ok = {"opacity", "dx", "dy", "blur", "color", "width", "radius"}
            if set(v) - ok:
                errs.append(f"{where}.effects.{k}: unknown keys {sorted(set(v) - ok)}; have {sorted(ok)}")
            if "color" in v and not is_color(v["color"], alpha=False):
                errs.append(f"{where}.effects.{k}.color: must be #rrggbb or brand.primary|secondary|text")
            for kk in set(v) - {"color"}:
                if not _num(v[kk], -200, 200):
                    errs.append(f"{where}.effects.{k}.{kk}: must be a number")
        elif not _num(v, 0, 1 if k != "stroke" else 1):
            errs.append(f"{where}.effects.{k}: a number 0..1 (strength) or an object")


def _check_pre(pre: Any, where: str, errs: list[str]) -> None:
    if pre is None:
        return
    if not isinstance(pre, list) or len(pre) > 16:
        errs.append(f"{where}.pre: must be a list of at most 16 geometry ops")
        return
    for i, op in enumerate(pre):
        if not (isinstance(op, dict) and op.get("op") in PRE_OPS):
            errs.append(f"{where}.pre[{i}]: must be {{\"op\": {'|'.join(PRE_OPS)}, ...}}")


def _check_layers(layers: Any, where: str, errs: list[str], seen: set[str], depth: int) -> None:
    if not isinstance(layers, list) or not layers:
        errs.append(f"{where}: must be a non-empty list of layers")
        return
    if depth > MAX_DEPTH:
        errs.append(f"{where}: groups nested deeper than {MAX_DEPTH}")
        return
    for i, lay in enumerate(layers):
        w = f"{where}[{i}]"
        if not isinstance(lay, dict):
            errs.append(f"{w}: must be an object")
            continue
        t = lay.get("type")
        if t not in LAYER_TYPES:
            errs.append(f"{w}: unknown layer type {t!r}; have {list(LAYER_TYPES)}")
            continue
        lid = lay.get("id")
        w = f"{where}[{i}] ({lid or t})"
        if lid is not None:
            if not isinstance(lid, str) or not ID_RE.match(lid):
                errs.append(f"{w}: id must match {ID_RE.pattern}")
            elif lid in seen:
                errs.append(f"{w}: duplicate layer id {lid!r}")
            else:
                seen.add(lid)
        bad = set(lay) - COMMON - TYPE_KEYS[t]
        if bad:
            errs.append(f"{w}: unknown keys {sorted(bad)} for a {t} layer; have {sorted(COMMON | TYPE_KEYS[t])}")
        if "blend" in lay and lay["blend"] not in BLEND_MODES:
            errs.append(f"{w}: unknown blend {lay['blend']!r}; have {BLEND_MODES}")
        if "opacity" in lay and not _num(lay["opacity"], 0, 1):
            errs.append(f"{w}: opacity must be 0..1")
        if "visible" in lay and not isinstance(lay["visible"], bool):
            errs.append(f"{w}: visible must be true or false")
        _check_mask(lay.get("mask"), w, errs)
        _check_adjust(lay.get("adjust"), w, errs)
        _check_filters(lay.get("filters"), w, errs)
        _check_type(t, lay, w, errs, seen, depth)


def _check_type(t: str, lay: dict, w: str, errs: list[str], seen: set[str], depth: int) -> None:
    if t == "image":
        if not isinstance(lay.get("path"), str) or not lay["path"]:
            errs.append(f"{w}: image layer needs `path`")
        if lay.get("fit", "cover") not in FITS:
            errs.append(f"{w}: fit must be one of {list(FITS)}")
        for k, lo, hi in (("x", -2, 3), ("y", -2, 3), ("scale", 0.01, 8), ("rotate_deg", -720, 720)):
            if k in lay and not _num(lay[k], lo, hi):
                errs.append(f"{w}: {k} must be a number in {lo}..{hi}")
        _check_pre(lay.get("pre"), w, errs)
    elif t == "solid":
        if not is_color(lay.get("color"), alpha=True):
            errs.append(f"{w}: solid layer needs `color` (#rrggbb, #rrggbbaa or brand.primary|secondary|text)")
    elif t == "gradient":
        st = lay.get("stops")
        if not (isinstance(st, list) and 2 <= len(st) <= 8):
            errs.append(f"{w}: gradient needs `stops`: 2..8 colours or {{\"pos\", \"color\"}} objects")
        else:
            for j, s in enumerate(st):
                c = s.get("color") if isinstance(s, dict) else s
                if not is_color(c):
                    errs.append(f"{w}: stops[{j}] must be a colour (#rrggbb[aa]) or {{\"pos\": 0..1, \"color\": ...}}")
                if isinstance(s, dict) and "pos" in s and not _num(s["pos"], 0, 1):
                    errs.append(f"{w}: stops[{j}].pos must be 0..1")
        if lay.get("kind", "linear") not in ("linear", "radial"):
            errs.append(f"{w}: kind must be linear or radial")
        for k in ("angle", "cx", "cy", "radius"):
            if k in lay and not _num(lay[k], -720, 720):
                errs.append(f"{w}: {k} must be a number")
    elif t == "shader":
        n = sum(k in lay for k in ("shader", "source", "recipe"))
        if n != 1:
            errs.append(f"{w}: shader layer needs exactly one of `shader` (path), `source` (inline GLSL) or `recipe`")
        if "t" in lay and not _num(lay["t"], 0, 1):
            errs.append(f"{w}: t must be 0..1 (the loop position the shader is frozen at)")
        if "seed" in lay and not (isinstance(lay["seed"], int) and not isinstance(lay["seed"], bool)):
            errs.append(f"{w}: seed must be an integer")
        if "params" in lay and not isinstance(lay["params"], dict):
            errs.append(f"{w}: params must be an object")
        if "recipe" in lay and not isinstance(lay["recipe"], (dict, str)):
            errs.append(f"{w}: recipe must be a funk2 recipe object or a path")
    elif t == "product":
        if not isinstance(lay.get("image"), str) or not lay["image"]:
            errs.append(f"{w}: product layer needs `image` (a PNG with alpha)")
        for k, lo, hi in (("x", 0, 1), ("y", 0, 1.5), ("height", 0.02, 3), ("shadow", 0, 1), ("match_color", 0, 1)):
            if k in lay and not _num(lay[k], lo, hi):
                errs.append(f"{w}: {k} must be a number in {lo}..{hi}")
        _check_effects(lay.get("effects"), w, errs)
    elif t == "text":
        has_id, has_lit = "copy_id" in lay, "text" in lay
        if has_id == has_lit:
            errs.append(f"{w}: text layer needs exactly one of `copy_id` (approved copy) or `text` (literal, casual docs only)")
        if has_id and not (isinstance(lay["copy_id"], str) and ID_RE.match(lay["copy_id"])):
            errs.append(f"{w}: copy_id must be a copy-deck line id")
        if has_lit and (not isinstance(lay["text"], str) or not lay["text"]):
            errs.append(f"{w}: literal text must be a non-empty string")
        if lay.get("style", "caption") not in ("display", "caption"):
            errs.append(f"{w}: style must be display or caption (the brand fonts)")
        if lay.get("align", "left") not in ("left", "center", "right"):
            errs.append(f"{w}: align must be left, center or right")
        if "color" in lay and not is_color(lay["color"], alpha=False):
            errs.append(f"{w}: color must be #rrggbb or brand.primary|secondary|text")
        for k, lo, hi in (("size", 0.01, 0.5), ("x", -1, 2), ("y", -1, 2), ("max_width", 0.05, 2),
                          ("line_height", 0.7, 3), ("rotate_deg", -360, 360), ("skew_x", -60, 60), ("skew_y", -60, 60)):
            if k in lay and not _num(lay[k], lo, hi):
                errs.append(f"{w}: {k} must be a number in {lo}..{hi}")
        if "wrap" in lay and not isinstance(lay["wrap"], bool):
            errs.append(f"{w}: wrap must be true or false (false: one line, shrunk to fit max_width)")
        if "wrap" in lay and lay["wrap"] is False and "\n" in str(lay.get("text", "")):
            errs.append(f"{w}: wrap false needs text without line breaks")
        if "arc" in lay and not (_num(lay["arc"], -20, 20) and abs(float(lay["arc"])) >= 0.05):
            errs.append(f"{w}: arc is the radius of the bend in canvas widths: -20..-0.05 (a frown) or 0.05..20 "
                        "(a smile)")
        _check_effects(lay.get("effects"), w, errs)
    elif t == "shape":
        from .shapes import check_shape
        check_shape(lay, w, errs, is_color, _num)
    elif t == "svg":
        if not isinstance(lay.get("path"), str) or not lay["path"].lower().endswith(".svg"):
            errs.append(f"{w}: svg layer needs `path` to an .svg file")
        for k, lo, hi in (("x", -2, 3), ("y", -2, 3), ("width", 0.01, 4), ("height", 0.01, 4)):
            if k in lay and not _num(lay[k], lo, hi):
                errs.append(f"{w}: {k} must be a number in {lo}..{hi}")
    elif t == "group":
        _check_layers(lay.get("layers"), f"{w}.layers", errs, seen, depth + 1)
    elif t == "border":
        if not _num(lay.get("px"), 1, 400):
            errs.append(f"{w}: border needs `px` (1..400)")
        if not is_color(lay.get("color", "#000000"), alpha=False):
            errs.append(f"{w}: color must be #rrggbb")


def _layer_list(layers: Any) -> list[dict]:
    """Every layer dict of an unvalidated tree, groups included, in drawing order."""
    out: list[dict] = []
    for lay in layers if isinstance(layers, list) else []:
        if isinstance(lay, dict):
            out.append(lay)
            if lay.get("type") == "group":
                out += _layer_list(lay.get("layers"))
    return out


def _check_clips(layers: Any, errs: list[str]) -> None:
    by_id = {l.get("id"): l for l in _layer_list(layers) if l.get("id")}
    for lay in _layer_list(layers):
        c = lay.get("clip")
        if c is None:
            continue
        w = f"layer {lay.get('id') or lay.get('type')!r}"
        tgt = by_id.get(c)
        if tgt is None:
            errs.append(f"{w}: clip {c!r} is not a layer id")
        elif tgt is lay:
            errs.append(f"{w}: a layer cannot clip to itself")
        elif tgt.get("type") not in ("shape", "svg"):
            errs.append(f"{w}: clip must name a shape or svg layer (got a {tgt.get('type')} layer)")
        elif tgt.get("clip"):
            errs.append(f"{w}: clip {c!r} is itself clipped; clip to a plain shape")


def check(doc: Any) -> list[str]:
    """Every structural problem in a poster document (empty list = valid). Copy and file checks happen at render."""
    errs: list[str] = []
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError as e:
            return [f"poster is not valid JSON: {e}"]
    if not isinstance(doc, dict):
        return ["poster must be a JSON object"]
    bad = set(doc) - DOC_KEYS
    if bad:
        errs.append(f"unknown top-level keys {sorted(bad)}; have {sorted(DOC_KEYS)}")
    if doc.get("version", 1) != 1:
        errs.append(f"unsupported poster version {doc.get('version')!r} (this is version 1)")
    canvas_size(doc.get("canvas"), errs)
    cv = doc.get("canvas")
    if isinstance(cv, dict) and "background" in cv and cv["background"] != "transparent" \
            and not is_color(cv["background"], alpha=False):
        errs.append("canvas.background: must be #rrggbb or \"transparent\"")
    if "project" in doc and not (isinstance(doc["project"], str) and SLUG_RE.match(doc["project"])):
        errs.append("project: must be a project slug (lowercase letters, digits, dashes)")
    if "casual" in doc and not isinstance(doc["casual"], bool):
        errs.append("casual: must be true or false")
    if "copy" in doc and not isinstance(doc["copy"], str):
        errs.append("copy: must be a path to a copy deck")
    if doc.get("project") and "copy" in doc:
        errs.append("copy: a project poster uses the project's approved copy.json; remove `copy`")
    if "seed" in doc and not (isinstance(doc["seed"], int) and not isinstance(doc["seed"], bool)):
        errs.append("seed: must be an integer")
    if "name" in doc and not (isinstance(doc["name"], str) and ID_RE.match(doc["name"])):
        errs.append("name: must match " + ID_RE.pattern)
    br = doc.get("brand")
    if br is not None:
        if not isinstance(br, dict) or set(br) - {"primary", "secondary", "text", "font_caption", "font_display",
                                                  "font_fallback"}:
            errs.append("brand: only primary, secondary, text (colours) and font_caption, font_display, "
                        "font_fallback (font file paths or names like 'Rubik:700')")
        else:
            for k in ("primary", "secondary", "text"):
                if k in br and not (isinstance(br[k], str) and HEX_RE.match(br[k]) and len(br[k]) == 7):
                    errs.append(f"brand.{k}: must be #rrggbb")
    seen: set[str] = set()
    _check_layers(doc.get("layers"), "layers", errs, seen, 1)
    _check_clips(doc.get("layers"), errs)
    if isinstance(doc.get("layers"), list) and _count(doc["layers"]) > MAX_LAYERS:
        errs.append(f"too many layers (limit {MAX_LAYERS})")
    post = doc.get("post", [])
    if not (isinstance(post, list) and all(isinstance(p, dict) and isinstance(p.get("name"), str) for p in post)):
        errs.append("post: must be a list of {\"name\": ..., <param>: value} (grain, vignette, chroma, ...)")
    ex = doc.get("export", {})
    if not isinstance(ex, dict) or set(ex) - {"formats", "jpg_quality"}:
        errs.append("export: only `formats` and `jpg_quality`")
    else:
        fm = ex.get("formats", ["png"])
        if not (isinstance(fm, list) and fm and all(f in EXPORTS for f in fm)):
            errs.append(f"export.formats: a non-empty list of {list(EXPORTS)}")
        if "jpg_quality" in ex and not (isinstance(ex["jpg_quality"], int) and 30 <= ex["jpg_quality"] <= 100):
            errs.append("export.jpg_quality: 30..100")
    return errs


def _count(layers: list) -> int:
    n = 0
    for l in layers:
        n += 1
        if isinstance(l, dict) and l.get("type") == "group" and isinstance(l.get("layers"), list):
            n += _count(l["layers"])
    return n


def _fill_ids(layers: list[dict], used: set[str]) -> None:
    n = 1
    for lay in layers:
        if "id" not in lay:
            while f"{lay['type']}{n}" in used:
                n += 1
            lay["id"] = f"{lay['type']}{n}"
            used.add(lay["id"])
        if lay["type"] == "group":
            _fill_ids(lay["layers"], used)


def all_layers(layers: list[dict]):
    for l in layers:
        yield l
        if l.get("type") == "group":
            yield from all_layers(l["layers"])


def parse(doc: Any) -> Doc:
    errs = check(doc)
    if errs:
        raise PosterError(errs)
    if isinstance(doc, str):
        doc = json.loads(doc)
    raw = copy.deepcopy(doc)
    w, h = canvas_size(raw["canvas"])
    _fill_ids(raw["layers"], {l["id"] for l in all_layers(raw["layers"]) if "id" in l})
    ex = raw.get("export", {})
    return Doc(raw=raw, width=w, height=h, background=raw["canvas"].get("background", "#000000"),
               seed=int(raw.get("seed", 1)), project=raw.get("project"), casual=bool(raw.get("casual", False)),
               copy_path=raw.get("copy"), brand=dict(raw.get("brand") or {}), name=raw.get("name", "poster"),
               exports=list(ex.get("formats", ["png"])), jpg_quality=int(ex.get("jpg_quality", 92)),
               post=[dict(p) for p in raw.get("post", [])])

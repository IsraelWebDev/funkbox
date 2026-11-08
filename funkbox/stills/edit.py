"""Non-destructive image edits: an op list becomes a one-image poster, rendered to a NEW file with a sidecar.

Ops (each ``{"op": "<name>", ...}``, applied in order; ``{"crop": {...}}`` is accepted as shorthand):

  crop {x, y, w, h}                 fractions of the current image (top-left origin)
  crop {aspect: "4:5", anchor}      largest box of that shape; anchor center|top|bottom|left|right|top-left|...
  resize {width?, height?}          keeps the aspect (both: fit inside the box)
  rotate {deg}                      clockwise, canvas grows to fit
  flip {axis: "h"|"v"}
  remove_background                 reel's cut-out (cropped to the subject) with its quality score
  adjust {brightness, contrast, saturation, hue, levels, temperature, invert}
  filter {blur, sharpen, posterize, halftone, duotone, pixelate, chroma, vignette}
  overlay {path, x, y, scale, blend, opacity, depth, shadow}
  text {copy_id | text, style, size, x, y, max_width, color, align, line_height, effects}
  border {px, color}
  vignette {amount}

crop/resize/rotate/flip/remove_background change the picture's shape, so they must come before everything else.
overlay depth 0..1 scales the overlay by how near its spot is (needs the depth model; 1 = full effect).
overlay shadow: a number (opacity) or {opacity, light, distance, blur, squash}: a flattened shadow on the
  ground, cast away from the light. light is the angle the light comes from (0 = above, 90 = right).
adjust/filter/vignette apply to everything built so far (the picture plus any overlay/text/border before them).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from funkbox.config import funk_out, resolve_path

from . import render, schema
from .raster import RCtx, _resize_rgba, place_image
from .schema import ADJUST_KEYS, FILTER_KEYS, TYPE_KEYS
from .sheet import contact_sheet

GEOMETRY = ("crop", "resize", "rotate", "flip", "remove_background")
PIXEL = ("adjust", "filter", "vignette")
LAYERED = ("overlay", "text", "border")
ALL_OPS = GEOMETRY + PIXEL + LAYERED
ANCHORS = {
    "center": (0.5, 0.5),
    "top": (0.5, 0.0),
    "bottom": (0.5, 1.0),
    "left": (0.0, 0.5),
    "right": (1.0, 0.5),
    "top-left": (0.0, 0.0),
    "top-right": (1.0, 0.0),
    "bottom-left": (0.0, 1.0),
    "bottom-right": (1.0, 1.0),
}
OP_KEYS = {
    "crop": {"x", "y", "w", "h", "aspect", "anchor"},
    "resize": {"width", "height"},
    "rotate": {"deg"},
    "flip": {"axis"},
    "remove_background": set(),
    "vignette": {"amount"},
    "border": {"px", "color"},
    "overlay": {"path", "x", "y", "scale", "blend", "opacity", "depth", "shadow"},
    "adjust": set(ADJUST_KEYS),
    "filter": set(FILTER_KEYS),
    "text": TYPE_KEYS["text"],
}
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


class EditError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# op normalisation
# --------------------------------------------------------------------------- #
def normalize(ops: Any) -> list[dict]:
    if isinstance(ops, str):
        ops = json.loads(ops)
    if not isinstance(ops, list) or not ops:
        raise EditError('ops must be a non-empty list like [{"op": "crop", "aspect": "4:5"}, ...]')
    out, errs = [], []
    for i, op in enumerate(ops):
        if isinstance(op, dict) and "op" not in op and len(op) == 1:
            ((k, v),) = op.items()
            if k in ALL_OPS and isinstance(v, dict):
                op = {"op": k, **v}
            elif k in ALL_OPS and v in (None, True):
                op = {"op": k}
        if not isinstance(op, dict) or op.get("op") not in ALL_OPS:
            errs.append(f'ops[{i}]: must be {{"op": one of {", ".join(ALL_OPS)}, ...}}')
            continue
        kind = op["op"]
        bad = set(op) - {"op"} - OP_KEYS[kind]
        if bad:
            errs.append(f"ops[{i}] ({kind}): unknown keys {sorted(bad)}; have {sorted(OP_KEYS[kind])}")
        out.append(dict(op))
    seen_other = None
    for i, op in enumerate(out):
        if op["op"] in GEOMETRY:
            if seen_other:
                errs.append(
                    f"ops[{i}] ({op['op']}) comes after {seen_other}: crop, resize, rotate, flip and "
                    "remove_background change the picture's shape, so put them first"
                )
        elif seen_other is None:
            seen_other = f"ops[{i}] ({op['op']})"
    if errs:
        raise EditError("; ".join(errs))
    return out


# --------------------------------------------------------------------------- #
# geometry on the picture (CPU)
# --------------------------------------------------------------------------- #
def _crop(im: Image.Image, op: dict) -> Image.Image:
    iw, ih = im.size
    if "aspect" in op:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", str(op["aspect"]))
        if not m or float(m.group(1)) <= 0 or float(m.group(2)) <= 0:
            raise EditError(f"crop.aspect {op['aspect']!r}: use W:H like 4:5")
        ar = float(m.group(1)) / float(m.group(2))
        w, h = (iw, round(iw / ar)) if iw / ih < ar else (round(ih * ar), ih)
        w, h = min(iw, max(1, w)), min(ih, max(1, h))
        if op.get("anchor", "center") not in ANCHORS:
            raise EditError(f"crop.anchor: use one of {', '.join(ANCHORS)}")
        ax, ay = ANCHORS[op.get("anchor", "center")]
        x0, y0 = round((iw - w) * ax), round((ih - h) * ay)
    else:
        try:
            x, y, w_, h_ = (float(op[k]) for k in ("x", "y", "w", "h"))
        except KeyError:
            raise EditError("crop needs x, y, w, h (fractions of the image) or aspect") from None
        if not (
            0 <= x < 1 and 0 <= y < 1 and 0 < w_ <= 1 and 0 < h_ <= 1 and x + w_ <= 1 + 1e-9 and y + h_ <= 1 + 1e-9
        ):
            raise EditError("crop x, y, w, h must be fractions inside the image (x + w <= 1, y + h <= 1)")
        x0, y0, w, h = round(x * iw), round(y * ih), max(1, round(w_ * iw)), max(1, round(h_ * ih))
        w, h = min(w, iw - x0), min(h, ih - y0)
    return im.crop((x0, y0, x0 + w, y0 + h))


def _resize(im: Image.Image, op: dict) -> Image.Image:
    iw, ih = im.size
    w, h = op.get("width"), op.get("height")
    if w is None and h is None:
        raise EditError("resize needs width and/or height")
    for k, v in (("width", w), ("height", h)):
        if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 8192):
            raise EditError(f"resize.{k}: an integer 1..8192")
    s = min(w / iw if w else math.inf, h / ih if h else math.inf)
    return _resize_rgba(im, (max(1, round(iw * s)), max(1, round(ih * s))))


def _rotate(im: Image.Image, op: dict) -> Image.Image:
    d = op.get("deg")
    if isinstance(d, bool) or not isinstance(d, (int, float)):
        raise EditError("rotate needs deg (clockwise degrees)")
    q = d % 360
    if q == 0:
        return im
    if q in (90, 180, 270):
        return im.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[int(q)])
    return im.rotate(-d, resample=Image.BICUBIC, expand=True)


def _flip(im: Image.Image, op: dict) -> Image.Image:
    ax = str(op.get("axis", "")).lower()
    if ax in ("h", "horizontal", "x"):
        return ImageOps.mirror(im)
    if ax in ("v", "vertical", "y"):
        return ImageOps.flip(im)
    raise EditError("flip.axis must be h or v")


def _remove_background(im: Image.Image, ctx: RCtx) -> Image.Image:
    from funkbox.reel import ingest

    work = funk_out() / "edits" / f".work-{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        q = ingest.build_product_png(im, work / "cutout.png", work)
        out = Image.open(work / "cutout.png")
        out.load()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    ctx.info["remove_background"] = {k: q.get(k) for k in ("score", "ok", "problems", "method", "coverage", "holes")}
    if not q["ok"]:
        ctx.warnings.append("remove_background: " + "; ".join(q["problems"]))
    return out.convert("RGBA")


def apply_pre(im: Image.Image, ops: list[dict], ctx: RCtx) -> Image.Image:
    """Run the geometry ops of an image layer's ``pre`` list (also used by the poster renderer)."""
    for op in ops:
        k = op["op"]
        if k == "crop":
            im = _crop(im, op)
        elif k == "resize":
            im = _resize(im, op)
        elif k == "rotate":
            im = _rotate(im, op)
        elif k == "flip":
            im = _flip(im, op)
        elif k == "remove_background":
            im = _remove_background(im, ctx)
        elif k == "trim_even":
            w, h = im.size
            im = im.crop((0, 0, w - w % 2, h - h % 2))
        else:
            raise EditError(f"unknown pre op {k!r}")
    return im.convert("RGBA")


# --------------------------------------------------------------------------- #
# ops -> poster document
# --------------------------------------------------------------------------- #
def load_source(path: str | Path) -> tuple[Path, Image.Image]:
    p = resolve_path(path)
    im = Image.open(p)
    im.load()
    return p, ImageOps.exif_transpose(im).convert("RGBA")


SHADOW_KEYS = {"opacity", "light", "distance", "blur", "squash"}


def _num(v: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
        raise EditError(f"overlay.{what}: use a number from {lo:g} to {hi:g}")
    return float(v)


def _shadow_spec(v: Any) -> dict | None:
    """``shadow``: a number (opacity) or {opacity, light, distance, blur, squash}; None when off."""
    if v is None or v is False:
        return None
    if v is True:
        v = {}
    elif not isinstance(v, dict):
        v = {"opacity": v}
    bad = set(v) - SHADOW_KEYS
    if bad:
        raise EditError(f"overlay.shadow: unknown keys {sorted(bad)}; have {sorted(SHADOW_KEYS)}")
    spec = {
        "opacity": _num(v.get("opacity", 0.6), "shadow.opacity", 0, 1),
        "light": _num(v.get("light", 45), "shadow.light", -360, 360) % 360,
        "distance": _num(v.get("distance", 0.02), "shadow.distance", 0, 0.2),
        "blur": _num(v.get("blur", 0.01), "shadow.blur", 0, 0.1),
        "squash": _num(v.get("squash", 0.25), "shadow.squash", 0.02, 1),
    }
    return spec if spec["opacity"] > 0 else None


def _depth_factor(shaped: Image.Image, x: float, y: float, strength: float) -> float:
    """1 + strength * (2 * depth - 1) at the overlay's centre: near ground keeps or grows its size, far ground shrinks."""
    from funkbox.reel import depth as reel_depth  # the depth model is optional, so it is imported only when asked for

    rgb = shaped.convert("RGB")
    src = funk_out() / "cache" / "depth-src" / f"{hashlib.sha1(rgb.tobytes()).hexdigest()}.png"
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        tmp = src.with_suffix(".part")
        rgb.save(tmp, format="PNG")
        os.replace(tmp, src)
    try:
        d = reel_depth.estimate_depth(src)
    except reel_depth.DepthUnavailable as e:
        raise EditError(f"overlay.depth needs the depth model: {e}") from e
    h, w = d.shape
    r = max(2, min(w, h) // 40)
    cx, cy = min(w - 1, max(0, round(x * w))), min(h - 1, max(0, round(y * h)))
    near = float(d[max(0, cy - r) : cy + r + 1, max(0, cx - r) : cx + r + 1].mean())
    return 1.0 + strength * (2.0 * near - 1.0)


def _shadow_layer(ov_path: str, W: int, H: int, x: float, y: float, scale: float, spec: dict) -> Path | None:
    """A canvas-sized shadow PNG: the overlay's silhouette squashed onto the ground and pushed away from the light."""
    im = Image.open(ov_path)
    im.load()
    alpha = place_image(ImageOps.exif_transpose(im).convert("RGBA"), W, H, "contain", x, y, scale)[..., 3]
    ys, xs = np.nonzero(alpha)
    if ys.size == 0:
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    short = min(W, H)
    sh = max(1, round((y1 - y0) * spec["squash"]))
    sprite = Image.fromarray(np.ascontiguousarray(alpha[y0:y1, x0:x1])).resize((x1 - x0, sh), Image.BILINEAR)
    sprite = sprite.point(lambda v: round(v * spec["opacity"]))
    t = math.radians(spec["light"])
    dx, dy = -math.sin(t) * spec["distance"] * short, math.cos(t) * spec["distance"] * short
    mask = Image.new("L", (W, H), 0)
    mask.paste(sprite, (round(x0 + dx), round(y1 - sh + dy)))
    if spec["blur"] * short >= 0.5:
        mask = mask.filter(ImageFilter.GaussianBlur(spec["blur"] * short))
    ov_key = hashlib.sha1(Path(ov_path).read_bytes()).hexdigest()
    key = hashlib.sha1(
        "|".join([ov_key, str(W), str(H), repr((x, y, scale)), repr(sorted(spec.items()))]).encode()
    ).hexdigest()
    out = funk_out() / "cache" / "shadow" / f"{key}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part")
        black = Image.new("L", (W, H), 0)
        Image.merge("RGBA", (black, black, black, mask)).save(tmp, format="PNG")
        os.replace(tmp, out)
    return out


def build_doc(
    path: str | Path,
    ops: Any,
    *,
    name: str = "",
    project: str | None = None,
    copy: str | None = None,
    casual: bool = False,
) -> tuple[dict, Path]:
    """The one-image poster for ``ops`` applied to ``path`` (the source is only ever read)."""
    ops = normalize(ops)
    src_path, im = load_source(path)
    pre = [op for op in ops if op["op"] in GEOMETRY]
    rest = [op for op in ops if op["op"] not in GEOMETRY]
    pre.append({"op": "trim_even"})
    probe = RCtx(w=2, h=2)
    shaped = apply_pre(im, pre, probe)
    W, H = shaped.size
    if W < schema.MIN_SIDE or H < schema.MIN_SIDE:
        raise EditError(f"the edited picture is {W}x{H}; the minimum is {schema.MIN_SIDE} px per side")
    if W > schema.MAX_SIDE or H > schema.MAX_SIDE:
        raise EditError(
            f"the edited picture is {W}x{H}; the maximum is {schema.MAX_SIDE} px per side (add a resize op)"
        )
    has_alpha = np.asarray(shaped.getchannel("A")).min() < 255
    src = {"id": "src", "type": "image", "path": src_path.as_posix(), "fit": "stretch", "pre": pre}
    root: list[dict] = [src]
    counter = {"n": 0}

    def next_id(kind: str) -> str:
        counter["n"] += 1
        return f"{kind}{counter['n']}"

    def wrap() -> dict:
        g = {"id": next_id("stack"), "type": "group", "layers": list(root)}
        root[:] = [g]
        return g

    def target(slot: str, keys: set[str]) -> dict:
        t = root[0] if len(root) == 1 else wrap()
        if slot == "adjust" and (t.get("adjust") or t.get("filters")):
            return wrap()
        if slot == "filters" and set(t.get("filters", {})) & keys:
            return wrap()
        return t

    for op in rest:
        k = op["op"]
        body = {a: b for a, b in op.items() if a != "op"}
        if k == "adjust":
            target("adjust", set())["adjust"] = body
        elif k in ("filter", "vignette"):
            f = {"vignette": float(body.get("amount", 0.35))} if k == "vignette" else body
            t = target("filters", set(f))
            t["filters"] = {**t.get("filters", {}), **f}
        elif k == "overlay":
            if "path" not in body:
                raise EditError("overlay needs path")
            depth_k = _num(body.pop("depth", 0.0), "depth", 0, 1)
            shadow = _shadow_spec(body.pop("shadow", None))
            ov_path = resolve_path(body.pop("path")).as_posix()
            lay = {"id": next_id("overlay"), "type": "image", "fit": "contain", "path": ov_path, **body}
            x, y = float(body.get("x", 0.5)), float(body.get("y", 0.5))
            if depth_k:
                lay["scale"] = float(body.get("scale", 1.0)) * _depth_factor(shaped, x, y, depth_k)
            if shadow:
                sh = _shadow_layer(ov_path, W, H, x, y, float(lay.get("scale", 1.0)), shadow)
                if sh is not None:
                    root.append({"id": next_id("shadow"), "type": "image", "path": sh.as_posix(), "fit": "stretch"})
            root.append(lay)
        elif k == "text":
            root.append({"id": next_id("text"), "type": "text", **body})
        elif k == "border":
            root.append({"id": next_id("border"), "type": "border", **body})
    doc: dict[str, Any] = {
        "version": 1,
        "canvas": {"width": W, "height": H, "background": "transparent" if has_alpha else "#000000"},
        "layers": root,
        "export": {"formats": ["png"]},
    }
    if project:
        doc["project"] = project
    if copy:
        doc["copy"] = copy
    if casual:
        doc["casual"] = True
    if name:
        doc["name"] = name
    return doc, src_path


# --------------------------------------------------------------------------- #
# running an edit
# --------------------------------------------------------------------------- #
def edits_dir() -> Path:
    return funk_out() / "edits"


def checker_flatten(im: Image.Image, cell: int = 12) -> Image.Image:
    """RGBA over a light checkerboard (so transparency shows in previews)."""
    w, h = im.size
    yy, xx = np.mgrid[0:h, 0:w]
    chk = np.where(((xx // cell + yy // cell) % 2) == 0, 235, 205).astype(np.uint8)
    bg = Image.fromarray(np.dstack([chk, chk, chk]), "RGB")
    bg.paste(im.convert("RGBA"), mask=im.getchannel("A"))
    return bg


@dataclass
class EditResult:
    paths: dict
    doc: dict
    rendered: Any
    before: Image.Image
    compare: Path
    tag: str


def _stem(p: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", p.stem)[:40] or "image"


def edit_image(
    path: str | Path,
    ops: Any,
    name: str = "",
    *,
    project: str | None = None,
    copy: str | None = None,
    casual: bool = False,
    out_dir: str | Path | None = None,
) -> EditResult:
    """Apply ``ops`` to a COPY of the picture: writes ``<edits>/<stem>-<name|n>.png`` (+ sidecar, audit, before/after
    sheet). The source file is never written."""
    if name and not NAME_RE.match(name):
        raise EditError(f"bad edit name {name!r}: 1-40 of letters, digits, _ and -")
    doc, src_path = build_doc(path, ops, name="", project=project, copy=copy, casual=casual)
    d = render.fence_out(out_dir or edits_dir())
    stem = _stem(src_path)
    if name:
        tag = name
    else:
        n = 1
        while (d / f"{stem}-{n}.png").exists():
            n += 1
        tag = str(n)
    out_name = f"{stem}-{tag}"
    if (d / f"{out_name}.png").resolve() == src_path.resolve():
        raise EditError("the output would overwrite the source image; pick another name")
    doc["name"] = out_name
    r = render.render_document(doc)
    paths = render.write_outputs(r, d, out_name, ["png"])
    before = Image.open(src_path).convert("RGBA")
    sheet = contact_sheet(
        [checker_flatten(before), checker_flatten(r.pil.convert("RGBA"))], ["before", "after"], tile=520, cols=2
    )
    cp = d / f"{out_name}.compare.png"
    sheet.save(cp)
    paths["compare"] = cp
    return EditResult(paths, doc, r, before, cp, tag)

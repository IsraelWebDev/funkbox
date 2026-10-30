"""Render poster documents: layers -> RGBA rasters -> GPU fx -> masks -> GPU compositing -> post -> files."""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from funkbox.gl.recipe import Recipe2, layer_seed, resolve_recipe
from funkbox.config import allowed_roots, resolve_path
from funkbox.reel.schema import Brand

from . import fx, gl as glmod, raster, schema, text
from .raster import RCtx
from .schema import Doc

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")


class RenderError(ValueError):
    pass


@dataclass
class LayerOut:
    id: str
    type: str
    rgba: np.ndarray                  # canvas-sized, straight alpha, fx and mask baked in
    blend: str
    opacity: float
    visible: bool = True


@dataclass
class Rendered:
    doc: Doc
    width: int
    height: int
    layers: list[LayerOut]
    flat: np.ndarray                  # composite before the poster-level post passes (HxWx3)
    image: np.ndarray                 # final (HxWx3 uint8)
    background: tuple[int, int, int]
    audit: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolved: dict = field(default_factory=dict)      # the sidecar document
    alpha: np.ndarray | None = None                   # HxW uint8 when canvas.background is "transparent"
    info: dict = field(default_factory=dict)
    base_dir: Path | None = None                      # where relative files (fonts, svgs) were found

    @property
    def pil(self) -> Image.Image:
        if self.alpha is not None:
            return Image.fromarray(np.dstack([self.image, self.alpha]), "RGBA")
        return Image.fromarray(self.image)

    @property
    def flat_pil(self) -> Image.Image:
        """The layer composite before the poster-level post passes (what an .ora stack adds up to)."""
        if self.alpha is not None:
            return Image.fromarray(np.dstack([self.flat, self.alpha]), "RGBA")
        return Image.fromarray(self.flat)


# --------------------------------------------------------------------------- #
# context
# --------------------------------------------------------------------------- #
def make_context(doc: Doc, base_dir: str | Path | None, w: int, h: int, scale: float) -> RCtx:
    from funkbox.reel import project as P
    base = Path(base_dir) if base_dir else None
    bd: dict = dict(doc.brand)
    if doc.project:
        base = P.project_dir(doc.project)
        bd = {**(P.load_meta(doc.project).get("brand") or {})}
    brand = Brand(**{k: v for k, v in bd.items() if k in ("primary", "secondary", "text", "font_caption", "font_display",
                                                       "font_fallback")})
    return RCtx(w=w, h=h, scale=scale, base_dir=base, brand=brand, seed=doc.seed, full=(doc.width, doc.height))


def scaled_size(doc: Doc, scale: float) -> tuple[int, int]:
    if scale >= 1.0:
        return doc.width, doc.height
    w, h = max(64, round(doc.width * scale)), max(64, round(doc.height * scale))
    return w - w % 2, h - h % 2


# --------------------------------------------------------------------------- #
# shader layers
# --------------------------------------------------------------------------- #
def _shader_recipe(lay: dict, ctx: RCtx, gl: glmod.GL, index: int, doc_seed: int) -> tuple[Recipe2, dict]:
    """The loop recipe a shader layer renders, plus the resolved layer dict for the sidecar."""
    seed = int(lay.get("seed", layer_seed(doc_seed, index)))
    t = float(lay.get("t", 0.0))
    res = {k: copy.deepcopy(v) for k, v in lay.items() if k not in ("shader", "source", "recipe", "params")}
    res.update(seed=seed, t=t)
    if "recipe" in lay:
        src = lay["recipe"]
        if isinstance(src, str):
            p = resolve_path(src, ctx.base_dir)
            d, bd = json.loads(p.read_text(encoding="utf-8-sig")), p.parent
        else:
            d, bd = copy.deepcopy(src), ctx.base_dir
        d = copy.deepcopy(d)
        st = dict(d.get("settings", {}))
        st.update(width=gl.w, height=gl.h)
        d["settings"] = st
        rec = Recipe2.from_dict(d, base_dir=bd)
        if rec.text and rec.text.text:
            raise RenderError(f"shader layer {lay['id']!r}: the recipe draws text; remove its `text` block "
                              "(poster text comes only from text layers)")
        rr = resolve_recipe(rec)
        res["recipe"] = rr.to_dict()
        res["recipe"]["settings"]["width"], res["recipe"]["settings"]["height"] = gl.w, gl.h
        return rec, res
    layer = {"type": "shader", "params": lay.get("params")}
    if "source" in lay:
        layer["source"] = lay["source"]
    else:
        layer["shader"] = lay["shader"]
    rec = Recipe2.from_dict({"version": 2, "settings": {"width": gl.w, "height": gl.h, "fps": 30, "seconds": 4,
                                                        "seed": seed},
                             "palette": {"name": lay.get("palette", "acid"), "cycles": int(lay.get("cycles", 1))},
                             "layers": [layer]}, base_dir=ctx.base_dir)
    rr = resolve_recipe(rec)
    sl = rr.layers[0]
    res["source"] = sl.source
    res["source_sha256"] = sl.source_sha256
    res["params"] = dict(sl.params or {})
    return rec, res


def raster_shader(lay: dict, ctx: RCtx, gl: glmod.GL, index: int, doc_seed: int) -> tuple[np.ndarray, dict]:
    rec, res = _shader_recipe(lay, ctx, gl, index, doc_seed)
    rgb = gl.render_recipe(rec, res["t"], grain=True)
    out = np.empty(rgb.shape[:2] + (4,), np.uint8)
    out[..., :3] = rgb
    out[..., 3] = 255
    return out, res


# --------------------------------------------------------------------------- #
# the layer tree
# --------------------------------------------------------------------------- #
class _Run:
    def __init__(self, doc: Doc, ctx: RCtx, gl: glmod.GL, cache: dict | None = None):
        self.doc, self.ctx, self.gl = doc, ctx, gl
        self.cache = cache
        self.resolved_layers: dict[str, dict] = {}
        self.audit: list[dict] = []
        self._deck = None

    def layer_rgba(self, lay: dict, index: int, backdrop) -> np.ndarray:
        """Raster -> fx -> mask for one layer (``backdrop()`` reads the composite so far, for colour matching)."""
        key = None
        if self.cache is not None and not (lay["type"] == "product" and lay.get("match_color", 0) > 0):
            key = json.dumps({k: v for k, v in lay.items() if k not in ("opacity", "blend", "visible")},
                             sort_keys=True, default=str) + f"|{index}|{self.ctx.w}x{self.ctx.h}"
            hit = self.cache.get(key)
            if hit is not None:
                rgba, res, entry = hit
                if entry is not None:
                    self.audit.append(copy.deepcopy(entry))
                self.resolved_layers[lay["id"]] = res
                return rgba
        n_audit = len(self.audit)
        rgba = self._layer_rgba(lay, index, backdrop)
        if key is not None:
            if len(self.cache) > 96:                     # animations: keep the layers that stay still
                self.cache.clear()
            entry = self.audit[-1] if len(self.audit) > n_audit and lay["type"] == "text" else None
            self.cache[key] = (rgba, self.resolved_layers[lay["id"]], entry)
        return rgba

    def _layer_rgba(self, lay: dict, index: int, backdrop) -> np.ndarray:
        ctx, gl = self.ctx, self.gl
        t = lay["type"]
        res = {k: copy.deepcopy(v) for k, v in lay.items()}
        if t == "image":
            rgba = raster.raster_image(lay, ctx)
        elif t == "solid":
            rgba = raster.raster_solid(lay, ctx)
        elif t == "border":
            rgba = raster.raster_border(lay, ctx)
        elif t == "gradient":
            rgba = raster.raster_gradient(lay, ctx)
        elif t == "shader":
            rgba, res = raster_shader(lay, ctx, gl, index, self.doc.seed)
        elif t == "product":
            want = lay.get("match_color", 0) > 0
            bd = backdrop() if want else None
            rgba, info = raster.raster_product(lay, ctx, bd)
            if want and bd is None:
                ctx.warnings.append(f"product {lay['id']!r}: match_color is ignored inside a group")
        elif t == "text":
            rgba, entry = text.raster_text(lay, ctx, self.doc, self.deck())
            self.audit.append(entry)
        elif t == "shape":
            from .shapes import raster_shape
            rgba = raster_shape(lay, ctx)
        elif t == "svg":
            from .shapes import raster_svg
            rgba = raster_svg(lay, ctx)
        elif t == "group":
            rgba = self.group(lay)
        else:                                            # pragma: no cover - schema rejects it
            raise RenderError(f"unknown layer type {t!r}")
        rgba = gl.apply(rgba, fx.steps(lay.get("adjust"), lay.get("filters"), ctx.scale))
        rgba = raster.apply_mask(rgba, raster.mask_alpha(lay.get("mask"), ctx))
        if lay.get("clip"):                              # show this layer only where the named shape is
            rgba = raster.apply_mask(rgba, self.clip_alpha(lay["clip"]))
        self.resolved_layers[lay["id"]] = res
        return rgba

    def deck(self):
        return self._deck

    def clip_alpha(self, layer_id: str) -> np.ndarray:
        """Coverage (0..1) of a shape or svg layer, used as a clip; cached per id."""
        cache = self.__dict__.setdefault("_clips", {})
        if layer_id not in cache:
            from .shapes import raster_shape, raster_svg
            tgt = next(l for l in schema.all_layers(self.doc.layers) if l.get("id") == layer_id)
            rgba = (raster_shape if tgt["type"] == "shape" else raster_svg)(tgt, self.ctx)
            cache[layer_id] = rgba[..., 3].astype(np.float32) / 255.0
        return cache[layer_id]

    def group(self, lay: dict) -> np.ndarray:
        comp = self.gl.composite(None)
        try:
            for i, sub in enumerate(lay["layers"]):
                if sub.get("visible", True) is False:
                    continue
                rgba = self.layer_rgba(sub, i, lambda: None)
                comp.add(rgba, sub.get("blend", "normal"), sub.get("opacity", 1.0))
            return comp.read()
        finally:
            comp.close()


def _bg(doc: Doc, ctx: RCtx) -> tuple[int, int, int, int]:
    if doc.background == "transparent":
        return 0, 0, 0, 0
    return raster.rgba_of(doc.background, ctx.brand)[:3] + (255,)


def render_document(doc_in: Any, base_dir: str | Path | None = None, scale: float = 1.0,
                    cache: dict | None = None) -> Rendered:
    """Render a poster document (dict, JSON text or a parsed Doc). ``scale`` < 1 renders a faster preview.
    ``cache`` (a dict kept across calls) reuses layers whose settings did not change: animation frames."""
    doc = doc_in if isinstance(doc_in, Doc) else schema.parse(doc_in)
    w, h = scaled_size(doc, scale)
    ctx = make_context(doc, base_dir, w, h, w / doc.width)
    gl = glmod.get_gl(w, h)
    run = _Run(doc, ctx, gl, cache)
    run._deck = text.preflight(doc, ctx)             # copy rules first: nothing is drawn if they fail
    bg = _bg(doc, ctx)
    base = np.empty((gl.h, gl.w, 4), np.uint8)
    base[:] = bg
    comp = gl.composite(base)
    outs: list[LayerOut] = []
    try:
        for i, lay in enumerate(doc.layers):
            if lay.get("visible", True) is False:
                continue
            rgba = run.layer_rgba(lay, i, lambda: comp.read()[..., :3])
            outs.append(LayerOut(lay["id"], lay["type"], rgba, lay.get("blend", "normal"),
                                 float(lay.get("opacity", 1.0))))
            comp.add(rgba, outs[-1].blend, outs[-1].opacity)
        flat_rgba = comp.read()
    finally:
        comp.close()
    flat = np.ascontiguousarray(flat_rgba[..., :3])
    alpha = np.ascontiguousarray(flat_rgba[..., 3]) if bg[3] == 0 else None
    image = gl.post(flat, doc.post, doc.seed)
    resolved = copy.deepcopy(doc.raw)
    _inline(resolved["layers"], run.resolved_layers)
    return Rendered(doc, w, h, outs, flat, image, bg[:3], run.audit, ctx.warnings, resolved, alpha, ctx.info,
                    ctx.base_dir)


def _inline(layers: list[dict], res: dict[str, dict]) -> None:
    """Replace shader layers by their resolved form (inline source, resolved params and seed)."""
    for i, lay in enumerate(layers):
        r = res.get(lay["id"])
        if lay["type"] == "shader" and r is not None:
            layers[i] = r
        elif lay["type"] == "group":
            _inline(lay["layers"], res)


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def fence_out(out_dir: str | Path) -> Path:
    p = Path(out_dir).expanduser().resolve()
    roots = allowed_roots()
    if not any(p == r or p.is_relative_to(r) for r in roots):
        raise ValueError(f"output folder {out_dir} is outside the allowed folders "
                         f"({', '.join(str(r) for r in roots)})")
    return p


def _over_white(im: Image.Image) -> Image.Image:
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.getchannel("A"))
    return bg


def _atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_outputs(r: Rendered, out_dir: str | Path, name: str, formats: list[str] | None = None) -> dict[str, Path]:
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad output name {name!r}: use 1-60 of letters, digits, _ and -")
    d = fence_out(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    img = r.pil
    for f in formats or r.doc.exports:
        p = d / f"{name}.{f}"
        if f == "png":
            img.save(p, format="PNG")
        elif f == "jpg":
            flat = img if img.mode == "RGB" else _over_white(img)
            flat.save(p, format="JPEG", quality=r.doc.jpg_quality, subsampling=0, optimize=True)
        elif f == "webp":
            try:
                img.save(p, format="WEBP", quality=r.doc.jpg_quality)
            except (KeyError, OSError, ValueError):
                r.warnings.append("webp export skipped: this Pillow has no WebP support")
                continue
        elif f == "ora":
            from . import ora
            ora.write_ora(r, p)
        elif f in ("svg", "pdf"):
            from . import vector
            r.info.setdefault("vector", {})[f] = rep = vector.export(r, p)
            r.warnings += [f"{f}: {w}" for w in rep["warnings"]]
        paths[f] = p
    audit = d / f"{name}.text_audit.json"
    _atomic_json(audit, r.audit)
    paths["text_audit"] = audit
    side = d / f"{name}.poster.json"
    _atomic_json(side, r.resolved)
    paths["sidecar"] = side
    return paths


def render_poster(doc: Any, out_dir: str | Path, name: str = "", base_dir: str | Path | None = None) -> dict[str, Path]:
    """Render ``doc`` at full resolution and write ``<name>.png`` (and .jpg/.webp/.ora as the doc's export asks),
    ``<name>.text_audit.json`` and the reproducible sidecar ``<name>.poster.json``. Returns the paths by kind."""
    r = render_document(doc, base_dir)
    return write_outputs(r, out_dir, name or r.doc.name)

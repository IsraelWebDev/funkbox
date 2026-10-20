"""CPU rasterisation of poster layers into canvas-sized RGBA uint8 arrays (straight alpha), plus masks.

Everything that is geometry or shape lives here; colour passes and compositing are GPU work (gl.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from funkgl.recipe import resolve_path
from reel.motion import Camera
from reel.product import ProductCompositor, _premul_resize
from reel.schema import Brand, ProductSpec

from .schema import BRAND_RE

MAX_PIXELS = 100_000_000          # a single resized image may not exceed this many pixels


class RasterError(ValueError):
    pass


@dataclass
class RCtx:
    """What the rasterisers need to know about the poster being rendered."""
    w: int
    h: int
    scale: float = 1.0                         # preview scale: px-valued parameters are multiplied by it
    base_dir: Path | None = None
    brand: Brand = field(default_factory=Brand)
    seed: int = 1
    warnings: list[str] = field(default_factory=list)

    def px(self, v: float) -> float:
        return float(v) * self.scale


def rgba_of(c: str, brand: Brand) -> tuple[int, int, int, int]:
    """'#rrggbb', '#rrggbbaa' or 'brand.primary|secondary|text' -> (r, g, b, a)."""
    if BRAND_RE.match(c):
        c = getattr(brand, c.split(".", 1)[1])
    c = c.lstrip("#")
    a = int(c[6:8], 16) if len(c) == 8 else 255
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a


def transparent(w: int, h: int) -> np.ndarray:
    return np.zeros((h, w, 4), np.uint8)


# --------------------------------------------------------------------------- #
# image
# --------------------------------------------------------------------------- #
def _resize_rgba(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    if im.size == size:
        return im
    if size[0] * size[1] > MAX_PIXELS:
        raise RasterError(f"image layer would be {size[0]}x{size[1]} px after scaling; reduce `scale`")
    a = np.asarray(im.getchannel("A"))
    if a.min() == 255:                              # opaque: plain resampling
        return im.convert("RGB").resize(size, Image.LANCZOS).convert("RGBA")
    return _premul_resize(np.asarray(im), size)


def place_image(im: Image.Image, w: int, h: int, fit: str = "cover", x: float = 0.5, y: float = 0.5,
                scale: float = 1.0, rotate_deg: float = 0.0) -> np.ndarray:
    """Fit, scale, rotate and place ``im`` (RGBA) on a transparent w x h canvas; (x, y) is its centre."""
    iw, ih = im.size
    if fit == "stretch":
        sx, sy = w / iw, h / ih
    else:
        s = max(w / iw, h / ih) if fit == "cover" else min(w / iw, h / ih)
        sx = sy = s
    tw, th = max(1, round(iw * sx * scale)), max(1, round(ih * sy * scale))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cx, cy = x * w, y * h
    if abs(rotate_deg) < 1e-9:
        spr = _resize_rgba(im.convert("RGBA"), (tw, th))
        ox, oy = round(cx - tw / 2), round(cy - th / 2)
    else:
        spr = _resize_rgba(im.convert("RGBA"), (tw, th))
        spr = spr.rotate(-rotate_deg, resample=Image.BICUBIC, expand=True)
        ox, oy = round(cx - spr.width / 2), round(cy - spr.height / 2)
    out.paste(spr, (ox, oy))          # replaces the pixels (the canvas is transparent), clipped at the edges
    return np.asarray(out)


def raster_image(lay: dict, ctx: RCtx) -> np.ndarray:
    p = resolve_path(lay["path"], ctx.base_dir)
    im = Image.open(p)
    im.load()
    im = ImageOps.exif_transpose(im).convert("RGBA")
    if lay.get("pre"):
        from . import edit
        im = edit.apply_pre(im, lay["pre"], ctx)
    return place_image(im, ctx.w, ctx.h, lay.get("fit", "cover"), lay.get("x", 0.5), lay.get("y", 0.5),
                       lay.get("scale", 1.0), lay.get("rotate_deg", 0.0))


# --------------------------------------------------------------------------- #
# solid, border, gradient
# --------------------------------------------------------------------------- #
def raster_solid(lay: dict, ctx: RCtx) -> np.ndarray:
    out = np.empty((ctx.h, ctx.w, 4), np.uint8)
    out[:] = rgba_of(lay["color"], ctx.brand)
    return out


def raster_border(lay: dict, ctx: RCtx) -> np.ndarray:
    out = transparent(ctx.w, ctx.h)
    b = max(1, round(ctx.px(lay["px"])))
    out[:b], out[-b:], out[:, :b], out[:, -b:] = (rgba_of(lay.get("color", "#000000"), ctx.brand),) * 4
    return out


def _stops(lay: dict, brand: Brand):
    raw = lay["stops"]
    n = len(raw)
    pos, col = [], []
    for i, s in enumerate(raw):
        c = s["color"] if isinstance(s, dict) else s
        pos.append(float(s["pos"]) if isinstance(s, dict) and "pos" in s else i / (n - 1))
        col.append(rgba_of(c, brand))
    order = np.argsort(pos, kind="stable")
    pos = np.array(pos)[order]
    col = np.array(col, np.float64)[order]
    # interpolate premultiplied so a transparent stop does not tint its neighbour
    pm = np.concatenate([col[:, :3] * col[:, 3:4] / 255.0, col[:, 3:4]], axis=1)
    return pos, pm


def raster_gradient(lay: dict, ctx: RCtx) -> np.ndarray:
    w, h = ctx.w, ctx.h
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx += 0.5
    yy += 0.5
    if lay.get("kind", "linear") == "radial":
        cx, cy = lay.get("cx", 0.5) * w, lay.get("cy", 0.5) * h
        t = np.hypot(xx - cx, yy - cy) / (lay.get("radius", 0.5) * float(np.hypot(w, h)))   # fraction of the diagonal
    else:
        a = np.deg2rad(lay.get("angle", 90.0))          # 0 = left to right, 90 = top to bottom
        dx, dy = float(np.cos(a)), float(np.sin(a))
        length = abs(w * dx) + abs(h * dy)
        t = ((xx - w / 2) * dx + (yy - h / 2) * dy) / max(length, 1e-6) + 0.5
    t = np.clip(t, 0.0, 1.0).ravel()
    pos, pm = _stops(lay, ctx.brand)
    ch = np.stack([np.interp(t, pos, pm[:, k]) for k in range(4)], axis=-1).reshape(h, w, 4).astype(np.float32)
    a = ch[..., 3:4]
    rgb = np.where(a > 0, ch[..., :3] * 255.0 / np.maximum(a, 1e-6), 0.0)      # un-premultiply
    out = np.concatenate([rgb, a], axis=-1)
    if lay.get("dither", True):
        rng = np.random.default_rng(1234)
        out = np.floor(out + rng.random((h, w, 1), dtype=np.float32))     # rounding with a +-0.5 level dither (no banding)
    else:
        out = np.rint(out)
    return np.clip(out, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# product
# --------------------------------------------------------------------------- #
def _over(P: np.ndarray, A: np.ndarray, col, alpha: np.ndarray, x0: int, y0: int) -> None:
    """Premultiplied 'over' of ``col`` (rgb, constant or array) with ``alpha`` (0..1) at (x0, y0)."""
    h, w = alpha.shape
    X0, Y0, X1, Y1 = max(0, x0), max(0, y0), min(P.shape[1], x0 + w), min(P.shape[0], y0 + h)
    if X1 <= X0 or Y1 <= Y0:
        return
    al = alpha[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
    c = col if np.ndim(col) == 1 else col[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
    reg, areg = P[Y0:Y1, X0:X1], A[Y0:Y1, X0:X1]
    P[Y0:Y1, X0:X1] = np.asarray(c, np.float32) * al[..., None] + reg * (1.0 - al[..., None])
    A[Y0:Y1, X0:X1] = al + areg * (1.0 - al)


def _blur_mask(a: np.ndarray, r: float) -> np.ndarray:
    return np.asarray(Image.fromarray(a).filter(ImageFilter.GaussianBlur(max(0.5, r))), np.float32) / 255.0


def effect_param(effects: dict | None, key: str, brand: Brand, defaults: dict) -> dict | None:
    """Normalise an effects entry (a number 0..1 or an object) to {opacity, dx, dy, blur, color, width, radius}."""
    if not effects or key not in effects:
        return None
    v = effects[key]
    d = dict(defaults)
    if isinstance(v, dict):
        d.update(v)
    else:
        if float(v) <= 0:
            return None
        d["opacity"] = float(v)
    if isinstance(d.get("color"), str):
        d["rgb"] = rgba_of(d["color"], brand)[:3]
    else:
        d["rgb"] = d["color"]
    return d if d.get("opacity", 1) > 0 else None


def raster_product(lay: dict, ctx: RCtx, backdrop_rgb: np.ndarray | None) -> tuple[np.ndarray, dict]:
    """Shadow, glow/stroke and the product sprite as one RGBA layer. Product pixels are only scaled and (when
    ``match_color`` > 0) put through reel's single global Lab transform; nothing else touches them."""
    W, H = ctx.w, ctx.h
    path = resolve_path(lay["image"], ctx.base_dir)
    spec = ProductSpec(image=lay["image"], x=lay.get("x", 0.5), y=lay.get("y", 0.9), height=lay.get("height", 0.5),
                       depth=0.5, shadow=lay.get("shadow", 0.6), match_color=lay.get("match_color", 0.0))
    pc = ProductCompositor(path, spec, W, H)
    cam = Camera(mode="kenburns", zoom=1.0)
    info: dict = {"transform": None}
    if spec.match_color > 0 and backdrop_rgb is not None:
        info["transform"] = pc.fit_transform(backdrop_rgb, cam)
    pl = pc.place(cam)
    rgb, a8 = pc.sprite(pl)
    P = np.zeros((H, W, 3), np.float32)
    A = np.zeros((H, W), np.float32)
    fx = lay.get("effects")
    glow = effect_param(fx, "glow", ctx.brand, {"opacity": 0.6, "color": "#ffffff", "radius": 0.06})
    if glow:
        r = max(1.0, glow["radius"] * pl.h)
        m = _blur_mask(np.pad(a8, int(r * 3)), r)
        _over(P, A, np.array(glow["rgb"], np.float32), np.clip(m * 1.6, 0, 1) * glow["opacity"],
              pl.x0 - int(r * 3), pl.y0 - int(r * 3))
    stroke = effect_param(fx, "stroke", ctx.brand, {"opacity": 1.0, "color": "#ffffff", "width": 0.01})
    if stroke:
        k = max(1, round(stroke["width"] * pl.h)) * 2 + 1
        pad = k
        big = Image.fromarray(np.pad(a8, pad)).filter(ImageFilter.MaxFilter(k))
        _over(P, A, np.array(stroke["rgb"], np.float32), np.asarray(big, np.float32) / 255.0 * stroke["opacity"],
              pl.x0 - pad, pl.y0 - pad)
    if spec.shadow > 0:
        contact, pad, ch, drop, dpad, off = pc._shadow_masks(pl)
        _over(P, A, np.zeros(3, np.float32), contact * (0.55 * spec.shadow), pl.x0 - pad, pl.y0 + pl.h - ch // 2)
        _over(P, A, np.zeros(3, np.float32), drop * (0.35 * spec.shadow), pl.x0 - dpad + off // 2, pl.y0 - dpad + off)
    _over(P, A, rgb.astype(np.float32), a8.astype(np.float32) / 255.0, pl.x0, pl.y0)
    out = np.zeros((H, W, 4), np.uint8)
    straight = np.where(A[..., None] > 0, P / np.maximum(A[..., None], 1e-9), 0.0)
    out[..., :3] = np.clip(np.floor(straight + 0.5), 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(np.floor(A * 255.0 + 0.5), 0, 255).astype(np.uint8)
    info["placement"] = (pl.x0, pl.y0, pl.w, pl.h)
    return out, info


# --------------------------------------------------------------------------- #
# masks
# --------------------------------------------------------------------------- #
def _smooth(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    if e1 <= e0:
        return (x >= e1).astype(np.float32)
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def mask_alpha(m: dict | None, ctx: RCtx) -> np.ndarray | None:
    """A float32 HxW multiplier in 0..1 for a layer's mask spec, or None."""
    if not m or m.get("type", "none") == "none":
        return None
    w, h = ctx.w, ctx.h
    t = m["type"]
    if t == "image":
        p = resolve_path(m["path"], ctx.base_dir)
        g = ImageOps.fit(Image.open(p).convert("L"), (w, h), Image.LANCZOS)
        a = np.asarray(g, np.float32) / 255.0
    else:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        xn, yn = (xx + 0.5) / w, (yy + 0.5) / h
        f = float(m.get("feather", 0.3))
        if t == "radial":
            r = np.hypot(xn - m.get("cx", 0.5), yn - m.get("cy", 0.5))
            rad = float(m.get("radius", 0.5))
            a = 1.0 - _smooth(rad * (1.0 - f), rad, r)
        else:                                              # linear
            ang = np.deg2rad(m.get("angle", 90.0))
            dx, dy = float(np.cos(ang)), float(np.sin(ang))
            length = abs(w * dx) + abs(h * dy)
            proj = ((xn - 0.5) * w * dx + (yn - 0.5) * h * dy) / max(length, 1e-6) + 0.5
            pos = float(m.get("position", 0.5))
            a = 1.0 - _smooth(pos - f / 2, pos + f / 2, proj)
    if m.get("invert"):
        a = 1.0 - a
    return a.astype(np.float32)


def apply_mask(rgba: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    if mask is None:
        return rgba
    out = rgba.copy()
    out[..., 3] = np.clip(np.floor(rgba[..., 3].astype(np.float32) * mask + 0.5), 0, 255).astype(np.uint8)
    return out

"""Product compositing: placement, contact shadow, global Lab colour match. Never generative.

The label stays exact: the only changes to product pixels are scaling and ONE global per-channel
affine transform in Lab space (strength ``match_color``), which ``invert`` undoes (tested).
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from .motion import Camera
from .schema import ProductSpec

GAIN_LIMITS = (0.85, 1.15)
RING = 0.15                      # background ring around the product, fraction of its size

_M = np.array([[0.4124564, 0.3575761, 0.1804375],
               [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
_MI = np.linalg.inv(_M).astype(np.float32)


# --------------------------------------------------------------------------- #
# Lab
# --------------------------------------------------------------------------- #
def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float32) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)
    xyz = (lin @ _M.T) / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0).astype(np.float32)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    """Returns float sRGB in 0..255 (unrounded, clipped)."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx, fz = fy + a / 500.0, fy - b / 200.0
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f ** 3 > 0.008856, f ** 3, (f - 16.0 / 116.0) / 7.787).astype(np.float32) * _WHITE
    lin = np.clip(xyz @ _MI.T, 0.0, 1.0)
    c = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return np.clip(c * 255.0, 0.0, 255.0).astype(np.float32)


@dataclass(frozen=True)
class ColorTransform:
    """Per-channel affine in Lab: lab' = gain * lab + bias."""
    gain: tuple[float, float, float] = (1.0, 1.0, 1.0)
    bias: tuple[float, float, float] = (0.0, 0.0, 0.0)
    applied: float = 0.0            # strength actually used (<= match_color; see compute_transform)

    @property
    def identity(self) -> bool:
        return self.gain == (1.0, 1.0, 1.0) and self.bias == (0.0, 0.0, 0.0)


def apply_transform(rgb: np.ndarray, tr: ColorTransform) -> np.ndarray:
    """uint8 (...,3) -> uint8 (...,3)."""
    if tr.identity:
        return np.asarray(rgb, dtype=np.uint8)
    lab = srgb_to_lab(rgb) * np.array(tr.gain, np.float32) + np.array(tr.bias, np.float32)
    return np.rint(lab_to_srgb(lab)).astype(np.uint8)


def invert_transform(rgb: np.ndarray, tr: ColorTransform) -> np.ndarray:
    """Undo apply_transform (float result, for tests / audits)."""
    if tr.identity:
        return np.asarray(rgb, dtype=np.float32)
    lab = (srgb_to_lab(rgb) - np.array(tr.bias, np.float32)) / np.array(tr.gain, np.float32)
    return lab_to_srgb(lab)


def _clipped_fraction(lab: np.ndarray, max_over: float = 1.2) -> float:
    """Fraction of Lab colours that would be clipped by more than ``max_over`` sRGB levels.
    Clipping is not invertible, so a clipped label colour could never be recovered."""
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    f = np.stack([fy + a / 500.0, fy, fy - b / 200.0], axis=-1)
    xyz = np.where(f ** 3 > 0.008856, f ** 3, (f - 16.0 / 116.0) / 7.787) * _WHITE
    lin = xyz @ _MI.T
    c = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(np.maximum(lin, 1e-9), 1 / 2.4) - 0.055) * 255.0
    over = np.maximum(-c, c - 255.0)
    return float((over > max_over).any(axis=-1).mean())


def _fit(lp, mp, sp, mb, sb, k: float) -> ColorTransform:
    gain, bias = [], []
    for c in range(3):
        new_std = sp[c] + k * (sb[c] - sp[c])
        g = float(new_std / sp[c]) if sp[c] > 1e-3 else 1.0
        g = float(min(GAIN_LIMITS[1], max(GAIN_LIMITS[0], g)))
        new_mean = mp[c] + k * (mb[c] - mp[c])
        gain.append(round(g, 5))
        bias.append(round(float(new_mean - g * mp[c]), 4))
    return ColorTransform(tuple(gain), tuple(bias), round(k, 4))      # type: ignore[arg-type]


def compute_transform(sprite_rgb: np.ndarray, sprite_alpha: np.ndarray, bg_rgb: np.ndarray,
                      strength: float, max_clipped: float = 0.001) -> ColorTransform:
    """Move the product's per-channel Lab mean/std towards the background's by ``strength`` (0..1).

    ``strength`` is a CAP: the strength actually applied is backed off until at most
    ``max_clipped`` of the product's opaque pixels would leave the sRGB gamut. Gamut clipping is
    not invertible, so a stronger shift would change the label's colours for good.
    """
    if strength <= 0:
        return ColorTransform()
    m = sprite_alpha > 200
    if m.sum() < 16 or bg_rgb.size == 0:
        return ColorTransform()
    px = sprite_rgb[m]
    if len(px) > 40000:
        px = px[:: len(px) // 40000 + 1]
    lp = srgb_to_lab(px)
    lb = srgb_to_lab(bg_rgb.reshape(-1, 3))
    mp, sp, mb, sb = lp.mean(0), lp.std(0), lb.mean(0), lb.std(0)
    k = float(strength)
    while k > 0.01:
        tr = _fit(lp, mp, sp, mb, sb, k)
        lab2 = lp * np.array(tr.gain, np.float32) + np.array(tr.bias, np.float32)
        if _clipped_fraction(lab2) <= max_clipped:
            return tr
        k *= 0.85
    return ColorTransform()


# --------------------------------------------------------------------------- #
# Placement
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Placement:
    x0: int
    y0: int
    w: int
    h: int
    scale: float            # camera scale applied on top of spec.height

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x0, self.y0, self.x0 + self.w, self.y0 + self.h


def _gauss(mask: Image.Image, r: float) -> Image.Image:
    return mask.filter(ImageFilter.GaussianBlur(max(0.5, r)))


class ProductCompositor:
    """One product image placed in one shot."""

    def __init__(self, path: Path, spec: ProductSpec, frame_w: int, frame_h: int):
        self.path, self.spec, self.fw, self.fh = path, spec, frame_w, frame_h
        self.src = np.asarray(Image.open(path).convert("RGBA"))
        self.transform: ColorTransform | None = None
        self._sprites: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._shadows: OrderedDict[int, tuple[np.ndarray, int, int, np.ndarray, int, int]] = OrderedDict()

    # ---- geometry
    def target_height(self, cam: Camera) -> tuple[int, tuple[float, float], float]:
        p, s = cam.forward((self.spec.x, self.spec.y), self.spec.depth)
        return max(2, int(round(self.spec.height * self.fh * s))), p, s

    def place(self, cam: Camera) -> Placement:
        hp, p, s = self.target_height(cam)
        sh, sw = self.src.shape[:2]
        wp = max(1, int(round(sw * hp / sh))) if hp != sh else sw
        return Placement(int(round(p[0] * self.fw - wp / 2)), int(round(p[1] * self.fh - hp)), wp, hp, s)

    def sprite(self, pl: Placement) -> tuple[np.ndarray, np.ndarray]:
        """(rgb uint8 HxWx3, alpha uint8 HxW) scaled to the placement, colour-transform applied."""
        hit = self._sprites.get(pl.h)
        if hit is not None:
            return hit
        sh, sw = self.src.shape[:2]
        if (pl.w, pl.h) == (sw, sh):
            rgba = self.src                                 # scale 1: not resampled at all
        else:
            rgba = np.asarray(_premul_resize(self.src, (pl.w, pl.h)))
        rgb, a = rgba[..., :3], rgba[..., 3]
        if self.transform is not None:
            rgb = apply_transform(rgb, self.transform)
        out = (np.ascontiguousarray(rgb), np.ascontiguousarray(a))
        self._sprites[pl.h] = out
        while len(self._sprites) > 12:
            self._sprites.popitem(last=False)
        return out

    def fit_transform(self, bg_frame: np.ndarray, cam: Camera) -> ColorTransform:
        """Pick the colour transform from the background around the product (once per shot)."""
        self.transform = None
        self._sprites.clear()
        pl = self.place(cam)
        rgb, a = self.sprite(pl)
        mx, my = int(pl.w * RING), int(pl.h * RING)
        x0, y0, x1, y1 = pl.box
        X0, Y0, X1, Y1 = max(0, x0 - mx), max(0, y0 - my), min(self.fw, x1 + mx), min(self.fh, y1 + my)
        region = bg_frame[Y0:Y1, X0:X1].copy()
        ring = np.ones(region.shape[:2], bool)
        ring[max(0, y0 - Y0):max(0, y1 - Y0), max(0, x0 - X0):max(0, x1 - X0)] = False
        self.transform = compute_transform(rgb, a, region[ring], self.spec.match_color)
        self._sprites.clear()
        return self.transform

    # ---- shadows
    def _shadow_masks(self, pl: Placement):
        hit = self._shadows.get(pl.h)
        if hit is not None:
            return hit
        _, a = self.sprite(pl)
        w, h = pl.w, pl.h
        # contact shadow: soft ellipse centred on the base line
        pad = int(0.25 * w)
        cw, ch = w + 2 * pad, max(8, int(0.16 * h))
        m = Image.new("L", (cw, ch), 0)
        from PIL import ImageDraw
        ImageDraw.Draw(m).ellipse((pad + 0.08 * w, ch * 0.25, pad + 0.92 * w, ch * 0.75), fill=255)
        contact = np.asarray(_gauss(m, ch * 0.14), dtype=np.float32) / 255.0
        # drop shadow: blurred silhouette offset by 6% of the height
        off = int(round(0.06 * h))
        dpad = off + int(0.04 * h) + 4
        sil = Image.new("L", (w + 2 * dpad, h + 2 * dpad), 0)
        sil.paste(Image.fromarray(a), (dpad, dpad))
        drop = np.asarray(_gauss(sil, 0.012 * h), dtype=np.float32) / 255.0
        out = (contact, pad, ch, drop, dpad, off)
        self._shadows[pl.h] = out
        while len(self._shadows) > 8:
            self._shadows.popitem(last=False)
        return out

    # ---- draw
    def draw(self, frame: np.ndarray, cam: Camera) -> np.ndarray:
        """Composite shadow + product onto an HxWx3 uint8 frame (returns a new array)."""
        pl = self.place(cam)
        rgb, a = self.sprite(pl)
        out = frame.astype(np.float32)
        sh = self.spec.shadow
        if sh > 0:
            contact, pad, ch, drop, dpad, off = self._shadow_masks(pl)
            _mul(out, contact, pl.x0 - pad, pl.y0 + pl.h - ch // 2, 0.55 * sh)
            _mul(out, drop, pl.x0 - dpad + off // 2, pl.y0 - dpad + off, 0.35 * sh)
        _over(out, rgb, a, pl.x0, pl.y0)
        return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _premul_resize(rgba: np.ndarray, size: tuple[int, int]) -> Image.Image:
    """LANCZOS resize with premultiplied alpha so transparent edge pixels don't bleed their RGB."""
    f = rgba.astype(np.float32)
    f[..., :3] *= f[..., 3:4] / 255.0
    chans = [Image.fromarray(f[..., i], mode="F").resize(size, Image.LANCZOS) for i in range(4)]
    g = np.stack([np.asarray(c) for c in chans], axis=-1)
    a = np.clip(g[..., 3], 0, 255)
    rgb = np.where(a[..., None] > 0.5, g[..., :3] * 255.0 / np.maximum(a[..., None], 1e-3), 0.0)
    return Image.fromarray(np.clip(np.concatenate([rgb, a[..., None]], axis=-1) + 0.5, 0, 255).astype(np.uint8), "RGBA")


def _clip(out_shape, x0: int, y0: int, w: int, h: int):
    X0, Y0, X1, Y1 = max(0, x0), max(0, y0), min(out_shape[1], x0 + w), min(out_shape[0], y0 + h)
    if X1 <= X0 or Y1 <= Y0:
        return None
    return X0, Y0, X1, Y1, X0 - x0, Y0 - y0


def _mul(out: np.ndarray, mask: np.ndarray, x0: int, y0: int, opacity: float) -> None:
    c = _clip(out.shape, x0, y0, mask.shape[1], mask.shape[0])
    if c is None:
        return
    X0, Y0, X1, Y1, sx, sy = c
    m = mask[sy:sy + (Y1 - Y0), sx:sx + (X1 - X0)]
    out[Y0:Y1, X0:X1] *= (1.0 - opacity * m)[..., None]


def _over(out: np.ndarray, rgb: np.ndarray, a: np.ndarray, x0: int, y0: int) -> None:
    c = _clip(out.shape, x0, y0, rgb.shape[1], rgb.shape[0])
    if c is None:
        return
    X0, Y0, X1, Y1, sx, sy = c
    al = a[sy:sy + (Y1 - Y0), sx:sx + (X1 - X0)].astype(np.float32)[..., None] / 255.0
    fg = rgb[sy:sy + (Y1 - Y0), sx:sx + (X1 - X0)].astype(np.float32)
    out[Y0:Y1, X0:X1] = out[Y0:Y1, X0:X1] * (1.0 - al) + fg * al


# --------------------------------------------------------------------------- #
# cutout helper (studio shots on a near-uniform background)
# --------------------------------------------------------------------------- #
def cutout(photo: str | Path, tolerance: float = 28.0, feather: float = 22.0) -> Image.Image:
    """RGBA cut-out of a product photographed on a near-uniform background (e.g. white).

    Alpha comes from the colour distance to the border colour: 0 below ``tolerance``,
    ramping to 255 over ``feather``. Then the result is cropped to the product. For anything
    harder, cut the product out in GIMP.
    """
    im = Image.open(photo).convert("RGB")
    a = np.asarray(im, dtype=np.float32)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.sqrt(((a - bg) ** 2).sum(axis=2))
    alpha = np.clip((dist - tolerance) / max(feather, 1e-3), 0, 1)
    al = Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    rgba = im.convert("RGBA")
    rgba.putalpha(al)
    bbox = al.point(lambda v: 255 if v > 24 else 0).getbbox()
    return rgba.crop(bbox) if bbox else rgba

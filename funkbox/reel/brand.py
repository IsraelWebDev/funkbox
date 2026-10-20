"""brand_from_assets: palette (k-means on the product cut-out + logo), contrast-safe text colour, fonts.

Palette: seeded numpy k-means (k=6, Lab space) over the opaque pixels of product/product.png and
brand/logo.png.  primary = the most saturated cluster with >= 4% share, secondary = the darkest
cluster, text = #ffffff or #111111 (whichever has the better WCAG contrast on primary; primary is
darkened/lightened until the contrast is >= 4.5 and the tool says so).
"""
from __future__ import annotations

import colorsys
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from funkbox.media import font as font_

from . import project as P
from .product import lab_to_srgb, srgb_to_lab
from .schema import HEX_RE

K = 6
MIN_SHARE = 0.04
SAMPLE = 20000
MIN_CONTRAST = 4.5
DEFAULT_CAPTION_FONT = "fonts/Oswald-SemiBold.ttf"
DEFAULT_DISPLAY_FONT = "fonts/DMSerifDisplay-Regular.ttf"


# --------------------------------------------------------------------------- #
def hex_to_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(round(float(v))) for v in rgb)
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, v)) for v in (r, g, b)))


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    r, g, b = (_lin(float(v)) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    """WCAG 2 contrast ratio between two sRGB colours (tuples or #rrggbb)."""
    if isinstance(a, str):
        a = hex_to_rgb(a)
    if isinstance(b, str):
        b = hex_to_rgb(b)
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def delta_e(a, b) -> float:
    """CIE76 colour difference between two sRGB colours."""
    if isinstance(a, str):
        a = hex_to_rgb(a)
    if isinstance(b, str):
        b = hex_to_rgb(b)
    la, lb = srgb_to_lab(np.array(a, np.float32)), srgb_to_lab(np.array(b, np.float32))
    return float(np.linalg.norm(la - lb))


def saturation(rgb) -> float:
    r, g, b = (float(v) / 255.0 for v in rgb)
    return colorsys.rgb_to_hsv(r, g, b)[1]


# --------------------------------------------------------------------------- #
def opaque_pixels(path: Path, limit: int = SAMPLE, seed: int = 11) -> np.ndarray:
    """Up to ``limit`` RGB rows (uint8) of the opaque pixels of an image (alpha >= 200)."""
    im = Image.open(path)
    rgba = np.asarray(im.convert("RGBA"))
    m = rgba[..., 3] >= 200
    px = rgba[..., :3][m]
    if len(px) > limit:
        idx = np.random.default_rng(seed).choice(len(px), limit, replace=False)
        px = px[np.sort(idx)]
    return px


def kmeans(lab: np.ndarray, k: int = K, iters: int = 25, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Seeded k-means++ in Lab. Returns (labels, centres)."""
    rng = np.random.default_rng(seed)
    n = len(lab)
    k = min(k, n)
    centres = [lab[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min(((lab[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        if d2.sum() <= 0:
            centres.append(lab[rng.integers(n)])
            continue
        centres.append(lab[rng.choice(n, p=d2 / d2.sum())])
    c = np.array(centres, np.float64)
    labels = np.zeros(n, int)
    for _ in range(iters):
        d = ((lab[:, None, :] - c[None]) ** 2).sum(-1)
        new = d.argmin(1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            sel = lab[labels == j]
            if len(sel):
                c[j] = sel.mean(0)
    return labels, c


def palette_of(px: np.ndarray, seed: int = 7) -> list[dict]:
    """Clusters of a pixel set: [{rgb, share, sat, L}], most populous first. rgb is the per-channel
    median of the cluster's own pixels, so flat label colours come out exact."""
    lab = srgb_to_lab(px.astype(np.float32)).astype(np.float64)
    labels, centres = kmeans(lab, K, seed=seed)
    out = []
    for j in range(len(centres)):
        sel = px[labels == j]
        if len(sel) == 0:
            continue
        rgb = tuple(int(v) for v in np.median(sel, axis=0))
        out.append({"rgb": rgb, "hex": rgb_to_hex(rgb), "share": len(sel) / len(px), "sat": saturation(rgb),
                    "L": float(srgb_to_lab(np.array(rgb, np.float32))[0])})
    return sorted(out, key=lambda c: -c["share"])


def pick(clusters: list[dict]) -> dict:
    notes: list[str] = []
    big = [c for c in clusters if c["share"] >= MIN_SHARE and c["L"] > 12] or [c for c in clusters if c["share"] >= MIN_SHARE] or clusters
    prim = max(big, key=lambda c: c["sat"])
    if prim["sat"] < 0.12:
        notes.append("the product is nearly colourless; primary is its most colourful tone. Override with set_brand if needed.")
    dark_pool = [c for c in clusters if c["share"] >= 0.02] or clusters
    sec = min(dark_pool, key=lambda c: c["L"])
    if delta_e(sec["rgb"], prim["rgb"]) < 10:
        others = sorted((c for c in dark_pool if delta_e(c["rgb"], prim["rgb"]) >= 10), key=lambda c: c["L"])
        if others:
            sec = others[0]
        else:
            sec = {"rgb": (17, 17, 17), "hex": "#111111"}
            notes.append("no dark cluster distinct from primary; secondary set to #111111")
    primary = tuple(prim["rgb"])
    white, dark = (255, 255, 255), (17, 17, 17)
    text = white if contrast(white, primary) >= contrast(dark, primary) else dark
    if contrast(text, primary) < MIN_CONTRAST:
        before = rgb_to_hex(primary)
        lab = srgb_to_lab(np.array(primary, np.float32)).astype(np.float64)
        step = -1.5 if text == white else 1.5            # white text: darken primary; dark text: lighten it
        for _ in range(100):
            lab[0] = float(np.clip(lab[0] + step, 0, 100))
            cand = tuple(float(v) for v in lab_to_srgb(lab.astype(np.float32)))
            primary = tuple(int(round(v)) for v in cand)
            if contrast(text, primary) >= MIN_CONTRAST or lab[0] in (0.0, 100.0):
                break
        notes.append(f"primary {before} gave text contrast {contrast(text, hex_to_rgb(before)):.2f} < {MIN_CONTRAST}; "
                     f"{'darkened' if text == white else 'lightened'} to {rgb_to_hex(primary)}")
    return {"primary": rgb_to_hex(primary), "secondary": sec["hex"] if "hex" in sec else rgb_to_hex(sec["rgb"]),
            "text": rgb_to_hex(text), "notes": notes}


# --------------------------------------------------------------------------- #
def project_fonts(d: Path) -> tuple[str, str, str]:
    """(font_caption, font_display, note). User fonts in brand/fonts/ win over the bundled ones."""
    fonts = sorted(p for p in (d / "brand" / "fonts").glob("*") if p.suffix.lower() in (".ttf", ".otf"))
    if not fonts:
        return DEFAULT_CAPTION_FONT, DEFAULT_DISPLAY_FONT, "bundled Oswald (captions) and DM Serif Display (end card)"
    rel = [p.relative_to(d).as_posix() for p in fonts]
    disp = [r for r in rel if re.search(r"display|serif", Path(r).stem, re.I)]
    cap = [r for r in rel if r not in disp]
    caption = cap[0] if cap else rel[0]
    display = disp[0] if disp else (cap[1] if len(cap) > 1 else caption)
    return caption, display, f"your fonts: {Path(caption).name} (captions), {Path(display).name} (end card)"


def brand_from_assets(slug: str) -> dict:
    d = P.project_dir(slug)
    prod, logo = d / "product" / "product.png", d / "brand" / "logo.png"
    srcs = [p for p in (prod, logo) if p.is_file()]
    if not srcs:
        raise P.ProjectError("no product.png or brand/logo.png to take colours from; ingest a product photo or logo "
                             "first (or use set_brand to give the colours yourself)")
    px = np.concatenate([opaque_pixels(p) for p in srcs])
    if len(px) < 50:
        raise P.ProjectError("the product/logo image has almost no opaque pixels; check the cut-out")
    clusters = palette_of(px)
    chosen = pick(clusters)
    cap, disp, fnote = project_fonts(d)
    brand = {"primary": chosen["primary"], "secondary": chosen["secondary"], "text": chosen["text"],
             "font_caption": cap, "font_display": disp,
             "logo": "brand/logo.png" if logo.is_file() else None,
             "palette": [c["hex"] for c in clusters], "notes": chosen["notes"] + [fnote],
             "contrast": round(contrast(chosen["text"], chosen["primary"]), 2), "source": "auto"}
    meta = P.load_meta(slug)
    meta["brand"] = brand
    P.save_meta(slug, meta)
    P.advance_status(slug, "branded")
    brand["swatch"] = str(swatch(d, brand, clusters))
    return brand


def set_brand(slug: str, primary: str | None = None, secondary: str | None = None, text: str | None = None,
              font_caption: str | None = None, font_display: str | None = None) -> dict:
    """Override any brand value. Colours are #rrggbb; fonts are paths inside the project or bundled fonts/."""
    d = P.project_dir(slug)
    meta = P.load_meta(slug)
    brand = dict(meta.get("brand") or {})
    if not brand:
        cap, disp, fnote = project_fonts(d)
        brand = {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff", "font_caption": cap,
                 "font_display": disp, "logo": "brand/logo.png" if (d / "brand" / "logo.png").is_file() else None,
                 "palette": [], "notes": [fnote], "source": "manual"}
    for k, v in (("primary", primary), ("secondary", secondary), ("text", text)):
        if v is not None:
            if not isinstance(v, str) or not HEX_RE.match(v):
                raise P.ProjectError(f"{k} must be a #rrggbb colour, got {v!r}")
            brand[k] = v.lower()
    from funkbox.config import resolve_path
    for k, v in (("font_caption", font_caption), ("font_display", font_display)):
        if v is not None:
            try:
                resolve_path(v, d)
            except (ValueError, FileNotFoundError) as e:
                raise P.ProjectError(f"{k}: {e}") from None
            if Path(v).suffix.lower() not in (".ttf", ".otf"):
                raise P.ProjectError(f"{k} must be a .ttf or .otf file")
            brand[k] = v.replace("\\", "/")
    brand["contrast"] = round(contrast(brand["text"], brand["primary"]), 2)
    brand["source"] = "manual" if brand.get("source") != "auto" else "auto+manual"
    if brand["contrast"] < MIN_CONTRAST:
        brand.setdefault("notes", []).append(f"warning: text/primary contrast is {brand['contrast']} (< {MIN_CONTRAST})")
    meta["brand"] = brand
    P.save_meta(slug, meta)
    P.advance_status(slug, "branded")
    brand = dict(brand)
    brand["swatch"] = str(swatch(d, brand, []))
    return brand


# --------------------------------------------------------------------------- #
def swatch(d: Path, brand: dict, clusters: list[dict]) -> Path:
    """Swatch PNG: primary / secondary / text colours, a caption mock-up and the k-means palette.
    (A diagnostic image: its sample words are not ad copy and never reach a render.)"""
    W, H = 900, 420
    im = Image.new("RGB", (W, H), (28, 28, 32))
    dr = ImageDraw.Draw(im)
    pr, sc, tx = hex_to_rgb(brand["primary"]), hex_to_rgb(brand["secondary"]), hex_to_rgb(brand["text"])
    dr.rectangle((20, 20, 440, 200), fill=pr)
    dr.rectangle((460, 20, 880, 200), fill=sc, outline=(120, 120, 130))
    dr.text((34, 30), f"primary {brand['primary']}", font=font_(26), fill=tx)
    dr.text((474, 30), f"secondary {brand['secondary']}", font=font_(26), fill=tx if contrast(tx, sc) >= 3 else (255, 255, 255))
    f40 = font_(34)
    tw = int(dr.textlength("Sample caption", font=f40))
    dr.rectangle((60, 112, 60 + tw + 40, 112 + 60), fill=pr, outline=tx)
    dr.text((80, 122), "Sample caption", font=f40, fill=tx)
    dr.text((34, 205), f"text {brand['text']} on primary: contrast {brand.get('contrast', contrast(tx, pr)):.2f} "
                       f"({'OK' if brand.get('contrast', 5) >= MIN_CONTRAST else 'LOW'})", font=font_(20), fill=(235, 235, 235))
    cl = clusters or [{"rgb": hex_to_rgb(h), "hex": h, "share": 0.0} for h in brand.get("palette", [])]
    x = 20
    for c in cl[:6]:
        w = 136
        dr.rectangle((x, 250, x + w, 330), fill=tuple(c["rgb"]), outline=(120, 120, 130))
        dr.text((x + 2, 336), c["hex"], font=font_(16), fill=(235, 235, 235))
        if c.get("share"):
            dr.text((x + 2, 358), f"{c['share']:.0%}", font=font_(16), fill=(160, 170, 190))
        x += w + 12
    out = d / "out" / "brand-swatch.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out


def brand_text(b: dict) -> str:
    lines = [f"brand: primary {b['primary']}, secondary {b['secondary']}, text {b['text']} "
             f"(contrast {b.get('contrast')})",
             f"fonts: captions {b['font_caption']}, end card {b['font_display']}",
             f"logo: {b.get('logo') or 'none'}"]
    lines += [f"note: {n}" for n in b.get("notes", [])]
    if b.get("swatch"):
        lines.append(f"swatch: {b['swatch']}")
    return "\n".join(lines)

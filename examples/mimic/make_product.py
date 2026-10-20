#!/usr/bin/env python3
"""Draw the fictional GLOWMOP carpet-shampoo bottle procedurally (no image model, no third-party art),
so the product image can live in the repo and its label text is exact by construction.

    python examples/mimic/make_product.py            -> examples/mimic/glowmop_bottle.png (RGBA)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
W, H = 760, 1500                      # final size; drawn at 2x and downsampled for clean edges
S = 2
CRIMSON, NAVY, CREAM = (196, 22, 48), (22, 34, 66), (250, 246, 236)


def font(name: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(ROOT / "fonts" / name), px)


def silhouette(w: int, h: int) -> Image.Image:
    """Mask of cap + neck + shoulders + body (rounded HDPE bottle)."""
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    cx = w // 2
    body_w, body_top, body_bot = int(w * 0.86), int(h * 0.30), int(h * 0.985)
    d.rounded_rectangle([cx - body_w // 2, body_top, cx + body_w // 2, body_bot], radius=int(w * 0.10), fill=255)
    # shoulders: a wide ellipse blending body into neck
    sh_h = int(h * 0.16)
    d.ellipse([cx - body_w // 2, body_top - sh_h // 2, cx + body_w // 2, body_top + sh_h], fill=255)
    neck_w = int(w * 0.36)
    d.rectangle([cx - neck_w // 2, int(h * 0.135), cx + neck_w // 2, body_top], fill=255)
    cap_w = int(w * 0.42)
    d.rounded_rectangle([cx - cap_w // 2, int(h * 0.02), cx + cap_w // 2, int(h * 0.145)], radius=int(w * 0.03), fill=255)
    return m


def cylinder_shade(w: int, h: int, base: tuple[int, int, int], spec: float = 0.55) -> np.ndarray:
    """RGB float image of a lit cylinder: diffuse falloff to the edges + a soft specular stripe."""
    x = np.linspace(-1, 1, w, dtype=np.float32)
    nz = np.sqrt(np.clip(1 - x ** 2, 0, 1))
    light = np.array([-0.45, 0.0, 0.89], np.float32)
    diffuse = np.clip(-0.45 * x + 0.89 * nz, 0, 1)
    shade = 0.38 + 0.62 * diffuse
    highlight = spec * np.exp(-((x + 0.42) / 0.07) ** 2) + 0.18 * np.exp(-((x - 0.62) / 0.05) ** 2)
    col = np.asarray(base, np.float32)[None, :] / 255.0 * shade[:, None] + highlight[:, None]
    return np.broadcast_to(np.clip(col, 0, 1)[None, :, :], (h, w, 3)).copy()


def label(w: int, h: int) -> Image.Image:
    lab = Image.new("RGB", (w, h), CREAM)
    d = ImageDraw.Draw(lab)
    top = int(h * 0.36)
    d.rectangle([0, 0, w, top], fill=CRIMSON)
    d.rectangle([0, h - int(h * 0.17), w, h], fill=NAVY)
    # wordmark
    f = font("Oswald-SemiBold.ttf", int(h * 0.19))
    t = "GLOWMOP"
    tw = d.textlength(t, font=f)
    d.text(((w - tw) / 2, top * 0.12), t, font=f, fill=(255, 255, 255))
    f2 = font("Oswald-SemiBold.ttf", int(h * 0.055))
    t2 = "CARPET & UPHOLSTERY CARE"
    d.text(((w - d.textlength(t2, font=f2)) / 2, top * 0.72), t2, font=f2, fill=(255, 220, 226))
    # product name block
    f3 = font("DMSerifDisplay-Regular.ttf", int(h * 0.13))
    t3 = "Deep Clean"
    d.text(((w - d.textlength(t3, font=f3)) / 2, top + h * 0.02), t3, font=f3, fill=NAVY)
    f4 = font("Oswald-SemiBold.ttf", int(h * 0.06))
    t4 = "SHAMPOO CONCENTRATE"
    d.text(((w - d.textlength(t4, font=f4)) / 2, top + h * 0.175), t4, font=f4, fill=CRIMSON)
    # three check bullets
    f5 = font("Oswald-SemiBold.ttf", int(h * 0.045))
    for i, line in enumerate(("LIFTS DEEP STAINS", "SAFE ON WOOL & SYNTHETICS", "FRESH CITRUS SCENT")):
        y = top + h * (0.275 + i * 0.058)
        d.ellipse([w * 0.16, y + h * 0.008, w * 0.16 + h * 0.035, y + h * 0.043], fill=CRIMSON)
        d.text((w * 0.16 + h * 0.055, y), line, font=f5, fill=NAVY)
    f6 = font("Oswald-SemiBold.ttf", int(h * 0.05))
    t6 = "DEMO PRODUCT · 1 QT (946 mL)"
    d.text(((w - d.textlength(t6, font=f6)) / 2, h - h * 0.135), t6, font=f6, fill=(230, 236, 250))
    return lab


def main() -> Path:
    w, h = W * S, H * S
    mask = silhouette(w, h)
    body = cylinder_shade(w, h, (244, 244, 240), spec=0.35)
    img = Image.fromarray((body * 255).astype(np.uint8), "RGB")
    # cap: darker, ribbed
    cap = cylinder_shade(w, h, (235, 235, 232), spec=0.25)
    capimg = Image.fromarray((cap * 255).astype(np.uint8), "RGB")
    ribs = Image.new("L", (w, h), 0)
    rd = ImageDraw.Draw(ribs)
    cx, cap_w = w // 2, int(w * 0.42)
    for x in range(cx - cap_w // 2, cx + cap_w // 2, int(w * 0.018)):
        rd.line([(x, int(h * 0.025)), (x, int(h * 0.14))], fill=40, width=max(2, w // 300))
    capimg = Image.composite(Image.new("RGB", (w, h), (120, 120, 120)), capimg, ribs)
    capmask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(capmask).rectangle([0, 0, w, int(h * 0.145)], fill=255)
    img = Image.composite(capimg, img, capmask)
    # label wrapped on the body: shade it with the same cylinder falloff
    lx0, lx1 = int(w * 0.10), int(w * 0.90)
    ly0, ly1 = int(h * 0.40), int(h * 0.90)
    lab = label(lx1 - lx0, ly1 - ly0)
    lab_np = np.asarray(lab, np.float32) / 255.0
    shade = cylinder_shade(w, h, (255, 255, 255), spec=0.25)[ly0:ly1, lx0:lx1]
    lab_np = np.clip(lab_np * (0.25 + 0.75 * shade.mean(axis=2, keepdims=True)) + 0.12 * (shade - shade.mean()), 0, 1)
    img.paste(Image.fromarray((lab_np * 255).astype(np.uint8), "RGB"), (lx0, ly0))
    # subtle plastic seam + edge darkening via the mask's blur
    edge = mask.filter(ImageFilter.GaussianBlur(w * 0.012))
    arr = np.asarray(img, np.float32) * (0.72 + 0.28 * (np.asarray(edge, np.float32)[..., None] / 255.0))
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    out.putalpha(mask)
    out = out.resize((W, H), Image.LANCZOS)
    dest = HERE / "glowmop_bottle.png"
    out.save(dest, optimize=True)
    return dest


if __name__ == "__main__":
    print(main())
    sys.exit(0)

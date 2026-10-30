"""Compare a render with a reference picture: how close is it, and where does it differ?

    res = compare(candidate, reference)      # paths or PIL images
    res.scores   -> {"overall", "structure", "colour", "layout"}  each 0..100 (100 = the same)
    res.cells    -> a grid of per-region scores, worst first, each with what differs (colour or shape)
    res.palette  -> the reference's main colours, their share, and the nearest colour in the candidate (delta E)
    res.sheet    -> candidate | reference | heat map (red = differs), for looking at

The reference is fitted to the candidate's frame first ("cover": scaled to fill and centre-cropped, or "stretch").
Structure is SSIM on the grey image, colour is the CIE76 delta E of the blurred pictures (layout and big colour areas,
not texture), layout compares where the edges are. Numbers are deterministic, so a change can be measured: render,
compare, adjust, compare again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

WORK = 384                       # analysis size (longest side)
GRID = 4


@dataclass
class Comparison:
    scores: dict
    cells: list[dict]
    palette: list[dict]
    sheet: Image.Image
    size: tuple[int, int]
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = self.scores
        lines = [f"overall {s['overall']:.1f}/100  (structure {s['structure']:.1f}, colour {s['colour']:.1f}, "
                 f"layout {s['layout']:.1f})"]
        bad = [c for c in self.cells if c["score"] < 85][:4]
        if bad:
            lines.append("most different regions: " + "; ".join(
                f"{c['where']} {c['score']:.0f} ({c['differs']})" for c in bad))
        miss = [p for p in self.palette if p["delta_e"] > 12 and p["share"] >= 0.05]
        if miss:
            lines.append("reference colours not matched: " + ", ".join(
                f"{p['color']} ({p['share']:.0%}, nearest {p['nearest']}, dE {p['delta_e']:.0f})" for p in miss))
        return "\n".join(lines + self.notes)


# --------------------------------------------------------------------------- colour + filters
def _lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _box(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, edges clamped (summed-area table)."""
    p = np.pad(a, r, mode="edge")
    s = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    k = 2 * r + 1
    return (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)


def ssim_map(x: np.ndarray, y: np.ndarray, r: int = 3) -> np.ndarray:
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = _box(x, r), _box(y, r)
    vx, vy = _box(x * x, r) - mx * mx, _box(y * y, r) - my * my
    cxy = _box(x * y, r) - mx * my
    return ((2 * mx * my + C1) * (2 * cxy + C2)) / ((mx * mx + my * my + C1) * (vx + vy + C2))


def _edges(g: np.ndarray) -> np.ndarray:
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1] = g[2:] - g[:-2]
    m = np.hypot(gx, gy)
    return m > max(24.0, np.percentile(m, 90))


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in c[:3])


# --------------------------------------------------------------------------- main
def _load(x) -> Image.Image:
    if isinstance(x, Image.Image):
        im = x
    else:
        from funkbox.config import resolve_path
        im = Image.open(resolve_path(str(x)))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def fit(ref: Image.Image, size: tuple[int, int], mode: str = "cover") -> Image.Image:
    if mode == "stretch":
        return ref.resize(size, Image.LANCZOS)
    if mode != "cover":
        raise ValueError("fit is 'cover' or 'stretch'")
    return ImageOps.fit(ref, size, Image.LANCZOS)


def compare(candidate, reference, fit_mode: str = "cover", grid: int = GRID) -> Comparison:
    cand, ref = _load(candidate), _load(reference)
    notes = []
    ar_c, ar_r = cand.width / cand.height, ref.width / ref.height
    if abs(ar_c / ar_r - 1) > 0.02:
        notes.append(f"the reference is {ref.width}x{ref.height}, the candidate {cand.width}x{cand.height}: "
                     f"the reference was {'cropped' if fit_mode == 'cover' else 'stretched'} to fit")
    ref = fit(ref, cand.size, fit_mode)
    k = WORK / max(cand.size)
    ws = (max(16, round(cand.width * k)), max(16, round(cand.height * k)))
    A = np.asarray(cand.resize(ws, Image.LANCZOS), np.float64)
    B = np.asarray(ref.resize(ws, Image.LANCZOS), np.float64)
    ga, gb = A @ [0.299, 0.587, 0.114], B @ [0.299, 0.587, 0.114]
    smap = ssim_map(ga, gb)
    blur = max(1, round(max(ws) / 96))
    La = _lab(np.asarray(Image.fromarray(A.astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur)), np.float64))
    Lb = _lab(np.asarray(Image.fromarray(B.astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur)), np.float64))
    de = np.linalg.norm(La - Lb, axis=-1)
    ea, eb = _edges(ga), _edges(gb)
    # tolerant edge match: an edge counts as matched if the other picture has one within 2 px
    da = np.asarray(Image.fromarray((ea * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    db = np.asarray(Image.fromarray((eb * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0

    def scores(sl) -> dict:
        st = float(np.clip(smap[sl].mean(), 0, 1)) * 100
        col = float(np.clip(1 - de[sl].mean() / 40.0, 0, 1)) * 100     # dE 40 (very different) -> 0
        na, nb = ea[sl].sum(), eb[sl].sum()
        if na + nb == 0:
            lay = 100.0
        else:
            rec = (eb[sl] & da[sl]).sum() / max(nb, 1)
            pre = (ea[sl] & db[sl]).sum() / max(na, 1)
            lay = 100.0 * (2 * rec * pre / (rec + pre) if rec + pre else 0.0)
        return {"overall": 0.4 * st + 0.3 * col + 0.3 * lay, "structure": st, "colour": col, "layout": lay}

    total = {k_: round(v, 1) for k_, v in scores((slice(None), slice(None))).items()}
    H, W = ga.shape
    cells = []
    rows = ["top", "upper middle", "lower middle", "bottom"] if grid == 4 else [f"row {i + 1}" for i in range(grid)]
    cols = ["left", "centre-left", "centre-right", "right"] if grid == 4 else [f"col {j + 1}" for j in range(grid)]
    for i in range(grid):
        for j in range(grid):
            sl = (slice(i * H // grid, (i + 1) * H // grid), slice(j * W // grid, (j + 1) * W // grid))
            s = scores(sl)
            differs = "colour" if s["colour"] < s["structure"] and s["colour"] < s["layout"] else "shape"
            cells.append({"row": i, "col": j, "where": f"{rows[i]} {cols[j]}", "score": round(s["overall"], 1),
                          "colour": round(s["colour"], 1), "structure": round(s["structure"], 1),
                          "layout": round(s["layout"], 1), "differs": differs})
    cells.sort(key=lambda c: c["score"])
    palette = _palette(B.astype(np.uint8), A.astype(np.uint8))
    sheet = _sheet(cand, ref, smap, de, cells, grid)
    return Comparison(total, cells, palette, sheet, cand.size, notes)


def kmeans_palette(rgb: np.ndarray, k: int = 6, iters: int = 15) -> tuple[np.ndarray, np.ndarray]:
    """Main colours of a picture: k-means in Lab (farthest-point start, so it is deterministic and a small but
    distinct colour gets its own cluster). Returns (rgb colours, share of pixels), largest first."""
    px = rgb.reshape(-1, 3)
    step = max(1, len(px) // 20000)
    sample = px[::step].astype(np.float64)
    lab = _lab(sample)
    cent = [lab[0]]
    for _ in range(k - 1):
        d = np.min([np.linalg.norm(lab - c, axis=-1) for c in cent], axis=0)
        if d.max() < 3:
            break
        cent.append(lab[int(np.argmax(d))])
    C = np.array(cent)
    for _ in range(iters):
        lbl = np.argmin(np.linalg.norm(lab[:, None] - C[None], axis=-1), axis=1)
        C = np.array([lab[lbl == i].mean(0) if (lbl == i).any() else C[i] for i in range(len(C))])
    lbl = np.argmin(np.linalg.norm(lab[:, None] - C[None], axis=-1), axis=1)
    share = np.bincount(lbl, minlength=len(C)) / len(lbl)
    cols = np.array([sample[lbl == i].mean(0) if (lbl == i).any() else [0, 0, 0] for i in range(len(C))])
    order = np.argsort(-share)
    return cols[order], share[order]


def _palette(ref: np.ndarray, cand: np.ndarray, n: int = 6) -> list[dict]:
    """The reference's main colours; for each, how far the candidate is from having that colour over a similar area
    (delta E at the quantile matching half the colour's share) and the candidate's colour there."""
    pal, shares = kmeans_palette(ref, n)
    cp = cand.reshape(-1, 3)
    lc = _lab(cp)
    lp = _lab(np.clip(pal + 0.5, 0, 255).astype(np.uint8))
    out = []
    for col, l, share in zip(pal, lp, shares):
        if share < 0.01:
            continue
        d = np.linalg.norm(lc - l, axis=-1)
        thr = float(np.quantile(d, min(0.5, share / 2)))
        near = cp[d <= thr].mean(0)
        out.append({"color": _hex(col + 0.5), "share": round(float(share), 3), "nearest": _hex(near + 0.5),
                    "delta_e": round(thr, 1)})
    return out


def _sheet(cand: Image.Image, ref: Image.Image, smap, de, cells, grid) -> Image.Image:
    side = 480
    k = side / max(cand.size)
    sz = (max(1, round(cand.width * k)), max(1, round(cand.height * k)))
    a, b = cand.resize(sz, Image.LANCZOS), ref.resize(sz, Image.LANCZOS)
    bad = np.clip(np.maximum(1 - smap, de / 40.0), 0, 1)
    heat = Image.fromarray((bad * 255).astype(np.uint8)).resize(sz, Image.BILINEAR)
    grey = ImageOps.grayscale(a).point(lambda v: 90 + v * 0.55).convert("RGB")
    red = Image.new("RGB", sz, (230, 30, 40))
    hm = Image.composite(red, grey, heat)
    d = ImageDraw.Draw(hm)
    for c in cells:
        if c["score"] < 85:
            x0, y0 = c["col"] * sz[0] // grid, c["row"] * sz[1] // grid
            d.rectangle((x0, y0, (c["col"] + 1) * sz[0] // grid - 1, (c["row"] + 1) * sz[1] // grid - 1),
                        outline=(255, 210, 0), width=2)
            d.text((x0 + 4, y0 + 3), f"{c['score']:.0f}", fill=(255, 255, 255))
    pad, top = 8, 22
    sheet = Image.new("RGB", (3 * sz[0] + 4 * pad, sz[1] + top + pad), (24, 24, 28))
    dd = ImageDraw.Draw(sheet)
    for n, (im, lab) in enumerate(((a, "candidate"), (b, "reference (fitted)"), (hm, "differences"))):
        x = pad + n * (sz[0] + pad)
        sheet.paste(im, (x, top))
        dd.text((x, 5), lab, fill=(230, 230, 230))
    return sheet


def write(res: Comparison, out_dir: str | Path, name: str) -> dict[str, Path]:
    import json
    from .render import NAME_RE, fence_out
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad output name {name!r}")
    d = fence_out(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    png, js = d / f"{name}.compare.png", d / f"{name}.compare.json"
    res.sheet.save(png)
    js.write_text(json.dumps({"scores": res.scores, "cells": res.cells, "palette": res.palette, "notes": res.notes},
                             indent=2) + "\n", encoding="utf-8")
    return {"sheet": png, "report": js}

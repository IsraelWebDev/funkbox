"""Exact text on posters: the copy rules, layout (reel's wrapping and fit), drawing with effects, and the audit.

Rules (the exactness guarantee, same as reel):
  * in a **project** poster every text layer uses ``copy_id`` and the project's approved ``copy.json`` must still match
    its lock (the same check the reel validator uses);
  * outside a project, ``copy_id`` needs ``"copy": "<deck path>"`` and a literal ``text`` layer needs ``"casual": true``;
  * text is wrapped on spaces only, never split inside a word, never truncated, never re-cased; a block that cannot fit
    at the minimum size is an error naming the layer and the copy id;
  * every render records each drawn string, its source and its layer id (``<name>.text_audit.json``).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from funkbox.config import resolve_path
from funkbox.reel import captions
from funkbox.reel import project as P
from funkbox.reel.copydeck import CopyDeck, parse_copy
from funkbox.reel.schema import ReelError

from .raster import RCtx, _over, rgba_of
from .schema import Doc, PosterError, all_layers

TEXT_SIZE = 0.06


def text_layers(doc: Doc) -> list[dict]:
    return [l for l in all_layers(doc.layers) if l["type"] == "text"]


# --------------------------------------------------------------------------- #
# the copy deck and the rules
# --------------------------------------------------------------------------- #
def deck_for(doc: Doc, ctx: RCtx) -> CopyDeck | None:
    """The deck text layers may draw from: the project's locked copy.json, or the doc's ``copy`` file."""
    if doc.project:
        d = P.project_dir(doc.project)
        cp = d / "copy.json"
        if cp.is_symlink() or not cp.is_file():
            raise PosterError([P.copy_lock_error(doc.project) or f"project {doc.project!r} has no approved copy.json"])
        data = cp.read_bytes()                      # ONE read: the bytes that are hash-checked are the bytes drawn
        err = P.copy_lock_error(doc.project, data)
        if err:
            raise PosterError([err])
        return _parse(data, cp)
    if doc.copy_path:
        cp = resolve_path(doc.copy_path, ctx.base_dir)
        data = cp.read_bytes()
        err = P.check_reel_copy(cp, ctx.base_dir, data)
        if err:
            raise PosterError([err])
        return _parse(data, cp)
    return None


def _parse(data: bytes, path: Path) -> CopyDeck:
    try:
        return parse_copy(json.loads(data.decode("utf-8-sig")), str(path))
    except (ValueError, ReelError) as e:
        raise PosterError([f"copy deck {path}: {e}"]) from None


def preflight(doc: Doc, ctx: RCtx) -> CopyDeck | None:
    """Check every text layer against the rules BEFORE any pixel is drawn; returns the deck (or None)."""
    layers = text_layers(doc)
    if not layers:
        return None
    errs: list[str] = []
    need_deck = False
    for l in layers:
        w = f"text layer {l['id']!r}"
        if "text" in l:
            if doc.project:
                errs.append(f"{w}: literal text is not allowed in a project poster; use copy_id with the project's "
                            "approved copy (project_propose_copy, then project_approve_copy)")
            elif not doc.casual:
                errs.append(f"{w}: literal text is only allowed in casual posters; set \"casual\": true, or use copy_id "
                            "with an approved copy deck (exact text is the default)")
        else:
            need_deck = True
            if not doc.project and not doc.copy_path:
                errs.append(f"{w}: copy_id {l['copy_id']!r} needs a copy deck: put the poster in a project, or set "
                            "\"copy\": \"<path to a copy.json>\"")
    if errs:
        raise PosterError(errs)
    deck = deck_for(doc, ctx) if need_deck else None
    if deck is not None:
        for l in layers:
            if "copy_id" in l and l["copy_id"] not in deck.lines:
                errs.append(f"text layer {l['id']!r}: unknown copy id {l['copy_id']!r}; the deck has {sorted(deck.lines)}")
    if errs:
        raise PosterError(errs)
    return deck


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
def _fonts(ctx: RCtx) -> captions.Fonts:
    b = ctx.brand
    return captions.fonts_for(b, ctx.base_dir)


def layout_text(lay: dict, text: str, ctx: RCtx, fonts: captions.Fonts) -> tuple[captions.Layout, int, int]:
    """Lay ``text`` out at full resolution. Returns (layout, frame width px, x of the frame). Raises PosterError."""
    W, H = ctx.full
    which = lay.get("style", "caption")
    max_w = float(lay.get("max_width", 0.84))
    x_frac = float(lay.get("x", (1.0 - max_w) / 2))
    y_frac = float(lay.get("y", 0.1))
    line_height = float(lay.get("line_height", 1.1))
    frame_w = int(round(max_w * W))
    x0, y0 = int(round(x_frac * W)), int(round(y_frac * H))
    min_px = max(8, math.ceil(captions.MIN_SIZE * H))
    want = int(round(float(lay.get("size", TEXT_SIZE)) * H))
    if want < min_px:
        ctx.warnings.append(f"text layer {lay['id']!r}: size raised to the minimum ({captions.MIN_SIZE} x height)")
    px = max(min_px, want)
    label = f"text layer {lay['id']!r}" + (f" (copy {lay['copy_id']!r})" if "copy_id" in lay else "")
    while True:
        try:
            font = fonts.get(which, px, text)
        except captions.GlyphError as e:
            raise PosterError([f"{label}: {e}"]) from e
        lines, fits = captions.wrap(text, font, frame_w)
        line_h = int(round(px * line_height))
        block_h = len(lines) * line_h
        widths = [font.getlength(text[s:e]) for s, e in lines]
        align = lay.get("align", "left")
        offs = [0.0 if align == "left" else (frame_w - w_) / (2 if align == "center" else 1) for w_ in widths]
        left = x0 + min(offs, default=0.0)
        right = x0 + max((o + w_ for o, w_ in zip(offs, widths)), default=0.0)
        inside = left >= 0 and y0 >= 0 and right <= W + 0.5 and y0 + block_h <= H
        if fits and inside:
            break
        if px <= min_px:
            why = ("a word is wider than the line" if not fits else
                   "the block runs outside the canvas; move x/y, widen max_width or shorten the text")
            raise PosterError([f"{label}: text does not fit at the minimum size "
                               f"({captions.MIN_SIZE} x canvas height = {min_px}px): {why}"])
        px = max(min_px, int(px * 0.94))
    bb = font.getbbox("H", anchor="ls")
    st = captions.Style(font=which, size=px / H, padding=0.0, max_width=max_w, line_height=line_height, box=None,
                        align="left")
    lo = captions.Layout(lay.get("copy_id", "literal"), text, st, fonts, ctx.brand, px, 0, line_h, lines, widths,
                         x0, y0, frame_w, block_h, -bb[1], (W, H))
    return lo, frame_w, x0


def _line_x(lay: dict, lo: captions.Layout, i: int, frame_w: int) -> float:
    align = lay.get("align", "left")
    if align == "center":
        return (frame_w - lo.widths[i]) / 2
    if align == "right":
        return frame_w - lo.widths[i]
    return 0.0


# --------------------------------------------------------------------------- #
# effects and drawing
# --------------------------------------------------------------------------- #
def _fx(lay: dict, key: str, ctx: RCtx, px: int, fill_rgb: tuple[int, int, int], defaults: dict) -> dict | None:
    e = (lay.get("effects") or {}).get(key)
    if e is None or e is False:
        return None
    d = dict(defaults)
    if isinstance(e, dict):
        d.update(e)
    else:
        if float(e) <= 0:
            return None
        d["opacity" if key != "stroke" else "width"] = float(e)
    if d.get("opacity", 1.0) <= 0 or (key == "stroke" and d["width"] <= 0):
        return None
    col = d["color"]
    d["rgb"] = fill_rgb if col is None else rgba_of(col, ctx.brand)[:3]
    return d


def draw_text(lay: dict, lo: captions.Layout, frame_w: int, ctx: RCtx) -> tuple[np.ndarray, list[str]]:
    """Full-resolution RGBA canvas (transparent) with the text, its effects, and the strings actually drawn."""
    W, H = ctx.full
    px = lo.px
    fill = rgba_of(lay.get("color", "brand.text"), ctx.brand)[:3]
    shadow = _fx(lay, "shadow", ctx, px, fill, {"opacity": 0.5, "dx": 0.04, "dy": 0.06, "blur": 0.05, "color": "#000000"})
    stroke = _fx(lay, "stroke", ctx, px, fill, {"width": 0.0, "opacity": 1.0, "color": "#000000"})
    glow = _fx(lay, "glow", ctx, px, fill, {"opacity": 0.5, "radius": 0.25, "color": None})
    sw = int(round(stroke["width"] * px)) if stroke else 0
    margin = int(px * 1.2) + sw + 4
    bw, bh = frame_w, lo.box_h
    cw, ch = bw + 2 * margin, bh + 2 * margin
    font = lo.font
    drawn: list[str] = []
    fill_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    mask_layer = Image.new("L", (cw, ch), 0)
    df, dm = ImageDraw.Draw(fill_layer), ImageDraw.Draw(mask_layer)
    for i, (s, e) in enumerate(lo.lines):
        line = lo.text[s:e]
        drawn.append(line)
        base = margin + i * lo.line_h + (lo.line_h + lo.cap_h) / 2
        x = margin + _line_x(lay, lo, i, frame_w)
        if line:
            df.text((x, base), line, font=font, fill=(*fill, 255), anchor="ls", stroke_width=sw,
                    stroke_fill=(*(stroke["rgb"] if stroke else fill), int(round(255 * (stroke or {}).get("opacity", 1.0)))))
            dm.text((x, base), line, font=font, fill=255, anchor="ls", stroke_width=sw)
    P = np.zeros((ch, cw, 3), np.float32)
    A = np.zeros((ch, cw), np.float32)
    sil = np.asarray(mask_layer, np.float32) / 255.0
    if glow:
        r = max(1.0, glow["radius"] * px)
        m = np.asarray(mask_layer.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255.0
        _over(P, A, np.array(glow["rgb"], np.float32), np.clip(m * 2.0, 0, 1) * glow["opacity"], 0, 0)
    if shadow:
        m = sil
        r = shadow["blur"] * px
        if r > 0.3:
            m = np.asarray(mask_layer.filter(ImageFilter.GaussianBlur(r)), np.float32) / 255.0
        dx, dy = int(round(shadow["dx"] * px)), int(round(shadow["dy"] * px))
        _over(P, A, np.array(shadow["rgb"], np.float32), np.roll(np.roll(m, dy, 0), dx, 1) * shadow["opacity"], 0, 0)
    fl = np.asarray(fill_layer, np.float32)
    _over(P, A, fl[..., :3], fl[..., 3] / 255.0, 0, 0)
    out_sprite = np.zeros((ch, cw, 4), np.uint8)
    straight = np.where(A[..., None] > 0, P / np.maximum(A[..., None], 1e-9), 0.0)
    out_sprite[..., :3] = np.clip(np.floor(straight + 0.5), 0, 255).astype(np.uint8)
    out_sprite[..., 3] = np.clip(np.floor(A * 255.0 + 0.5), 0, 255).astype(np.uint8)
    canvas = np.zeros((H, W, 4), np.uint8)
    ox, oy = lo.x0 - margin, lo.y0 - margin
    X0, Y0, X1, Y1 = max(0, ox), max(0, oy), min(W, ox + cw), min(H, oy + ch)
    if X1 > X0 and Y1 > Y0:
        canvas[Y0:Y1, X0:X1] = out_sprite[Y0 - oy:Y1 - oy, X0 - ox:X1 - ox]
    return canvas, drawn


def raster_text(lay: dict, ctx: RCtx, doc: Doc, deck: CopyDeck | None) -> tuple[np.ndarray, dict]:
    """The RGBA layer for a text layer (canvas-sized at the render scale) and its audit entry."""
    if "copy_id" in lay:
        if deck is None or lay["copy_id"] not in deck.lines:
            raise PosterError([f"text layer {lay['id']!r}: no approved copy for {lay['copy_id']!r}"])
        source, text = "copy_id", deck.text(lay["copy_id"])
    else:
        if doc.project or not doc.casual:
            raise PosterError([f"text layer {lay['id']!r}: literal text is not allowed here"])
        source, text = "literal", lay["text"]
    fonts = _fonts(ctx)
    lo, frame_w, _ = layout_text(lay, text, ctx, fonts)
    full, drawn = draw_text(lay, lo, frame_w, ctx)
    rebuilt = lo.reconstruct(drawn)
    entry = {"layer": lay["id"], "source": source, "copy_id": lay.get("copy_id"), "text": rebuilt,
             "text_exact": rebuilt == text, "drawn_lines": drawn, "font": lay.get("style", "caption"),
             "px": lo.px, "box": [lo.x0, lo.y0, frame_w, lo.box_h]}
    if ctx.scale != 1.0 or (ctx.w, ctx.h) != ctx.full:
        full = _downscale(full, ctx.w, ctx.h)
    return full, entry


def _downscale(rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    from .raster import _resize_rgba
    return np.asarray(_resize_rgba(Image.fromarray(rgba, "RGBA"), (w, h)))

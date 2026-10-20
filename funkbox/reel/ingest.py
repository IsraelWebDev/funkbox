"""Ingest: drop anything in, get a classified project.

Classification (first match wins): audio -> music/, video -> clips/, .ttf/.otf -> brand/fonts/,
"logo" in the name (or SVG) -> brand/, product candidate (name hint, real alpha, or a uniform border
when it is the only such image) -> product/, everything else -> shots/.

Files from the inbox are MOVED; files from any other path the user names are COPIED and the source is
never touched. Nothing is ever deleted.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from funkbox.media import font as font_, require_ffmpeg

from . import project as P
from .product import cutout as _cutout
from .product import srgb_to_lab

AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
VIDEO = {".mp4", ".mov", ".webm"}
FONT = {".ttf", ".otf"}
IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
MAX_SIDE = 2400
MAX_FILES = 400
PRODUCT_HINTS = ("product", "bottle", "pack")
BORDER_BAND = 0.04
BORDER_SHARE = 0.92
BORDER_DE = 12.0
CUTOUT_MIN_SCORE = 0.85            # below this the tool says so and suggests the GIMP route
HOLE_MAX = 0.02
STRENGTH = {"name": 3, "alpha": 2, "border": 1}


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
@dataclass
class Img:
    src: Path
    sha: str
    im: Image.Image                  # exif-transposed, downscaled (RGB or RGBA)
    changed: bool                    # transposed or downscaled (so the original is kept in _orig)
    name_hint: bool = False
    has_alpha: bool = False
    uniform: bool = False


def _load_image(p: Path) -> tuple[Image.Image, bool]:
    with Image.open(p) as raw:
        raw.load()
        orient = raw.getexif().get(0x0112, 1)
        im = ImageOps.exif_transpose(raw)
        changed = orient not in (1, None)
        has_a = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if has_a else "RGB")
    long = max(im.size)
    if long > MAX_SIDE:
        s = MAX_SIDE / long
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        changed = True
    return im, changed


def _real_alpha(im: Image.Image) -> bool:
    if im.mode != "RGBA":
        return False
    a = np.asarray(im.getchannel("A"))
    return float((a < 250).mean()) >= 0.005


def border_uniform_share(im: Image.Image) -> float:
    """Share of the 4%-wide border band within dE < 12 of the median border colour (Lab)."""
    rgb = im.convert("RGB")
    rgb.thumbnail((256, 256))
    a = np.asarray(rgb)
    h, w = a.shape[:2]
    bh, bw = max(1, round(BORDER_BAND * h)), max(1, round(BORDER_BAND * w))
    m = np.zeros((h, w), bool)
    m[:bh], m[-bh:], m[:, :bw], m[:, -bw:] = True, True, True, True
    lab = srgb_to_lab(a)[m]
    de = np.linalg.norm(lab - np.median(lab, axis=0), axis=1)
    return float((de < BORDER_DE).mean())


# --------------------------------------------------------------------------- #
# Cut-out quality
# --------------------------------------------------------------------------- #
def _ring(a: np.ndarray) -> bool:
    return bool(a[0].any() or a[-1].any() or a[:, 0].any() or a[:, -1].any())


def cutout_quality(rgba: Image.Image, src_size: tuple[int, int], touches_edge: bool) -> dict:
    """Score a product cut-out (``rgba`` is already cropped to the product): the share of the object's alpha
    that is cleanly 0/255 rather than semi-transparent mush, whether the subject touches the edge of the
    photo, and whether it has enclosed see-through holes (a white label cut away, say)."""
    a = np.asarray(rgba.getchannel("A"))
    opaque = int((a >= 251).sum())
    semi = int(((a > 4) & (a < 251)).sum())
    score = 1.0 - semi / max(1, semi + opaque)
    small = Image.fromarray(((a > 24).astype(np.uint8)) * 255)
    small.thumbnail((400, 400), Image.NEAREST)
    pad = Image.new("L", (small.width + 2, small.height + 2), 0)
    pad.paste(small, (1, 1))
    ImageDraw.floodfill(pad, (0, 0), 128)
    arr = np.asarray(pad)
    holes = float((arr == 0).sum()) / max(1, float((arr != 128).sum()))
    coverage = (opaque + semi) / max(1, src_size[0] * src_size[1])
    problems = []
    if opaque + semi == 0 or coverage < 0.01:
        problems.append("the cut-out is almost empty")
    if coverage > 0.97:
        problems.append("the background was not removed")
    if score < CUTOUT_MIN_SCORE:
        problems.append(f"edges are soft/semi-transparent (clean share {score:.2f} < {CUTOUT_MIN_SCORE})")
    if holes > HOLE_MAX:
        problems.append(f"{holes:.0%} of the product is see-through holes (white areas cut away?)")
    if touches_edge:
        problems.append("the subject touches the photo edge (it may be cropped)")
    return {"score": round(score, 3), "touches_edge": touches_edge, "holes": round(holes, 3),
            "coverage": round(coverage, 3), "ok": not problems, "problems": problems}


GIMP_ADVICE = ("Use the GIMP route: open product/original.* in GIMP (the funk-studio GIMP MCP can do it), remove "
               "the background (Select by Colour or Foreground Select), and export a PNG with alpha to "
               "product/product.png. Keep the label pixels untouched.")


def build_product_png(source_im: Image.Image, dest: Path, tmp_dir: Path) -> dict:
    """product.png from an image: keep real alpha, else the reel_cli `cutout` logic. Returns the quality dict."""
    src_size = source_im.size
    if _real_alpha(source_im):
        rgba = source_im.convert("RGBA")
        touches = _ring(np.asarray(rgba.getchannel("A")) > 24)
        method = "alpha"
    else:
        rgb = source_im.convert("RGB")
        arr = np.asarray(rgb, dtype=np.float32)
        border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
        dist = np.sqrt(((arr - np.median(border, axis=0)) ** 2).sum(axis=2))
        touches = _ring(dist > 28.0)
        tmp = tmp_dir / "_cutout_src.png"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(tmp)
        try:
            rgba = _cutout(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        method = "cutout"
    bbox = rgba.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
    q = cutout_quality(rgba, src_size, touches)
    dest.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(dest)
    q["method"] = method
    q["size"] = list(rgba.size)
    if not q["ok"]:
        q["advice"] = GIMP_ADVICE
    return q


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass
class Item:
    src: str
    dest: str | None
    kind: str
    note: str = ""


@dataclass
class IngestResult:
    project: str
    source: str
    moved: bool
    items: list[Item] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    unsupported: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    product: dict = field(default_factory=dict)
    contact_sheet: Path | None = None

    def manifest(self) -> dict:
        return {"project": self.project, "source": self.source, "moved": self.moved, "counts": self.counts,
                "product": self.product, "warnings": self.warnings, "duplicates": self.duplicates,
                "unsupported": [{"file": f, "why": w} for f, w in self.unsupported],
                "items": [{"from": i.src, "to": i.dest, "kind": i.kind, "note": i.note} for i in self.items],
                "contact_sheet": str(self.contact_sheet) if self.contact_sheet else None}

    def text(self) -> str:
        c = self.counts
        out = [f"ingested into {self.project!r} from {self.source} "
               f"({'moved' if self.moved else 'copied; the source is untouched'}): "
               + ", ".join(f"{k} {v}" for k, v in c.items() if v) or "nothing"]
        pr = self.product
        st = pr.get("status")
        if st == "set":
            q = pr.get("cutout", {})
            out.append(f"product: {pr['file']} (chosen by {pr['source']}); cut-out score {q.get('score')} "
                       f"[{'OK' if q.get('ok') else 'POOR'}], touches edge: {q.get('touches_edge')}, "
                       f"holes: {q.get('holes')}")
            for pb in q.get("problems", []):
                out.append(f"  cut-out problem: {pb}")
            if q.get("advice"):
                out.append("  " + q["advice"])
        elif st == "ambiguous":
            out.append("product: AMBIGUOUS, several equally likely candidates; do not guess. Ask the user which "
                       "is the product, then call set_product(project, file) with one of: "
                       + ", ".join(pr["candidates"]))
        elif st == "kept":
            out.append(f"product: unchanged ({pr['file']}); new product-like images were kept in product/alt/")
        else:
            out.append("product: none found. Add a product photo (name it product.jpg, or use a PNG with "
                       "transparency) or call set_product(project, file).")
        for i in self.items:
            out.append(f"  {i.kind:8s} {Path(i.src).name} -> {i.dest}" + (f"  ({i.note})" if i.note else ""))
        for d in self.duplicates:
            out.append(f"  duplicate (same sha256), skipped: {d}")
        for f, w in self.unsupported:
            out.append(f"  unsupported, left in place: {f} ({w})")
        for w in self.warnings:
            out.append(f"warning: {w}")
        if self.contact_sheet:
            out.append(f"contact sheet: {self.contact_sheet}")
        return "\n".join(out)


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
def _safe_name(stem: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "file"
    return s[:60]


def _unique(d: Path, stem: str, ext: str) -> Path:
    p = d / f"{stem}{ext}"
    k = 2
    while p.exists():
        p = d / f"{stem}-{k}{ext}"
        k += 1
    return p


def _gather(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    out: list[Path] = []
    for p in sorted(source.rglob("*")):
        rel = p.relative_to(source)
        if any(part.startswith(".") or part in ("__pycache__", "_orig") for part in rel.parts):
            continue
        if p.is_symlink() or not p.is_file():
            continue
        out.append(p)
        if len(out) > MAX_FILES:
            raise P.ProjectError(f"{source} holds more than {MAX_FILES} files; ingest a smaller folder")
    return out


def _put(src: Path, dest: Path, move: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(src, dest)


def _save_image(im: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ext = dest.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        im.convert("RGB").save(dest, quality=93, subsampling=0)
    elif ext == ".webp":
        im.save(dest, quality=93)
    else:
        im.save(dest)


def ingest(slug: str, source: str | Path | None = None, move: bool = True) -> IngestResult:
    d = P.project_dir(slug)
    meta = P.load_meta(slug)
    inbox = P.ensure_inbox()
    src_root = Path(source).expanduser() if source else inbox
    if not src_root.exists():
        raise P.ProjectError(f"source {src_root} does not exist")
    src_root = src_root.resolve()
    if src_root == d or d in src_root.parents:
        raise P.ProjectError("the source is inside the project itself; name the folder you dropped files in")
    from_inbox = src_root == inbox or inbox in src_root.parents
    do_move = bool(move) and from_inbox
    res = IngestResult(slug, str(src_root), do_move)
    if move and not from_inbox:
        res.warnings.append("external path: files were copied, never moved; the source is untouched")

    index: dict[str, str] = meta.setdefault("assets", {})
    seen: set[str] = set()
    todo: list[tuple[Path, str]] = []
    for f in _gather(src_root):
        sha = P.sha256_file(f)
        if sha in index or sha in seen:
            res.duplicates.append(f.name)
            continue
        seen.add(sha)
        todo.append((f, sha))

    imgs: list[Img] = []
    for f, sha in todo:
        ext = f.suffix.lower()
        lname = f.stem.lower()
        if ext in AUDIO:
            dest = _unique(d / "music", _safe_name(f.stem), ext)
            _put(f, dest, do_move)
            res.items.append(Item(str(f), str(dest.relative_to(d)), "music"))
            index[sha] = str(dest.relative_to(d))
        elif ext in VIDEO:
            dest = _unique(d / "clips", _safe_name(f.stem), ext)
            _put(f, dest, do_move)
            res.items.append(Item(str(f), str(dest.relative_to(d)), "clip"))
            index[sha] = str(dest.relative_to(d))
        elif ext in FONT:
            dest = _unique(d / "brand" / "fonts", _safe_name(f.stem), ext)
            _put(f, dest, do_move)
            res.items.append(Item(str(f), str(dest.relative_to(d)), "font"))
            index[sha] = str(dest.relative_to(d))
        elif ext == ".svg":
            dest = _unique(d / "brand", _safe_name(f.stem), ext)
            _put(f, dest, do_move)
            res.items.append(Item(str(f), str(dest.relative_to(d)), "logo", "SVG"))
            res.warnings.append(f"{f.name}: SVG logos are not rasterised here; convert it to PNG and drop it in "
                                f"the inbox again (it is not used until then)")
            index[sha] = str(dest.relative_to(d))
        elif ext in IMAGE:
            try:
                im, changed = _load_image(f)
            except Exception as e:  # noqa: BLE001
                res.unsupported.append((str(f), f"unreadable image: {type(e).__name__}"))
                continue
            if "logo" in lname:
                _ingest_logo(d, f, sha, im, changed, do_move, res, index)
                continue
            img = Img(f, sha, im, changed, name_hint=any(h in lname for h in PRODUCT_HINTS),
                      has_alpha=_real_alpha(im))
            if not img.has_alpha:
                img.uniform = border_uniform_share(im) >= BORDER_SHARE
            imgs.append(img)
        else:
            res.unsupported.append((str(f), f"unsupported type {ext or '(no extension)'}"))

    _ingest_images(d, slug, meta, imgs, do_move, res, index)

    P.save_meta(slug, meta)
    P.advance_status(slug, "ingested")
    kinds = ("music", "clip", "font", "logo", "product", "shot")
    res.counts = {k: sum(1 for i in res.items if i.kind == k) for k in kinds}
    res.counts["duplicates skipped"] = len(res.duplicates)
    res.counts["unsupported"] = len(res.unsupported)
    res.contact_sheet = contact_sheet(d, res)
    if not any(res.counts[k] for k in kinds):
        res.warnings.append("nothing was ingested" + (f"; the inbox {inbox} is empty" if from_inbox and not todo else ""))
    return res


def _ingest_logo(d: Path, f: Path, sha: str, im: Image.Image, changed: bool, do_move: bool, res: IngestResult,
                 index: dict) -> None:
    dest = d / "brand" / "logo.png"
    note = ""
    if dest.exists():
        dest = _unique(d / "brand", "logo", ".png")
        note = "a logo already existed; brand/logo.png is unchanged"
        res.warnings.append(f"{f.name}: there is already a brand/logo.png; this one is {dest.name} (unused)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGBA").save(dest)
    if do_move:
        f.unlink()
    index[sha] = str(dest.relative_to(d))
    res.items.append(Item(str(f), str(dest.relative_to(d)), "logo", note))


def _ingest_images(d: Path, slug: str, meta: dict, imgs: list[Img], do_move: bool, res: IngestResult,
                   index: dict) -> None:
    n_uniform = sum(1 for i in imgs if i.uniform)
    cands: list[tuple[int, str, Img]] = []
    for i in imgs:
        if i.name_hint:
            cands.append((STRENGTH["name"], "name", i))
        elif i.has_alpha:
            cands.append((STRENGTH["alpha"], "alpha", i))
        elif i.uniform and n_uniform == 1:
            cands.append((STRENGTH["border"], "border", i))
    chosen: Img | None = None
    why = ""
    tied: list[Img] = []
    already = bool(meta.get("product")) and (d / "product" / "product.png").exists()
    if cands:
        top = max(c[0] for c in cands)
        best = [c for c in cands if c[0] == top]
        if len(best) == 1:
            chosen, why = best[0][2], best[0][1]
        else:
            tied = [c[2] for c in best]
    cand_ids = {id(c[2]) for c in cands}

    def put_orig_and_processed(img: Img, dest: Path) -> None:
        if img.changed:
            _save_image(img.im, dest)
            orig = _unique(d / "shots" / "_orig", _safe_name(img.src.stem), img.src.suffix.lower())
            _put(img.src, orig, do_move)
        else:
            _put(img.src, dest, do_move)
        index[img.sha] = str(dest.relative_to(d))

    for img in imgs:
        if img is chosen and not already:
            _set_product_from(d, slug, meta, img, why, do_move, res, index)
        elif any(img is t for t in tied) and not already:
            dest = _unique(d / "product" / "candidates", _safe_name(img.src.stem), ".png")
            _save_image(img.im, dest)
            if do_move:
                img.src.unlink()
            index[img.sha] = str(dest.relative_to(d))
            res.items.append(Item(str(img.src), str(dest.relative_to(d)), "product", "candidate"))
        elif id(img) in cand_ids:
            dest = _unique(d / "product" / "alt", _safe_name(img.src.stem), ".png")
            _save_image(img.im, dest)
            if do_move:
                img.src.unlink()
            index[img.sha] = str(dest.relative_to(d))
            res.items.append(Item(str(img.src), str(dest.relative_to(d)), "product", "alternative, not used"))
        else:
            dest = _unique(d / "shots", _safe_name(img.src.stem), img.src.suffix.lower())
            put_orig_and_processed(img, dest)
            res.items.append(Item(str(img.src), str(dest.relative_to(d)), "shot",
                                  "downscaled/rotated; original in shots/_orig" if img.changed else ""))
    if already and cands:
        res.product = {"status": "kept", "file": "product/product.png"}
        res.warnings.append("the project already has a product; use set_product to replace it")
    elif chosen is not None:
        res.product = meta["product"] | {"status": "set"}
    elif tied:
        res.product = {"status": "ambiguous",
                       "candidates": sorted(str(x.relative_to(d)) for x in (d / "product" / "candidates").glob("*"))}
        meta["product"] = None
    elif meta.get("product") and (d / "product" / "product.png").exists():
        res.product = {"status": "kept", "file": "product/product.png"}
    else:
        res.product = {"status": "none"}
        if imgs:
            res.warnings.append("no product image detected; name the file product.jpg, use a PNG with transparency, "
                                "or call set_product")


def _set_product_from(d: Path, slug: str, meta: dict, img: Img, why: str, do_move: bool, res: IngestResult,
                      index: dict) -> None:
    ext = img.src.suffix.lower()
    orig = d / "product" / f"original{ext}"
    if orig.exists():
        orig = _unique(d / "product", "original", ext)
    _put(img.src, orig, do_move)
    q = build_product_png(img.im, d / "product" / "product.png", d / "out")
    index[img.sha] = str(orig.relative_to(d))
    meta["product"] = {"file": "product/product.png", "original": str(orig.relative_to(d)), "source": why,
                       "cutout": q}
    res.items.append(Item(str(img.src), "product/product.png", "product", f"chosen by {why}; original kept as {orig.name}"))


def set_product(slug: str, file: str) -> dict:
    """Make ``file`` (a project-relative path, or just a file name under product/ or shots/) the product."""
    d = P.project_dir(slug)
    meta = P.load_meta(slug)
    cand: Path | None = None
    f = str(file).replace("\\", "/")
    for rel in (f, f"product/candidates/{f}", f"product/alt/{f}", f"shots/{f}", f"product/{f}"):
        p = (d / rel).resolve()
        if d.resolve() in p.parents and p.is_file():
            cand = p
            break
    if cand is None:
        raise P.ProjectError(f"no file {file!r} in project {slug!r}; use a path like product/candidates/name.png "
                             f"or shots/name.jpg")
    if cand.suffix.lower() not in IMAGE:
        raise P.ProjectError(f"{cand.name} is not an image")
    im, _ = _load_image(cand)
    keep = d / "product" / f"original{cand.suffix.lower()}"
    if not keep.exists() and cand.resolve() != keep.resolve():
        shutil.copy2(cand, keep)
    q = build_product_png(im, d / "product" / "product.png", d / "out")
    meta["product"] = {"file": "product/product.png", "original": str(keep.relative_to(d)), "source": "manual",
                       "cutout": q}
    P.save_meta(slug, meta)
    return meta["product"]


# --------------------------------------------------------------------------- #
# Contact sheet
# --------------------------------------------------------------------------- #
def _video_frame(p: Path, size: int) -> Image.Image | None:
    try:
        ff = require_ffmpeg()
        r = subprocess.run([ff, "-loglevel", "error", "-ss", "0.3", "-i", str(p), "-frames:v", "1", "-f", "image2pipe",
                            "-vcodec", "png", "-"], capture_output=True, timeout=20)
        if r.returncode == 0 and r.stdout:
            return Image.open(io.BytesIO(r.stdout)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    return None


def contact_sheet(d: Path, res: IngestResult, tile: int = 220) -> Path | None:
    """Thumbnails of everything ingested, each labelled with its destination."""
    rows = [i for i in res.items if i.dest]
    if not rows:
        return None
    cols = min(5, len(rows))
    nrows = -(-len(rows) // cols)
    fs = 13
    lab_h = 2 * (fs + 4) + 6
    pad = 10
    sheet = Image.new("RGB", (cols * (tile + pad) + pad, nrows * (tile + lab_h + pad) + pad), (24, 24, 28))
    dr = ImageDraw.Draw(sheet)
    font = font_(fs, bold=False)
    for k, it in enumerate(rows):
        x, y = pad + (k % cols) * (tile + pad), pad + (k // cols) * (tile + lab_h + pad)
        p = d / it.dest
        t = Image.new("RGB", (tile, tile), (52, 54, 62))
        thumb = None
        ext = p.suffix.lower()
        if ext in IMAGE:
            try:
                thumb = Image.open(p)
                thumb.load()
                if thumb.mode in ("RGBA", "LA", "P"):
                    bg = Image.new("RGB", thumb.size, (200, 200, 200))
                    for yy in range(0, thumb.height, 24):             # checkerboard shows transparency
                        for xx in range(0, thumb.width, 24):
                            if (xx // 24 + yy // 24) % 2:
                                ImageDraw.Draw(bg).rectangle((xx, yy, xx + 23, yy + 23), fill=(150, 150, 150))
                    rgba = thumb.convert("RGBA")
                    bg.paste(rgba, mask=rgba.getchannel("A"))
                    thumb = bg
                else:
                    thumb = thumb.convert("RGB")
            except Exception:  # noqa: BLE001
                thumb = None
        elif ext in VIDEO:
            thumb = _video_frame(p, tile)
        elif ext in FONT:
            try:
                f = ImageFont.truetype(str(p), 96)
                ImageDraw.Draw(t).text((tile // 2, tile // 2), "Aa", font=f, fill=(240, 240, 240), anchor="mm")
            except OSError:
                pass
        if thumb is not None:
            thumb.thumbnail((tile, tile))
            t.paste(thumb, ((tile - thumb.width) // 2, (tile - thumb.height) // 2))
        elif ext in AUDIO:
            ImageDraw.Draw(t).text((tile // 2, tile // 2), "AUDIO", font=font_(28, bold=False), fill=(240, 240, 240), anchor="mm")
        sheet.paste(t, (x, y))
        dr.text((x + 2, y + tile + 4), it.kind.upper() + (f" ({it.note.split(';')[0]})" if it.note and it.kind == "product" else ""),
                font=font, fill=(235, 235, 235))
        dr.text((x + 2, y + tile + 4 + fs + 4), "-> " + it.dest[-30:], font=font, fill=(160, 170, 190))
    out = d / "out" / "ingest-contact.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out

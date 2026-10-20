#!/usr/bin/env python3
"""
funk - CPU-only procedural art machine.

Renders perfectly seamless loops (MP4 / GIF / PNG) from math, not models:
no GPU, no accounts, no API keys, no credits. numpy + Pillow + ffmpeg.

    python funk.py list
    python funk.py render --preset liquid-disco --seed 7 --text "FUNK WAS HERE"
    python funk.py sheet  --preset hypno-tunnel --seeds 1-9
    python funk.py render --random --seed 42 --set kaleido=8 chroma=6

Every render writes a sidecar <name>.json with the exact recipe + settings,
so anything you like can be reproduced with  --recipe <name>.json.

Why loops are seamless: time enters every effect only as theta = 2*pi*t,
always multiplied by an integer. Frame N is therefore identical to frame 0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, fields, replace
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

TAU = 2.0 * math.pi
f32 = np.float32

# --------------------------------------------------------------------------- #
# Palettes: Inigo Quilez cosine palettes  col = a + b*cos(2pi*(c*x + d))
# Time is added *outside* c, as an integer number of cycles, so loops stay exact.
# --------------------------------------------------------------------------- #
PALETTES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "acid":   ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), (0.00, 0.33, 0.67)),
    "vapor":  ((0.6, 0.4, 0.7), (0.4, 0.4, 0.3), (1.0, 1.0, 1.0), (0.00, 0.45, 0.25)),
    "sunset": ((0.5, 0.3, 0.3), (0.5, 0.4, 0.3), (1.0, 1.0, 1.0), (0.00, 0.10, 0.25)),
    "toxic":  ((0.4, 0.6, 0.3), (0.4, 0.4, 0.4), (1.0, 1.0, 1.0), (0.30, 0.10, 0.60)),
    "candy":  ((0.8, 0.5, 0.6), (0.3, 0.5, 0.4), (1.0, 1.0, 1.0), (0.00, 0.30, 0.55)),
    "ice":    ((0.4, 0.6, 0.8), (0.3, 0.3, 0.3), (1.0, 1.0, 1.0), (0.55, 0.60, 0.65)),
    "mono":   ((0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0), (0.00, 0.00, 0.00)),
}

# --------------------------------------------------------------------------- #
# Effects: (u, v, theta, params) -> field x, or (x, shade).
# u, v are centred coords, short side spans [-1, 1].
# --------------------------------------------------------------------------- #
FieldFn = Callable[[np.ndarray, np.ndarray, float, dict], "np.ndarray | tuple[np.ndarray, np.ndarray]"]


@dataclass(frozen=True)
class Effect:
    fn: FieldFn
    sample: Callable[[np.random.Generator], dict]
    blurb: str


EFFECTS: dict[str, Effect] = {}


def effect(name: str, blurb: str, sample: Callable[[np.random.Generator], dict]):
    def deco(fn: FieldFn) -> FieldFn:
        EFFECTS[name] = Effect(fn, sample, blurb)
        return fn
    return deco


@effect("plasma", "classic demoscene sine soup",
        lambda r: {"k": r.uniform(4.0, 10.0, 4).round(3).tolist()})
def _plasma(u, v, th, p):
    k = p["k"]
    cx, cy = 0.6 * math.cos(th), 0.6 * math.sin(2 * th)
    f = (np.sin(k[0] * u + th)
         + np.sin(k[1] * v - 2 * th)
         + np.sin(k[2] * (u + v) + th)
         + np.sin(k[3] * np.hypot(u - cx, v - cy) * 2 - 3 * th))
    return f * 0.3 + 0.5


@effect("moire", "two orbiting ring emitters interfering",
        lambda r: {"k": float(r.uniform(8, 16)), "ph": float(r.uniform(0, TAU))})
def _moire(u, v, th, p):
    c1 = (0.45 * math.cos(th), 0.45 * math.sin(th))
    c2 = (0.45 * math.cos(-2 * th + p["ph"]), 0.45 * math.sin(-2 * th + p["ph"]))
    r1 = np.hypot(u - c1[0], v - c1[1])
    r2 = np.hypot(u - c2[0], v - c2[1])
    bands = np.sign(np.sin(p["k"] * r1) * np.sin(p["k"] * r2))
    return 0.3 * (r1 + r2) + 0.22 * bands


@effect("warp", "domain-warped liquid stripes",
        lambda r: {"amp": float(r.uniform(0.25, 0.55)), "freq": float(r.uniform(2.0, 4.5)),
                   "stripes": float(r.uniform(6, 14)), "iters": int(r.integers(2, 4))})
def _warp(u, v, th, p):
    a, f = p["amp"], p["freq"]
    for i in range(p["iters"]):
        u, v = (u + a * np.sin(f * v + (i + 1) * th),
                v + a * np.cos(f * u - (i + 1) * th))
    return 0.5 + 0.35 * np.sin(p["stripes"] * (u + v)) + 0.12 * np.hypot(u, v)


@effect("tunnel", "infinite checkered wormhole",
        lambda r: {"n": float(r.uniform(1.5, 3.5)), "m": int(r.integers(4, 13))})
def _tunnel(u, v, th, p):
    r = np.hypot(u, v).astype(np.float64) + 1e-3
    a = np.arctan2(v, u)
    depth = p["n"] / r
    # Reduce the phase before sin(): near the centre depth is huge, and float32
    # rounding there would flip signs between t=0 and t=1, breaking the loop.
    # m is an integer, so the atan2 branch cut is invisible. The 0.37 offset keeps
    # sign() edges off the exact symmetry diagonals, where sin(0) vs sin(2pi)=-2e-16
    # would flip pixels between the first and last frame.
    s = np.sin(TAU * np.mod(depth, 1.0) + 2 * th) * np.sin(p["m"] * a + th + 0.37)
    return 0.05 * depth + 0.25 * np.sign(s), np.clip(r * 1.6, 0.0, 1.0)


@effect("blobs", "lava-lamp metaballs on Lissajous orbits",
        lambda r: {"balls": [[float(r.uniform(0.3, 0.8)), float(r.uniform(0.3, 0.8)),
                              int(r.integers(1, 4)), int(r.integers(1, 4)),
                              float(r.uniform(0, TAU))] for _ in range(int(r.integers(4, 8)))]})
def _blobs(u, v, th, p):
    f = np.zeros_like(u)
    for ax, ay, fx, fy, ph in p["balls"]:
        cx, cy = ax * math.sin(fx * th + ph), ay * math.cos(fy * th + ph)
        f += 0.05 / ((u - cx) ** 2 + (v - cy) ** 2 + 1e-3)
    lf = np.log1p(f)
    return 0.35 * lf + 0.08 * np.sin(9.0 * lf)


@effect("rings", "op-art concentric pulse with angular wobble",
        lambda r: {"k": float(r.uniform(28, 50)), "lobes": int(r.integers(3, 9)),
                   "wob": float(r.uniform(0.05, 0.2))})
def _rings(u, v, th, p):
    r = np.hypot(u, v)
    a = np.arctan2(v, u)
    rr = r + p["wob"] * np.sin(p["lobes"] * a + 2 * th) * r
    return 0.5 + 0.5 * np.sign(np.sin(p["k"] * rr - 3 * th)) * 0.4 + 0.25 * r


# --------------------------------------------------------------------------- #
# Recipe = everything that defines the look. Render settings are separate.
# --------------------------------------------------------------------------- #
@dataclass
class Recipe:
    effect: str = "plasma"
    palette: str = "acid"
    cycles: int = 1          # full palette rotations per loop (integer -> seamless)
    spin: int = 0            # full rotations per loop (integer -> seamless)
    zoom: float = 0.0        # breathing zoom amplitude
    kaleido: int = 0         # mirror segments, 0 = off
    posterize: int = 0       # colour levels, 0 = off
    halftone: int = 0        # dot cell size px, 0 = off
    chroma: int = 0          # RGB split px
    scanlines: bool = False
    vignette: float = 0.35
    grain: float = 0.0
    text: str = ""
    text_size: float = 0.13  # fraction of image height
    text_wobble: float = 0.0 # px, per-letter wave
    text_color: str = "#ffffff"
    shadow: str = "#ff2bd6"
    params: dict | None = None  # effect params; None -> sampled from seed


@dataclass
class Settings:
    width: int = 540
    height: int = 540
    fps: int = 30
    seconds: float = 4.0
    seed: int = 1

    @property
    def frames(self) -> int:
        return max(1, round(self.fps * self.seconds))


PRESETS: dict[str, Recipe] = {
    "acid-plasma":  Recipe("plasma", "acid", cycles=1, chroma=4, scanlines=True, grain=0.04),
    "pop-moire":    Recipe("moire", "candy", cycles=1, halftone=11, vignette=0.2),
    "liquid-disco": Recipe("warp", "vapor", cycles=1, spin=1, kaleido=6, chroma=3, grain=0.03),
    "hypno-tunnel": Recipe("tunnel", "toxic", cycles=2, spin=1, scanlines=True, chroma=2),
    "lava-lamp":    Recipe("blobs", "sunset", cycles=1, posterize=7, grain=0.05, vignette=0.5),
    "op-art":       Recipe("rings", "mono", cycles=0, kaleido=0, chroma=5, vignette=0.15),
    "frozen-kaleido": Recipe("plasma", "ice", cycles=1, spin=1, kaleido=8, posterize=9, chroma=2),
    "party-poster": Recipe("warp", "candy", cycles=1, kaleido=8, halftone=7, chroma=4,
                           text="FUNK", text_wobble=8, shadow="#00e5ff"),
}


def resolve(recipe: Recipe, seed: int) -> Recipe:
    """Fill in seeded effect params so the recipe is fully reproducible."""
    if recipe.effect not in EFFECTS:
        raise SystemExit(f"unknown effect {recipe.effect!r}; have {sorted(EFFECTS)}")
    if recipe.palette not in PALETTES:
        raise SystemExit(f"unknown palette {recipe.palette!r}; have {sorted(PALETTES)}")
    if recipe.params is None:
        recipe = replace(recipe, params=EFFECTS[recipe.effect].sample(np.random.default_rng(seed)))
    return recipe


def random_recipe(seed: int) -> Recipe:
    r = np.random.default_rng(seed ^ 0x5EED)
    return Recipe(
        effect=str(r.choice(sorted(EFFECTS))),
        palette=str(r.choice([p for p in PALETTES if p != "mono"])),
        cycles=int(r.integers(1, 3)),
        spin=int(r.choice([0, 0, 1, -1])),
        zoom=float(r.choice([0.0, 0.15])),
        kaleido=int(r.choice([0, 0, 4, 6, 8])),
        posterize=int(r.choice([0, 0, 6, 9])),
        halftone=int(r.choice([0, 0, 0, 8])),
        chroma=int(r.integers(0, 6)),
        scanlines=bool(r.random() < 0.3),
        grain=float(r.choice([0.0, 0.03])),
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def _grid(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    s = 2.0 / min(w, h)
    x = (np.arange(w, dtype=f32) - (w - 1) / 2) * s
    y = (np.arange(h, dtype=f32) - (h - 1) / 2) * s
    return np.meshgrid(x, y)


def _transform(u, v, th, rc: Recipe):
    if rc.zoom:
        z = 1.0 + rc.zoom * math.sin(th)
        u, v = u * z, v * z
    if rc.spin:
        c, s = math.cos(rc.spin * th), math.sin(rc.spin * th)
        u, v = c * u - s * v, s * u + c * v
    if rc.kaleido >= 2:
        r = np.hypot(u, v)
        seg = TAU / rc.kaleido
        a = np.mod(np.arctan2(v, u), seg)
        a = np.abs(a - seg / 2)
        u, v = r * np.cos(a), r * np.sin(a)
    return u, v


def _palette(x: np.ndarray, t: float, rc: Recipe) -> np.ndarray:
    a, b, c, d = (np.asarray(q, dtype=f32) for q in PALETTES[rc.palette])
    # mod before adding time: keeps float32 precision when x is large (tunnel centre)
    phase = np.mod(c * x[..., None] + d, 1.0) + rc.cycles * t
    return a + b * np.cos(TAU * phase)


def _halftone(img: np.ndarray, cell: int) -> np.ndarray:
    h, w, _ = img.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cy = np.minimum((yy // cell) * cell + cell // 2, h - 1)
    cx = np.minimum((xx // cell) * cell + cell // 2, w - 1)
    centre = img[cy, cx]
    lum = centre @ np.array([0.299, 0.587, 0.114], dtype=f32)
    radius = cell * 0.62 * np.sqrt(np.clip(lum, 0, 1))
    dot = np.hypot(yy - cy, xx - cx) < radius
    return np.where(dot[..., None], centre * 1.15, centre * 0.12)


def _post(img: np.ndarray, th: float, frame: int, st: Settings, rc: Recipe) -> np.ndarray:
    if rc.posterize >= 2:
        img = np.floor(np.clip(img, 0, 1) * rc.posterize) / (rc.posterize - 1)
    if rc.halftone >= 3:
        img = _halftone(img, rc.halftone)
    if rc.chroma:
        s = int(round(rc.chroma * (1.0 + 0.5 * math.sin(2 * th))))
        img = img.copy()
        img[..., 0] = np.roll(img[..., 0], s, axis=1)
        img[..., 2] = np.roll(img[..., 2], -s, axis=1)
    if rc.scanlines:
        img = img.copy()
        img[1::3] *= 0.72
    if rc.vignette:
        u, v = _grid(st.width, st.height)
        img = img * (1.0 - rc.vignette * np.clip(np.hypot(u, v) - 0.4, 0, None))[..., None]
    if rc.grain:
        g = np.random.default_rng(st.seed * 1_000_003 + frame).normal(0, rc.grain, img.shape[:2])
        img = img + g[..., None].astype(f32)
    return img


@lru_cache(maxsize=8)
def _font(px: int) -> ImageFont.ImageFont:
    for name in ("arialbd.ttf", "Impact.ttf", "impact.ttf", "DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def _text(im: Image.Image, th: float, rc: Recipe) -> Image.Image:
    w, h = im.size
    draw = ImageDraw.Draw(im)
    lines = rc.text.split("\\n") if "\\n" in rc.text else rc.text.split("\n")
    px = max(8, int(h * rc.text_size))
    widest = max(draw.textlength(ln, font=_font(px)) for ln in lines)
    if widest > 0.86 * w:  # auto-fit long text instead of clipping it
        px = max(8, int(px * 0.86 * w / widest))
    font = _font(px)
    line_h = font.size * 1.15
    y0 = h / 2 - line_h * len(lines) / 2
    off = max(2, font.size // 12)
    for li, line in enumerate(lines):
        total = draw.textlength(line, font=font)
        x = (w - total) / 2
        for i, ch in enumerate(line):
            dy = rc.text_wobble * math.sin(2 * th + i * 0.7 + li)
            y = y0 + li * line_h + dy
            draw.text((x + off, y + off), ch, font=font, fill=rc.shadow)
            draw.text((x, y), ch, font=font, fill=rc.text_color,
                      stroke_width=max(1, font.size // 22), stroke_fill="#000000")
            x += draw.textlength(ch, font=font)
    return im


def render_frame(i: int, st: Settings, rc: Recipe) -> np.ndarray:
    t = i / st.frames
    th = TAU * t
    u, v = _transform(*_grid(st.width, st.height), th, rc)
    out = EFFECTS[rc.effect].fn(u, v, th, rc.params or {})
    x, shade = out if isinstance(out, tuple) else (out, None)
    img = _palette(x.astype(f32), t, rc)
    if shade is not None:
        img = img * shade[..., None]
    img = _post(img, th, i, st, rc)
    arr = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
    if rc.text:
        arr = np.asarray(_text(Image.fromarray(arr), th, rc))
    return arr


# Pool plumbing (top-level so it pickles under Windows' spawn start method)
_W: tuple[Settings, Recipe] | None = None


def _init(st: Settings, rc: Recipe) -> None:
    global _W
    _W = (st, rc)


def _work(i: int) -> bytes:
    assert _W is not None
    return render_frame(i, *_W).tobytes()


def ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # pip install imageio-ffmpeg -> bundled static ffmpeg, no install needed
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _frames(st: Settings, rc: Recipe, workers: int) -> Iterable[bytes]:
    if workers <= 1:
        _init(st, rc)
        yield from map(_work, range(st.frames))
        return
    with Pool(workers, initializer=_init, initargs=(st, rc)) as pool:
        yield from pool.imap(_work, range(st.frames), chunksize=2)


def render(rc: Recipe, st: Settings, out_dir: Path, name: str, *, mp4: bool = True,
           gif: bool = True, png: bool = True, gif_width: int = 400,
           workers: int | None = None) -> dict[str, str]:
    """Render a loop. Returns {kind: path}."""
    st.width -= st.width % 2
    st.height -= st.height % 2
    rc = resolve(rc, st.seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / name
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    paths: dict[str, str] = {}

    if png:
        Image.fromarray(render_frame(0, st, rc)).save(f"{base}.png")
        paths["png"] = f"{base}.png"

    ff = ffmpeg_exe()
    if (mp4 or gif) and ff:
        mp4_path = f"{base}.mp4"
        cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{st.width}x{st.height}", "-r", str(st.fps), "-i", "-",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
               "-preset", "medium", "-movflags", "+faststart", mp4_path]
        # stdout -> DEVNULL: under an MCP stdio server, stray bytes on stdout corrupt the protocol
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        assert proc.stdin
        try:
            for buf in _frames(st, rc, workers):
                proc.stdin.write(buf)
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg failed while encoding mp4")
        paths["mp4"] = mp4_path
        if gif:
            gw = min(gif_width, st.width)
            gfps = min(st.fps, 20)  # GIF is the bloated format; keep it lean
            vf = (f"fps={gfps},scale={gw}:-1:flags=lanczos,split[a][b];"
                  "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3")
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", mp4_path, "-vf", vf,
                            "-loop", "0", f"{base}.gif"], check=True, stdout=subprocess.DEVNULL)
            paths["gif"] = f"{base}.gif"
        if not mp4:
            os.remove(mp4_path)
            paths.pop("mp4")
    elif gif:  # no ffmpeg anywhere: Pillow fallback, slower and grainier
        print("ffmpeg not found; writing GIF with Pillow (pip install imageio-ffmpeg for MP4)",
              file=sys.stderr)
        ims = [Image.frombytes("RGB", (st.width, st.height), b) for b in _frames(st, rc, workers)]
        ims[0].save(f"{base}.gif", save_all=True, append_images=ims[1:], loop=0,
                    duration=round(1000 / st.fps), optimize=True)
        paths["gif"] = f"{base}.gif"

    sidecar = {"recipe": asdict(rc), "settings": asdict(st), "outputs": paths}
    Path(f"{base}.json").write_text(json.dumps(sidecar, indent=2))
    paths["json"] = f"{base}.json"
    return paths


def contact_sheet(rc: Recipe | None, st: Settings, seeds: list[int], path: Path, cols: int = 3,
                  t: float = 0.25) -> str:
    """Grid of stills, one per seed, labelled - for picking the seed you like.
    rc=None rolls a whole random recipe per seed (pair with --random)."""
    tiles = []
    for s in seeds:
        r = resolve(random_recipe(s) if rc is None else replace(rc, params=None), s)
        sst = replace(st, seed=s)
        frame = render_frame(round(t * sst.frames) % sst.frames, sst, r)
        im = Image.fromarray(frame)
        ImageDraw.Draw(im).text((8, 6), f"seed {s}", font=_font(max(12, st.height // 18)),
                                fill="#ffffff", stroke_width=2, stroke_fill="#000000")
        tiles.append(im)
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * st.width, rows * st.height), "#000")
    for k, im in enumerate(tiles):
        sheet.paste(im, ((k % cols) * st.width, (k // cols) * st.height))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return str(path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _coerce(raw: str, typ):
    if typ in (bool, "bool"):
        return raw.lower() in ("1", "true", "yes", "on")
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def apply_overrides(rc: Recipe, pairs: list[str]) -> Recipe:
    known = {f.name: f.type for f in fields(Recipe)}
    changes = {}
    for pair in pairs:
        key, _, val = pair.partition("=")
        if key not in known:
            raise SystemExit(f"unknown recipe key {key!r}; have {sorted(known)}")
        changes[key] = _coerce(val, known[key])
    return replace(rc, **changes)


def build_recipe(args) -> tuple[Recipe, Settings]:
    st = Settings(width=args.width or args.size, height=args.height or args.size,
                  fps=args.fps, seconds=args.seconds, seed=args.seed)
    if args.recipe:
        data = json.loads(Path(args.recipe).read_text())
        rc = Recipe(**data.get("recipe", data))
        if "settings" in data and not args.keep_settings_from_cli:
            st = Settings(**{**data["settings"]})
    elif args.random:
        rc = random_recipe(args.seed)
    else:
        if args.preset not in PRESETS:
            raise SystemExit(f"unknown preset {args.preset!r}; try: python funk.py list")
        rc = replace(PRESETS[args.preset])
    if args.text is not None:
        rc = replace(rc, text=args.text, text_wobble=rc.text_wobble or 6.0)
    rc = apply_overrides(rc, args.set or [])
    return rc, st


def parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b) + 1) if b else [int(a)])
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="funk", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show presets, effects and palettes")

    def common(p):
        p.add_argument("--preset", default="liquid-disco")
        p.add_argument("--random", action="store_true", help="roll a random recipe from --seed")
        p.add_argument("--recipe", help="load a sidecar .json to reproduce a render")
        p.add_argument("--keep-settings-from-cli", action="store_true",
                       help="with --recipe: ignore its settings, use CLI size/fps/seconds")
        p.add_argument("--seed", type=int, default=1)
        p.add_argument("--size", type=int, default=540)
        p.add_argument("--width", type=int)
        p.add_argument("--height", type=int)
        p.add_argument("--fps", type=int, default=30)
        p.add_argument("--seconds", type=float, default=4.0)
        p.add_argument("--text")
        p.add_argument("--set", nargs="*", metavar="KEY=VAL",
                       help="override recipe fields, e.g. kaleido=8 palette=toxic chroma=6")
        p.add_argument("--out", default="funk-out")

    r = sub.add_parser("render", help="render a seamless loop")
    common(r)
    r.add_argument("--name")
    r.add_argument("--no-mp4", action="store_true")
    r.add_argument("--no-gif", action="store_true")
    r.add_argument("--no-png", action="store_true")
    r.add_argument("--gif-width", type=int, default=400)
    r.add_argument("--workers", type=int)

    s = sub.add_parser("sheet", help="contact sheet of stills across seeds")
    common(s)
    s.add_argument("--seeds", default="1-9")
    s.add_argument("--cols", type=int, default=3)

    args = ap.parse_args(argv)

    if args.cmd == "list":
        print("PRESETS")
        for k, v in PRESETS.items():
            extras = [f"{f}={getattr(v, f)}" for f in ("kaleido", "halftone", "posterize", "chroma")
                      if getattr(v, f)]
            print(f"  {k:<15} {v.effect}/{v.palette}  {' '.join(extras)}")
        print("\nEFFECTS")
        for k, e in EFFECTS.items():
            print(f"  {k:<15} {e.blurb}")
        print("\nPALETTES\n  " + "  ".join(PALETTES))
        print("\nRECIPE KEYS (use with --set)\n  " + "  ".join(f.name for f in fields(Recipe)))
        return

    rc, st = build_recipe(args)
    label = "random" if args.random else (Path(args.recipe).stem if args.recipe else args.preset)

    if args.cmd == "sheet":
        st = replace(st, width=min(st.width, 360), height=min(st.height, 360))
        path = contact_sheet(None if args.random else rc, st, parse_seeds(args.seeds),
                             Path(args.out) / f"{label}-sheet.png",
                             cols=args.cols)
        print(path)
        return

    name = args.name or f"{label}-s{st.seed}"
    t0 = time.perf_counter()
    paths = render(rc, st, Path(args.out), name, mp4=not args.no_mp4, gif=not args.no_gif,
                   png=not args.no_png, gif_width=args.gif_width, workers=args.workers)
    dt = time.perf_counter() - t0
    print(f"{st.frames} frames {st.width}x{st.height} in {dt:.1f}s "
          f"({st.frames / dt:.1f} fps)")
    for k, p in paths.items():
        print(f"  {k:<4} {p}")


if __name__ == "__main__":
    main()

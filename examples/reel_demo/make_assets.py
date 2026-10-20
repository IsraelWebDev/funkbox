#!/usr/bin/env python3
"""Generate the synthetic demo assets for the reel engine (no external files needed).

    python examples/reel_demo/make_assets.py [--size 1080x1920] [--dest examples/reel_demo]

Writes under <dest>/inputs/: gradient+shape shots (rendered with the funk2 GPU engine), a clip,
a fake bottle PNG with alpha and the printed text "DEMO-123", a logo, a 120 BPM click track,
copy.json, a text-free funk2 recipe, and <dest>/demo.reel.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

COPY = {
    "brand_name": "GLOWMOP",
    "lines": {
        "hook": {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?", "say": "Look closer. Time for a clean?"},
        "tackle": {"text": "TACKLE CARPET CLEANING WITH GLOWMOP.", "say": "Tackle carpet cleaning with GLOWMOP."},
        "care": {"text": "GIVE YOUR CARPET THE ATTENTION IT NEEDS."},
        "fresh": {"text": "FRESH FIBRES, EVERY TIME."},
        "tough": {"text": "TOUGH ON STAINS."},
        "gentle": {"text": "GENTLE ON FAMILY LIFE."},
        "end_head": {"text": "Deep\nCleaning\nPower"},
        "end_sub": {"text": "PROFESSIONAL RESULTS"},
        "url": {"text": "GLOWMOP.EXAMPLE"},
    },
    "pronounce": {"GLOWMOP": "glow mop"},
}

SHADERS = [("warp", "vapor"), ("plasma", "sunset"), ("blobs", "vapor"), ("voronoi_pop", "sunset"),
           ("fbm_flow", "vapor"), ("moire", "sunset"), ("rings", "vapor"), ("tunnel", "sunset")]


def _font(px: int):
    try:
        return ImageFont.truetype(FONT_BOLD, px)
    except OSError:
        return ImageFont.load_default(size=px)


def make_shots(dest: Path, w: int, h: int) -> list[str]:
    """Gradient/shader backgrounds with a few flat shapes on top (no text)."""
    from funkgl.engine import Engine
    from funkgl.recipe import Recipe2
    out = []
    eng = Engine(w, h)
    try:
        for i, (shader, pal) in enumerate(SHADERS):
            rec = Recipe2.from_dict({
                "version": 2, "settings": {"width": w, "height": h, "fps": 30, "seconds": 1, "seed": 3 + i},
                "palette": {"name": pal, "cycles": 1},
                "layers": [{"type": "shader", "shader": f"shaders/{shader}.frag"}],
                "post": [{"name": "vignette", "amount": 0.35}]})
            im = Image.fromarray(eng.render(rec, 0))
            d = ImageDraw.Draw(im, "RGBA")
            rng = np.random.default_rng(100 + i)
            for _ in range(5):                       # flat shapes so parallax/Ken Burns show motion
                cx, cy = rng.uniform(0.1, 0.9) * w, rng.uniform(0.1, 0.9) * h
                r = rng.uniform(0.04, 0.12) * w
                col = tuple(int(c) for c in rng.integers(40, 255, 3)) + (170,)
                if rng.random() < 0.5:
                    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
                else:
                    d.rectangle((cx - r, cy - r * 0.7, cx + r, cy + r * 0.7), fill=col)
            p = dest / "inputs" / "shots" / f"shot{i + 1}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            im.save(p)
            out.append(p.relative_to(dest).as_posix())
    finally:
        eng.release()
    return out


def make_bottle(path: Path, w: int = 300, h: int = 700) -> None:
    """A fake product: RGBA bottle with a flat-colour label that prints DEMO-123."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    body = (32, 130, 190, 255)
    d.rounded_rectangle((20, 190, w - 20, h - 8), radius=40, fill=body)                 # body
    d.rounded_rectangle((70, 90, w - 70, 220), radius=30, fill=body)                    # shoulder
    d.rectangle((105, 40, w - 105, 100), fill=body)                                     # neck
    d.rounded_rectangle((90, 4, w - 90, 56), radius=10, fill=(235, 235, 235, 255))      # cap
    d.rectangle((20, 330, w - 20, 600), fill=(250, 250, 245, 255))                      # label
    d.rectangle((20, 330, w - 20, 372), fill=(200, 16, 46, 255))                        # red band
    f = _font(40)
    tw = d.textlength("DEMO-123", font=f)
    d.text(((w - tw) / 2, 440), "DEMO-123", font=f, fill=(20, 20, 20, 255))
    d.rectangle((60, 520, w - 60, 540), fill=(32, 130, 190, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def make_logo(path: Path) -> None:
    im = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((10, 10, 390, 390), fill=(255, 255, 255, 255))
    d.ellipse((50, 50, 350, 350), fill=(200, 16, 46, 255))
    d.polygon([(130, 130), (270, 130), (200, 290)], fill=(255, 255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def click_track(path: Path, bpm: float = 120.0, seconds: float = 30.0, sr: int = 44100,
                pad: bool = True) -> None:
    """Metronome clicks (decaying 1.2 kHz bursts) on every beat, optionally over a quiet bass pad."""
    n = int(sr * seconds)
    x = np.zeros(n, dtype=np.float64)
    period = 60.0 / bpm
    burst = np.arange(int(0.03 * sr)) / sr
    click = np.sin(2 * math.pi * 1200 * burst) * np.exp(-burst * 140)
    k = 0
    while k * period < seconds - 0.05:
        i = int(round(k * period * sr))
        x[i:i + len(click)] += 0.8 * click[: n - i]
        k += 1
    if pad:
        t = np.arange(n) / sr
        x += 0.05 * np.sin(2 * math.pi * 110 * t) * (0.6 + 0.4 * np.sin(2 * math.pi * t / (4 * period)))
    x = np.clip(x, -1, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((x * 32767).astype("<i2").tobytes())


def make_clip(path: Path, w: int, h: int, seconds: float = 3.0) -> None:
    import funk
    ff = funk.ffmpeg_exe()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"mandelbrot=size={w}x{h}:rate=30", "-t", str(seconds), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "24", str(path)], check=True)


def demo_reel(shots: list[str]) -> dict:
    """The demo shot list (paths relative to the reel file's folder)."""
    def cap(cid, reveal="typewriter", anchor="bottom", style="red-box"):
        return {"copy_id": cid, "style": style, "reveal": reveal, "anchor": anchor}
    # v3.1: captions and the product must not collide (validator rule): the product sits low, captions go on top
    prod = lambda x=0.5, y=0.78, hgt=0.38, **k: {"image": "inputs/brand/bottle.png", "x": x, "y": y,  # noqa: E731
                                              "height": hgt, "depth": 0.85, "shadow": 0.6, "match_color": 0.5, **k}
    img = lambda i: {"image": shots[i % len(shots)]}  # noqa: E731
    kb = lambda a, b, pan=((0, 0), (0.02, -0.03)): {"type": "kenburns", "zoom": [a, b], "pan": [list(pan[0]), list(pan[1])]}  # noqa: E731
    par = lambda **k: {"type": "parallax", "push": 0.08, "orbit_deg": 3, "dof": 0.3, **k}  # noqa: E731
    return {
        "version": 3,
        "settings": {"width": 1080, "height": 1920, "fps": 30, "safe_area": "reels"},
        "brand": {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff",
                  "font_caption": "fonts/Oswald-SemiBold.ttf", "font_display": "fonts/DMSerifDisplay-Regular.ttf",
                  "logo": "inputs/brand/logo.png"},
        "copy": "inputs/brand/copy.json",
        "audio": {"music": "inputs/music/click120.wav", "music_gain_db": -14, "voiceover": True,
                  "duck_db": -10, "snap_cuts_to_beats": True, "loudness_lufs": -14},
        "shots": [
            {"id": "s1", "source": img(0), "motion": par(), "caption": cap("hook", anchor="top"),
             "vo": "hook", "duration": "auto"},
            {"id": "s2", "source": img(1), "motion": kb(1.0, 1.12), "product": prod(),
             "caption": cap("fresh", "words", "top"), "duration": "beats:3", "transition_in": "push"},
            {"id": "s3", "source": img(2), "motion": par(), "product": prod(),
             "caption": cap("tackle", anchor="top"), "vo": "tackle", "duration": "auto", "transition_in": "flash"},
            {"id": "s4", "source": {"color": "#0b3d5c"}, "product": prod(),
             "caption": cap("tough", "pop", "top"), "duration": "beats:2", "transition_in": "crossfade"},
            {"id": "s5", "source": img(3), "motion": kb(1.12, 1.0, ((0.02, 0.0), (0, 0))),
             "product": prod(x=0.4), "duration": "beats:3"},
            {"id": "s6", "source": {"video": "inputs/clips/swirl.mp4", "trim": [0.5, 2.0]},
             "caption": cap("care", "none"), "duration": "beats:3", "transition_in": "zoom-punch"},
            {"id": "s7", "source": img(4), "motion": par(orbit_deg=-3), "product": prod(x=0.6),
             "caption": cap("gentle", anchor="top"), "duration": "beats:3"},
            {"id": "s8", "source": {"funk2": "inputs/funk2/swirl.json"}, "product": prod(),
             "duration": 1.5, "transition_in": "flash"},
            {"id": "s9", "source": img(5), "motion": kb(1.0, 1.1), "product": prod(),
             "caption": cap("fresh", "typewriter", "top"), "duration": "beats:3"},
            {"id": "s10", "source": img(6), "motion": par(), "product": prod(x=0.45),
             "duration": "beats:3", "transition_in": "push"},
            {"id": "s11", "source": img(7), "motion": kb(1.0, 1.15), "product": prod(),
             "caption": cap("tough", "words", "top"), "duration": "beats:3", "transition_in": "crossfade"},
            {"id": "s12", "source": img(0), "motion": par(orbit_deg=3), "product": prod(),
             "caption": cap("gentle", "typewriter", "top"), "duration": "beats:3", "transition_in": "zoom-punch"},
        ],
        "end_card": {"template": "brand-split", "duration": 5.0, "audio": "silence",
                     "copy": {"headline": "end_head", "sub": "end_sub", "url": "url"},
                     "product": "inputs/brand/bottle.png", "headline_reveal": "typewriter"},
    }


def make(dest: Path, width: int = 1080, height: int = 1920) -> dict[str, Path]:
    """Write every demo asset under ``dest``; returns the main paths."""
    dest = Path(dest)
    shots = make_shots(dest, width, height)
    make_bottle(dest / "inputs/brand/bottle.png")
    make_logo(dest / "inputs/brand/logo.png")
    (dest / "inputs/brand").mkdir(parents=True, exist_ok=True)
    (dest / "inputs/brand/copy.json").write_text(json.dumps(COPY, indent=2, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    click_track(dest / "inputs/music/click120.wav")
    make_clip(dest / "inputs/clips/swirl.mp4", max(2, width // 2) // 2 * 2, max(2, height // 2) // 2 * 2)
    (dest / "inputs/funk2").mkdir(parents=True, exist_ok=True)
    (dest / "inputs/funk2/swirl.json").write_text(json.dumps({
        "version": 2, "settings": {"width": 270, "height": 480, "fps": 30, "seconds": 2, "seed": 5},
        "palette": {"name": "vapor", "cycles": 1},
        "layers": [{"type": "shader", "shader": "shaders/warp.frag"}]}, indent=2) + "\n", encoding="utf-8")
    reel = dest / "demo.reel.json"
    reel.write_text(json.dumps(demo_reel(shots), indent=2) + "\n", encoding="utf-8")
    return {"reel": reel, "copy": dest / "inputs/brand/copy.json", "bottle": dest / "inputs/brand/bottle.png",
            "music": dest / "inputs/music/click120.wav"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--dest", default=str(Path(__file__).resolve().parent))
    a = ap.parse_args()
    if sys.platform == "linux" and "DISPLAY" not in os.environ:
        os.environ.setdefault("FUNK_GL_BACKEND", "egl")
    w, h = (int(x) for x in a.size.lower().split("x"))
    for k, v in make(Path(a.dest), w, h).items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()

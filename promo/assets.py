#!/usr/bin/env python3
"""Build one day's asset pack for a funkbox promo: everything in it is real funkbox output or a real repo file.

    python promo/assets.py ~/funkbox-pending/days/2026-10-29 [--seed N]

Writes into <day>/assets/ and prints assets.json, a manifest the agent picks shots from:
- loops: two fresh shader loops (a preset look each, seeded by the day), 720x1280 mp4
- demo: the GLOWMOP demo ad (examples/mimic/preview.mp4)
- stills: FastSD scenes, poster variations, the storyboard sheet, the social preview (real outputs)
- cards: code / command "screenshots" drawn from REAL repo files (so what is shown exists), 1080x1920 PNG
- music: a seeded music bed (promo/music.py)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "promo"))

PRESETS = ["acid-plasma", "pop-moire", "liquid-disco", "hypno-tunnel", "lava-lamp", "op-art", "frozen-kaleido",
           "party-poster"]
STILLS = {
    "fastsd-scenes": ("docs/gallery/fastsd-scenes.jpg", "six FastSD scenes generated on an Intel iGPU"),
    "poster-variations": ("docs/gallery/poster-variations.jpg", "one poster in four palettes, exact text"),
    "storyboard": ("docs/mimic-storyboard.png", "storyboard of the demo ad: one tile per shot"),
    "social-preview": ("docs/social-preview.png", "repo social card"),
    "scene-dog": ("examples/mimic/shots/dog.jpg", "FastSD scene, upscaled"),
    "scene-machine": ("examples/mimic/shots/machine.jpg", "FastSD scene, upscaled"),
}
#: (id, file, marker, lines, caption): the card shows `lines` REAL lines verbatim, starting at the first line that
#: contains `marker` (found at build time, so edits to the files never break a card)
CARDS = [
    ("card-reel-json", "examples/mimic/mimic.reel.json", '"shots": [', 22, "a real shot from the demo ad's shot list"),
    ("card-copy-json", "examples/mimic/copy.json", '"lines": {', 18, "the demo ad's copy deck: every caption comes from here"),
    ("card-install", "README.md", "git clone https://github.com/IsraelWebDev/funkbox", 3, "the install commands"),
    ("card-loop-recipe", "examples/disco_cat.json", "{", 14, "the disco-cat loop recipe"),
    ("card-frame-json", "docs/reel.md", '{"id": "accept"', 3, "a push-in on a screen recording, from the docs"),
    ("card-text-exact", "docs/reel.md", "**Text is exact by construction.**", 3, "how the text audit works, from the docs",
     720),
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "C:/Windows/Fonts/consola.ttf",
              str(ROOT / "fonts" / "Oswald-SemiBold.ttf")):
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def find_lines(src: Path, marker: str, n: int) -> tuple[int, list[str]]:
    lines = src.read_text(encoding="utf-8").splitlines()
    i = next((k for k, l in enumerate(lines) if marker in l), None)
    if i is None:
        raise ValueError(f"{src}: marker {marker!r} not found")
    return i + 1, lines[i:i + n]


def code_card(src: Path, marker: str, n: int, out: Path, title: str, w: int = 1080, h: int = 1920,
              text_w: int | None = None) -> tuple[Path, str]:
    """``n`` real lines of a file (from the first containing ``marker``), drawn as an editor screenshot with line
    numbers; long lines soft-wrap like an editor would (the text itself is verbatim). ``text_w`` narrows the wrap
    column (prose cards) so a push-in can zoom while keeping whole lines in frame."""
    import textwrap
    first, lines = find_lines(src, marker, n)
    img = Image.new("RGB", (w, h), (18, 20, 28))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 110], fill=(32, 36, 48))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([40 + i * 50, 38, 72 + i * 50, 70], fill=c)
    d.text((220, 34), title, font=_font(34), fill=(200, 205, 220))
    longest = max((len(l.expandtabs(4)) for l in lines), default=1)
    tw = min(text_w or w, w)
    size = max(30, min(44, int((tw - 150) / (0.6 * max(longest, 24)))))
    cols = int((tw - 150) / (0.602 * size))
    f, fn = _font(size), _font(int(size * 0.75))
    y = 170
    for num, line in enumerate(lines, start=first):
        parts = textwrap.wrap(line.expandtabs(4), cols, drop_whitespace=False, replace_whitespace=False,
                              subsequent_indent="  ") or [""]
        for j, part in enumerate(parts):
            if j == 0:
                d.text((20, y + size * 0.15), f"{num:>3}", font=fn, fill=(90, 98, 120))
            d.text((120, y), part, font=f, fill=(222, 226, 238))
            y += int(size * 1.45)
        if y > h - 140:
            break
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out, f"{first}-{first + len(lines) - 1}"


def build(day: Path, seed: int) -> dict:
    a = day / "assets"
    a.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    man: dict = {"day": day.name, "seed": seed, "assets": []}

    def add(kind, path, note, **kw):
        man["assets"].append({"id": Path(path).stem, "kind": kind, "path": Path(path).relative_to(day).as_posix(),
                              "note": note, **kw})

    for preset in rng.sample(PRESETS, 2):
        s = rng.randint(1, 999)
        r = subprocess.run([sys.executable, "-m", "funkbox", "loop", "render", "--preset", preset, "--seed", str(s),
                            "--width", "720", "--height", "1280", "--seconds", "4", "--no-png", "--set", "text=", "--name",
                            f"loop-{preset}", "--out-dir", str(a)], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0 and (a / f"loop-{preset}.mp4").is_file():
            add("video", a / f"loop-{preset}.mp4", f"seamless shader loop, preset {preset} seed {s}, 4 s", seconds=4)
        else:
            print(f"loop {preset} failed: {r.stderr[-400:]}", file=sys.stderr)
    shutil.copy2(ROOT / "examples/mimic/preview.mp4", a / "demo-ad.mp4")
    add("video", a / "demo-ad.mp4", "the GLOWMOP demo ad made with funkbox (23 s, 9:16, has its own captions)",
        seconds=23.4)
    for sid, (rel, note) in STILLS.items():
        dst = a / f"{sid}{Path(rel).suffix}"
        shutil.copy2(ROOT / rel, dst)
        with Image.open(dst) as im:
            add("image", dst, note, size=list(im.size))
    for cid, rel, marker, n, note, *opt in CARDS:
        src = ROOT / rel
        try:
            path, span = code_card(src, marker, n, a / f"{cid}.png", rel, text_w=opt[0] if opt else None)
        except (OSError, ValueError) as e:
            print(f"card {cid} skipped: {e}", file=sys.stderr)
            continue
        add("image", path, f"{note} ({rel} lines {span}, verbatim; text sits in the top part of the card)",
            size=[1080, 1920], source=f"{rel}:{span}")
    import music
    add("music", music.write(a / "music.mp3", 30.0, 110.0, seed), "seeded music bed, 110 BPM, 30 s", bpm=110)
    (day / "assets.json").write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    return man


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="build a promo day's asset pack")
    import state
    ap.add_argument("day", nargs="?", default=str(state.DAYS / dt.datetime.now(dt.timezone.utc).date().isoformat()))
    ap.add_argument("--seed", type=int, default=None)
    x = ap.parse_args()
    d = Path(x.day)
    seed = x.seed if x.seed is not None else int(d.name.replace("-", "")) if d.name.replace("-", "").isdigit() else 7
    print(json.dumps(build(d, seed), indent=2))

#!/usr/bin/env python3
"""Synthetic assets for the stills demo poster (no external files needed).

    python examples/stills/make_assets.py [--dest examples/stills/inputs]

Writes bottle.png (a fake product with alpha) and carpet.png (a woven texture). Both are generated, gitignored,
and carry no text.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "reel_demo"))


def make_carpet(path: Path, w: int = 1080, h: int = 1350, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    weave = 0.5 + 0.25 * np.sin(xx * 0.9) * np.sin(yy * 0.9)
    fibres = Image.fromarray((rng.random((h, w)) * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.1))
    blotch = Image.fromarray((rng.random((h // 24, w // 24)) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    v = 0.55 * weave + 0.3 * (np.asarray(fibres, np.float32) / 255) + 0.15 * (np.asarray(blotch, np.float32) / 255)
    v = np.clip((v - v.min()) / (v.max() - v.min()), 0, 1)
    Image.fromarray((v * 255).astype(np.uint8)).convert("RGB").save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(HERE / "inputs"))
    a = ap.parse_args()
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    import make_assets
    make_assets.make_bottle(dest / "bottle.png", 300, 700)
    make_carpet(dest / "carpet.png")
    print(f"wrote {dest / 'bottle.png'} and {dest / 'carpet.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Synthetic project inputs for the v3.1 tests (no external files)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

LABEL_RED = (200, 16, 46)


def _ff():
    import funk
    return funk.ffmpeg_exe()


def bottle(w=300, h=700, bg=None, label=LABEL_RED, body=(60, 66, 78)) -> Image.Image:
    """A product with a flat red label. bg=None -> transparent RGBA, else an RGB background colour."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((20, 190, w - 20, h - 8), radius=40, fill=body + (255,))
    d.rounded_rectangle((70, 90, w - 70, 220), radius=30, fill=body + (255,))
    d.rectangle((105, 40, w - 105, 100), fill=body + (255,))
    d.rounded_rectangle((90, 4, w - 90, 56), radius=10, fill=(30, 30, 34, 255))
    d.rectangle((20, 330, w - 20, 600), fill=label + (255,))
    d.rectangle((60, 440, w - 60, 470), fill=(240, 200, 40, 255))
    if bg is None:
        return im
    out = Image.new("RGB", (w + 120, h + 120), bg)
    out.paste(im, (60, 60), im)
    return out


def scene(path: Path, seed: int, size=(480, 640)) -> None:
    """A noisy colourful scene (non-uniform border)."""
    rng = np.random.default_rng(seed)
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([128 + 100 * np.sin(xx / (30 + seed * 7) + seed), 128 + 100 * np.sin(yy / (40 + seed * 5)),
                     128 + 100 * np.sin((xx + yy) / (50 + seed * 3))], -1)
    base += rng.normal(0, 18, base.shape)
    im = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for _ in range(6):
        cx, cy, r = rng.integers(0, w), rng.integers(0, h), rng.integers(20, 80)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def sine(path: Path, seconds=2.0, hz=440) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "libmp3lame"] if path.suffix == ".mp3" else []
    subprocess.run([_ff(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}",
                    *codec, str(path)], check=True)


def clip(path: Path, w=240, h=426, seconds=1.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([_ff(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=15",
                    "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def logo(path: Path) -> None:
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((10, 10, 190, 190), fill=(20, 120, 220, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def mixed_folder(d: Path, with_product_jpg=True, with_product_png=True, n_scenes=3, with_media=True) -> Path:
    """mp3 + wav (ffmpeg sine), an mp4, a white-background product jpg, an alpha product png,
    logo.png and scene images."""
    d.mkdir(parents=True, exist_ok=True)
    if with_media:
        sine(d / "track.mp3")
        sine(d / "sting.wav", 1.0, 880)
        clip(d / "move.mp4")
    if with_product_jpg:
        bottle(bg=(255, 255, 255)).save(d / "shelf-photo.jpg", quality=95)
    if with_product_png:
        bottle().save(d / "cutout.png")
    logo(d / "logo.png")
    for i in range(n_scenes):
        scene(d / f"scene{i + 1}.jpg", i + 1)
    return d


COPY_LINES = {
    "hook": {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?", "say": "Look closer. Time for a clean?"},
    "tackle": {"text": "TACKLE CARPET CLEANING WITH GLOWMOP.", "say": "Tackle carpet cleaning with glow mop."},
    "care": {"text": "GIVE YOUR CARPET THE ATTENTION IT NEEDS."},
    "fresh": {"text": "FRESH FIBRES, EVERY TIME."},
    "end_head": {"text": "Deep\nCleaning\nPower"},
    "end_sub": {"text": "PROFESSIONAL RESULTS"},
    "url": {"text": "GLOWMOP.EXAMPLE"},
}


def make_project(slug="demo", scenes=3, clips=1, music=True, product=True, logo_file=True, approve=True,
                 lines=None, inbox: Path | None = None):
    """A complete synthetic project via the real tools (env FUNK_PROJECTS/FUNK_INBOX must already be set)."""
    from reel import approval, brand, ingest, project as P
    P.new_project(slug)
    inbox = inbox or P.inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    if product:
        bottle().save(inbox / "product.png")
    if logo_file:
        logo(inbox / "logo.png")
    for i in range(scenes):
        scene(inbox / f"scene{i + 1}.jpg", i + 1)
    for i in range(clips):
        clip(inbox / f"clip{i + 1}.mp4", seconds=1.0 + 0.2 * i)
    if music:
        sine(inbox / "track.mp3", 6.0)
    ingest.ingest(slug)
    if product or logo_file:
        brand.brand_from_assets(slug)
    if approve:
        r = approval.propose_copy(slug, lines or COPY_LINES, {"GLOWMOP": "glow mop"})
        approval.approve(slug, r["draft_sha256"], "chat")
    return slug

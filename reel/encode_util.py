"""Video encode + audio mux. Builds on funkgl.encode (same rgb24 -> ffmpeg pipe); adds AAC 192k mux."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

import numpy as np

from funkgl.encode import _ffmpeg, encode


def encode_video(frames: Iterable[np.ndarray], w: int, h: int, fps: int, out_mp4: Path, poster: Path | None = None,
                 gif: bool = False) -> dict[str, str]:
    """Encode silent H.264. funkgl.encode names files <base>.mp4 / <base>.png; we move the poster if wanted."""
    base = out_mp4.with_suffix("")
    paths = encode(frames, w, h, fps, base, mp4=True, gif=gif, poster=poster is not None, gif_width=360)
    res = {"video": paths["mp4"]}
    if "png" in paths:
        want = poster or Path(paths["png"])
        if Path(paths["png"]) != want:
            Path(paths["png"]).replace(want)
        res["png"] = str(want)
    if "gif" in paths:
        res["gif"] = paths["gif"]
    return res


def mux_audio(video: Path, wav: Path, out: Path) -> None:
    """Copy the video stream, encode the 48 kHz stereo wav as AAC 192k."""
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {r.stderr.decode('utf-8', 'replace').strip()[:400]}")

"""ffmpeg, video encoding and font lookup: shared by every area.

One ``ffmpeg_exe``, one rgb24-pipe encode path (mp4 / gif / poster png), one audio mux, one font lookup.
ffmpeg's stdout is always DEVNULL or a pipe we read: a surrounding MCP stdio server must never see a stray byte.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageFont


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #
def ffmpeg_exe() -> str | None:
    """ffmpeg on PATH, else the static build bundled with imageio-ffmpeg, else None."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:  # pip install imageio-ffmpeg -> bundled static ffmpeg, no install needed
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def require_ffmpeg() -> str:
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg not found; `pip install imageio-ffmpeg` or put ffmpeg on PATH")
    return exe


_SCRIPT_OPT: dict[str, list[str]] = {}


def filter_script_args(script: Path) -> list[str]:
    """ffmpeg arguments that read a filtergraph from a file. ffmpeg >= 7 replaced -filter_complex_script
    with -/filter_complex <file> (8.x removed the old option; the imageio-ffmpeg build on Windows is 8.x,
    distro ffmpeg 6.x only has the old one)."""
    exe = require_ffmpeg()
    if exe not in _SCRIPT_OPT:
        h = subprocess.run([exe, "-hide_banner", "-h", "full"], capture_output=True)
        txt = h.stdout.decode("utf-8", "replace") + h.stderr.decode("utf-8", "replace")
        _SCRIPT_OPT[exe] = ["-filter_complex_script"] if "-filter_complex_script" in txt else ["-/filter_complex"]
    return [*_SCRIPT_OPT[exe], str(script)]


# --------------------------------------------------------------------------- #
# encoding
# --------------------------------------------------------------------------- #
def encode(frames: Iterable[np.ndarray], w: int, h: int, fps: int, out_base: Path,
           mp4: bool = True, gif: bool = True, gif_width: int = 400,
           poster: bool = True, audio: Path | None = None) -> dict[str, str]:
    """Pipe raw rgb24 frames into ffmpeg. Returns {kind: path} for <out_base>.mp4 / .gif / .png."""
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    first: np.ndarray | None = None
    ff = require_ffmpeg() if (mp4 or gif) else None
    proc = None
    mp4_path = f"{out_base}.mp4"
    if ff:
        cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
        if audio is not None:
            cmd += ["-i", str(audio)]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
                "-movflags", "+faststart"]
        if audio is not None:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd += [mp4_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
    try:
        for fr in frames:
            if first is None:
                first = fr
            if proc is not None:
                assert proc.stdin
                proc.stdin.write(np.ascontiguousarray(fr).tobytes())
    except BrokenPipeError:
        pass
    finally:
        if proc is not None and proc.stdin:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
    if proc is not None:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed while encoding mp4: {err.strip()[:500]}")
        paths["mp4"] = mp4_path
        if gif:
            gw = min(gif_width, w)
            gfps = min(fps, 20)
            vf = (f"fps={gfps},scale={gw}:-1:flags=lanczos,split[a][b];"
                  "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3")
            subprocess.run([ff, "-y", "-loglevel", "error", "-i", mp4_path, "-vf", vf,
                            "-loop", "0", f"{out_base}.gif"], check=True,
                           stdout=subprocess.DEVNULL)
            paths["gif"] = f"{out_base}.gif"
        if not mp4:
            os.remove(mp4_path)
            paths.pop("mp4")
    if poster and first is not None:
        Image.fromarray(first).save(f"{out_base}.png")
        paths["png"] = f"{out_base}.png"
    return paths


def encode_video(frames: Iterable[np.ndarray], w: int, h: int, fps: int, out_mp4: Path,
                 poster: Path | None = None, gif: bool = False) -> dict[str, str]:
    """Silent H.264 to ``out_mp4`` (the reel path); the first frame is saved to ``poster`` if wanted."""
    base = Path(out_mp4).with_suffix("")
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
    cmd = [require_ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav), "-map", "0:v:0",
           "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-shortest", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {r.stderr.decode('utf-8', 'replace').strip()[:400]}")


# --------------------------------------------------------------------------- #
# fonts
# --------------------------------------------------------------------------- #
_BOLD = ("arialbd.ttf", "Impact.ttf", "impact.ttf", "DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf")
_REGULAR = ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf")


@lru_cache(maxsize=16)
def font(px: int, bold: bool = True) -> ImageFont.ImageFont:
    """A system TrueType font at ``px`` (bold Arial/Impact/DejaVu, or regular), else Pillow's default.
    Brand and caption fonts are loaded from the paths the brand names; this is for labels and v1-style text."""
    for name in (_BOLD if bold else _REGULAR):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)

"""ffmpeg pipe encoding (mp4 / gif) and the high-level render_recipe()."""
from __future__ import annotations

import copy
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
from PIL import Image

import funk

from .engine import Engine
from .recipe import Recipe2, resolve_path, save_recipe


def _ffmpeg() -> str:
    exe = funk.ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg not found; `pip install imageio-ffmpeg` or put ffmpeg on PATH")
    return exe


def encode(frames: Iterable[np.ndarray], w: int, h: int, fps: int, out_base: Path,
           mp4: bool = True, gif: bool = True, gif_width: int = 400,
           poster: bool = True, audio: Path | None = None) -> dict[str, str]:
    """Pipe raw rgb24 frames into ffmpeg. Returns {kind: path}. stdout is DEVNULL so a
    surrounding MCP stdio server is never corrupted."""
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    first: np.ndarray | None = None
    ff = _ffmpeg() if (mp4 or gif) else None
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


@dataclass
class RenderResult:
    paths: dict[str, str]
    frames: int
    width: int
    height: int
    seconds: float            # wall time spent rendering+encoding
    recipe: Recipe2 = field(repr=False, default=None)  # type: ignore[assignment]

    @property
    def fps(self) -> float:
        return self.frames / self.seconds if self.seconds > 0 else float("inf")

    def timing_line(self) -> str:
        return f"{self.frames} frames {self.width}x{self.height} in {self.seconds:.1f}s ({self.fps:.1f} fps)"


def render_recipe(recipe: Recipe2, out_dir: str | Path, name: str, mp4: bool = True,
                  gif: bool = True, png: bool = True, gif_width: int = 400,
                  engine: Engine | None = None) -> RenderResult:
    """Render the whole loop to out_dir/name.{mp4,gif,png,json}. The JSON sidecar is a v2 recipe
    with inline shader source, so it reproduces the render by itself."""
    rec = copy.deepcopy(recipe)
    st = rec.settings
    st.width -= st.width % 2
    st.height -= st.height % 2
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / name
    t0 = time.perf_counter()
    own = engine is None
    eng = engine or Engine(st.width, st.height)
    try:
        eng.prepare(rec)            # fail fast on compile errors before ffmpeg starts
        n = st.frames

        def gen() -> Iterator[np.ndarray]:
            for i in range(n):
                yield eng.render(rec, i)

        audio = resolve_path(st.audio, rec.base_dir) if st.audio else None
        paths = encode(gen(), eng.w, eng.h, st.fps, base, mp4=mp4, gif=gif,
                       gif_width=gif_width, poster=png, audio=audio)
    finally:
        if own:
            eng.release()
    dt = time.perf_counter() - t0
    paths["json"] = f"{base}.json"
    resolved = save_recipe(rec, f"{base}.json", outputs=paths)
    return RenderResult(paths, n, eng.w if own else st.width, eng.h if own else st.height, dt, resolved)

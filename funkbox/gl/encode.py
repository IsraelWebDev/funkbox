"""High-level render_recipe(): a recipe in, a seamless loop (mp4 / gif / png / json sidecar) out."""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np

from funkbox.config import resolve_path
from funkbox.media import encode

from .engine import Engine
from .recipe import Recipe2, save_recipe


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

"""``funkbox doctor --bench``: how fast does THIS machine render frames?

Renders a few frames of a named loop look off-screen at a vertical Short's size (1080x1920 by default) on whatever
OpenGL backend funkbox picked (a GPU, or software OpenGL), times them, and estimates how long the frames of a
20-second Short would take. It answers "is my laptop enough?" before a long render. The estimate covers drawing
frames only; encoding, music and captions add some time on top.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

DEFAULT_LOOK = "liquid-disco"


@dataclass
class BenchResult:
    look: str
    width: int
    height: int
    frames: int
    seconds: float
    renderer: str
    software: bool

    @property
    def fps(self) -> float:
        return self.frames / self.seconds if self.seconds > 0 else float("inf")


def estimate_seconds(fps: float, video_seconds: float = 20.0, video_fps: int = 30) -> float:
    """Seconds needed to draw every frame of a video of ``video_seconds`` at ``video_fps``, at ``fps`` frames/s."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    return video_seconds * video_fps / fps


def report(res: BenchResult, video_seconds: float = 20.0, video_fps: int = 30) -> str:
    """The lines ``doctor --bench`` prints."""
    kind = "software OpenGL" if res.software else "GPU"
    est = estimate_seconds(res.fps, video_seconds, video_fps)
    return (
        f"bench: {res.frames} frames of '{res.look}' at {res.width}x{res.height} in {res.seconds:.2f} s "
        f"= {res.fps:.1f} frames/s ({kind}: {res.renderer})\n"
        f"bench: a {video_seconds:g} s {res.width}x{res.height} video at {video_fps} fps needs about "
        f"{est:.0f} s to draw its frames here (encoding and audio add to this)"
    )


def run(frames: int = 30, width: int = 1080, height: int = 1920, look: str = DEFAULT_LOOK) -> BenchResult:
    """Render ``frames`` frames of ``look`` and time them. The first frame (shader compile) is not counted."""
    if frames < 1:
        raise ValueError("frames must be at least 1")
    from funkbox.gl import context
    from funkbox.gl.engine import Engine
    from funkbox.loops import presets

    if look not in presets.LOOKS:
        raise ValueError(f"unknown look {look!r}; have {sorted(presets.LOOKS)}")
    rec = presets.preset_recipe(look, width=width, height=height, seconds=max(1.0, frames / 30))
    eng = Engine(width, height)
    try:
        eng.render(rec, 0)  # warm up: compile the shaders, upload textures
        t0 = time.perf_counter()
        for i in range(1, frames + 1):
            eng.render(rec, i % rec.settings.frames)
        seconds = time.perf_counter() - t0
        renderer = context.gl_info().get("GL_RENDERER") or "unknown"
    finally:
        eng.release()
    return BenchResult(look, eng.w, eng.h, frames, seconds, renderer, context._is_software_renderer(renderer))

#!/usr/bin/env python3
"""
funk as an MCP server, so Claude can render and *look at* its own loops.

Register (Claude Desktop, claude_desktop_config.json):
    "funk": {"command": "python", "args": ["C:\\\\dev\\\\funkbox\\\\funk_mcp.py"],
             "env": {"FUNK_OUT": "C:\\\\dev\\\\funkbox\\\\out"}}
Register (Claude Code):
    claude mcp add funk -e FUNK_OUT=C:\\dev\\funkbox\\out -- python C:\\dev\\funkbox\\funk_mcp.py

Every tool returns a PNG preview alongside the text, so the model can critique
the result and iterate ("more chroma, fewer blobs") instead of guessing.
"""
from __future__ import annotations

import io
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Union

from PIL import Image as PILImage

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore

import funk
from funkbox.config import funk_out

OUT = funk_out()
Scalar = Union[str, int, float, bool]

server = _Server(
    "funk",
    instructions=(
        "CPU-only procedural art: seamless looping MP4/GIF/PNG from math. "
        "Workflow: preview_still or contact_sheet to explore cheaply, then render_loop "
        "once the look is right. Every result includes a PNG so you can judge it. "
        "Call list_looks for presets, effects, palettes and recipe keys."
    ),
)


def _png(img: PILImage.Image, max_side: int = 640) -> Image:
    img = img.copy()
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return Image(data=buf.getvalue(), format="png")


def _recipe(preset: str, seed: int, text: str, overrides: dict[str, Scalar] | None,
            random: bool) -> funk.Recipe:
    if random:
        rc = funk.random_recipe(seed)
    elif preset in funk.PRESETS:
        rc = replace(funk.PRESETS[preset])
    else:
        raise ValueError(f"unknown preset {preset!r}; have {sorted(funk.PRESETS)}")
    if text:
        rc = replace(rc, text=text, text_wobble=rc.text_wobble or 6.0)
    pairs = [f"{k}={v}" for k, v in (overrides or {}).items()]
    return funk.apply_overrides(rc, pairs)


@server.tool()
def list_looks() -> str:
    """List presets, effects, palettes and the recipe keys usable in `overrides`."""
    lines = ["PRESETS:"]
    for k, v in funk.PRESETS.items():
        lines.append(f"  {k}: effect={v.effect} palette={v.palette} kaleido={v.kaleido} "
                     f"halftone={v.halftone} posterize={v.posterize} chroma={v.chroma}")
    lines.append("EFFECTS: " + ", ".join(f"{k} ({e.blurb})" for k, e in funk.EFFECTS.items()))
    lines.append("PALETTES: " + ", ".join(funk.PALETTES))
    lines.append("RECIPE KEYS: effect palette cycles(int) spin(int) zoom kaleido(int) "
                 "posterize(int) halftone(int px) chroma(int px) scanlines(bool) vignette grain "
                 "text text_size text_wobble text_color shadow")
    lines.append(f"OUTPUT DIR: {OUT}")
    return "\n".join(lines)


@server.tool()
def preview_still(preset: str = "liquid-disco", seed: int = 1, t: float = 0.25,
                  size: int = 480, text: str = "", overrides: dict[str, Scalar] | None = None,
                  random: bool = False) -> list:
    """Render a single frame at loop position t (0..1). Fast - use it to iterate on a look."""
    rc = funk.resolve(_recipe(preset, seed, text, overrides, random), seed)
    st = funk.Settings(width=size, height=size, seed=seed)
    frame = funk.render_frame(round(t * st.frames) % st.frames, st, rc)
    return [f"recipe: {asdict(rc)}", _png(PILImage.fromarray(frame))]


@server.tool()
def contact_sheet(preset: str = "liquid-disco", seeds: str = "1-9", text: str = "",
                  overrides: dict[str, Scalar] | None = None, random: bool = False) -> list:
    """Grid of stills across seeds (e.g. "1-9" or "3,7,12") to pick the best variation."""
    seed_list = funk.parse_seeds(seeds)
    rc = _recipe(preset, seed_list[0], text, overrides, random)
    label = "random" if random else preset
    path = funk.contact_sheet(None if random else rc, funk.Settings(width=300, height=300),
                              seed_list,
                              OUT / f"{label}-sheet.png")
    return [f"saved {path}", _png(PILImage.open(path), max_side=900)]


@server.tool()
def render_loop(preset: str = "liquid-disco", seed: int = 1, seconds: float = 4.0,
                size: int = 540, fps: int = 30, text: str = "",
                overrides: dict[str, Scalar] | None = None, random: bool = False,
                gif: bool = True, name: str = "") -> list:
    """Render a perfectly seamless loop to MP4 (+GIF, +PNG poster, +JSON recipe sidecar).
    A 4s 540px loop takes roughly 5-15s on a laptop CPU."""
    rc = _recipe(preset, seed, text, overrides, random)
    st = funk.Settings(width=size, height=size, fps=fps, seconds=seconds, seed=seed)
    label = "random" if random else preset
    paths = funk.render(rc, st, OUT, name or f"{label}-s{seed}", gif=gif)
    summary = "\n".join(f"{k}: {v}" for k, v in paths.items())
    return [summary, _png(PILImage.open(paths["png"]))]


if __name__ == "__main__":
    server.run()

#!/usr/bin/env python3
"""Interim studio MCP server (replaced by the one funkbox server in v4 C4): thin MCP wrappers over
``funkbox.studio.apps`` and ``funkbox.studio.images``."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from funkbox import mcp_util  # noqa: E402
from funkbox.studio import apps, images  # noqa: E402

server = mcp_util.Server(
    "studio",
    instructions=("Starts/stops the local creative apps on demand. Call status() first. generate_image() "
                  "auto-starts FastSD and saves into the funk inputs folder."))


def _blocks(res: images.ImageResult):
    return [res.text, mcp_util.png(res.image, res.max_side)] if res.image is not None else res.text


@server.tool()
@mcp_util.safe
def status() -> str:
    """Which local services are running."""
    return apps.status()


@server.tool()
@mcp_util.safe
def start(service: str = "all", wait_s: float = 0) -> str:
    """Start 'fastsd', 'gimp' or 'all'."""
    return apps.start(service, wait_s)


@server.tool()
@mcp_util.safe
def stop(service: str = "fastsd", force: bool = False) -> str:
    """Stop 'fastsd'."""
    return apps.stop(service, force)


@server.tool()
@mcp_util.safe
def generate_image(prompt: str, aspect: str = "square", seed: int = -1, n: int = 1, negative_prompt: str = "",
                   name: str = "", steps: int = 1, confirm_slow: bool = False) -> list:
    """Text-to-image with FastSD (SD-Turbo, OpenVINO on the iGPU); auto-starts FastSD. Aspect presets:
    square 512x512, portrait 512x768, landscape 768x512, story 512x896, wide 896x512. n=2-8 returns variations
    (seeds seed, seed+1, ... or random) as a contact sheet plus one file each in the inputs folder. seed -1 =
    random (the seeds used are reported). steps 1 is best for SD-Turbo. COST OF SIZE CHANGES: the model is
    compiled per image size (~2-3 min the first time at each size); this tool answers with a warning and does
    nothing until you pass confirm_slow=true. PREFER 'square': SD-Turbo is trained at 512x512 and tall/wide sizes often duplicate the subject (two dogs stacked, a room on two floors); for 9:16 use square and let reel/stills cover-crop it (upscale_image first for sharper crops). Never request a product or text in the prompt. SD-Turbo output is
    under the Stability AI Community License (free commercial use under US$1M annual revenue; Stability asks
    commercial users to register)."""
    return _blocks(images.generate(prompt, aspect, seed, n, negative_prompt, name, steps, confirm_slow))


@server.tool()
@mcp_util.safe
def restyle_image(path: str, prompt: str, strength: float = 0.5, aspect: str | None = None, seed: int = -1,
                  name: str = "", confirm_slow: bool = False) -> list:
    """img2img: re-render an existing picture (a file in inputs/, out/ or the funkbox folder) following `prompt`.
    strength 0.05 stays close to the original, 0.95 nearly ignores it. aspect None picks the preset closest to the
    picture's shape (it is cover-cropped to that size). SD-Turbo needs steps*strength >= 1, so steps are raised
    automatically and reported. A size not used since FastSD started needs confirm_slow=true (see generate_image).
    SD-Turbo output is under the Stability AI Community License (free commercial use under US$1M annual revenue)."""
    return _blocks(images.restyle(path, prompt, strength, aspect, seed, name, confirm_slow))


@server.tool()
@mcp_util.safe
def upscale_image(path: str, name: str = "") -> list:
    """2x upscale of an image (factor 2 only). Uses FastSD's upscaler when it works on this machine, otherwise
    LANCZOS plus a mild unsharp mask; the result says which method ran. Saved next to the other inputs."""
    return _blocks(images.upscale(path, name))


if __name__ == "__main__":
    server.run()

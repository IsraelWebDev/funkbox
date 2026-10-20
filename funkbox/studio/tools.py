"""MCP tools of the ``image`` (FastSD text-to-image, img2img, upscale) and ``apps`` (FastSD, GIMP) areas.

Plain module-level functions plus ``register(server)``: thin wrappers over ``funkbox.studio.images`` and
``funkbox.studio.apps``. With STUDIO_FAKE_SD=1 the image tools use a deterministic stub (the tests).
"""
from __future__ import annotations

from funkbox.mcp_util import png as _png, safe
from funkbox.studio import apps, images


def _blocks(res: images.ImageResult):
    return [res.text, _png(res.image, res.max_side)] if res.image is not None else res.text


@safe
def apps_status() -> str:
    """Which local services are running (FastSD, GIMP's MCP port), the inputs folder, the image backend."""
    return apps.status()


@safe
def apps_start(service: str = "all", wait_s: float = 0) -> str:
    """Start 'fastsd', 'gimp' or 'all'. Idempotent. FastSD is launched and awaited. GIMP is opened by
    the user via the 'GIMP + MCP' desktop shortcut: this returns what to ask them, or, with wait_s>0,
    waits that long for GIMP's MCP port after you've asked."""
    return apps.start(service, wait_s)


@safe
def apps_stop(service: str = "fastsd", force: bool = False) -> str:
    """Stop 'fastsd' (frees ~4 GB RAM). GIMP is never closed by funkbox (unsaved work)."""
    return apps.stop(service, force)


@safe
def image_generate(prompt: str, aspect: str = "square", seed: int = -1, n: int = 1, negative_prompt: str = "",
                   name: str = "", steps: int = 1, confirm_slow: bool = False) -> list:
    """Text-to-image with FastSD (SD-Turbo, OpenVINO on the iGPU); auto-starts FastSD. Aspect presets:
    square 512x512, portrait 512x768, landscape 768x512, story 512x896, wide 896x512. n=2-8 returns variations
    (seeds seed, seed+1, ... or random) as a contact sheet plus one file each in the inputs folder. seed -1 =
    random (the seeds used are reported). steps 1 is best for SD-Turbo. COST OF SIZE CHANGES: the model is
    compiled per image size (~2-3 min the first time at each size); this tool answers with a warning and does
    nothing until you pass confirm_slow=true. PREFER 'square': SD-Turbo is trained at 512x512 and tall/wide sizes often duplicate the subject (two dogs stacked, a room on two floors); for 9:16 use square and let reel/stills cover-crop it (image_upscale first for sharper crops). Never request a product or text in the prompt. SD-Turbo output is
    under the Stability AI Community License (free commercial use under US$1M annual revenue; Stability asks
    commercial users to register)."""
    return _blocks(images.generate(prompt, aspect, seed, n, negative_prompt, name, steps, confirm_slow))


@safe
def image_restyle(path: str, prompt: str, strength: float = 0.5, aspect: str | None = None, seed: int = -1,
                  name: str = "", confirm_slow: bool = False) -> list:
    """img2img: re-render an existing picture (a file in inputs/, out/ or the funkbox folder) following `prompt`.
    strength 0.05 stays close to the original, 0.95 nearly ignores it. aspect None picks the preset closest to the
    picture's shape (it is cover-cropped to that size). SD-Turbo needs steps*strength >= 1, so steps are raised
    automatically and reported. A size not used since FastSD started needs confirm_slow=true (see image_generate).
    SD-Turbo output is under the Stability AI Community License (free commercial use under US$1M annual revenue)."""
    return _blocks(images.restyle(path, prompt, strength, aspect, seed, name, confirm_slow))


@safe
def image_upscale(path: str, name: str = "") -> list:
    """2x upscale of an image (factor 2 only). Uses FastSD's upscaler when it works on this machine, otherwise
    LANCZOS plus a mild unsharp mask; the result says which method ran. Saved next to the other inputs."""
    return _blocks(images.upscale(path, name))


TOOLS = [apps_status, apps_start, apps_stop, image_generate, image_restyle, image_upscale]


def register(server) -> None:
    for fn in TOOLS:
        server.tool(name=fn.__name__)(fn)

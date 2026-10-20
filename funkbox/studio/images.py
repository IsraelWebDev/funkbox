"""funk images: text-to-image, img2img and 2x upscale through FastSD (or the deterministic stub with
STUDIO_FAKE_SD=1, which is what the tests use).

Each function returns an ``ImageResult``: ``text`` is what the user (or Claude) reads, ``image`` is the saved
picture (a contact sheet when n > 1) or None when nothing was generated (a size warning, say). Files land in
the inputs folder, so recipes and posters can use them as ``inputs/<name>.png``. The MCP tools (``image_*``)
and the CLI (``funkbox image``) are thin wrappers. Settings come from ``funkbox.config`` at call time.
"""
from __future__ import annotations

import hashlib
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image as PILImage

from funkbox import config
from funkbox.studio import apps, sdcore

@dataclass
class ImageResult:
    text: str
    image: PILImage.Image | None = None
    max_side: int = 640


def _rel(p: Path) -> str:
    home = config.get().home
    return p.relative_to(home).as_posix() if p.is_relative_to(home) else str(p)


def find_image(path: str) -> Path:
    """A path as the user/Claude gives it: absolute, or relative to inputs/, the funkbox folder or out/."""
    c = config.get()
    p = Path(str(path).replace("\\", "/")).expanduser()
    cands = [p] if p.is_absolute() else [c.inputs / p, c.home / p, c.out / p]
    last: Exception | None = None
    for cand in cands:
        try:
            return sdcore.fence_path(cand, apps.roots())
        except sdcore.SDError as e:
            last = e
    raise last  # type: ignore[misc]


def _fake_generate(body: dict) -> dict:
    t0 = time.perf_counter()
    w, h = body["width"], body["height"]
    init = PILImage.open(body["init_image"]) if body.get("init_image") else None
    im = sdcore.fake_image(body["prompt"], w, h, max(0, body["seed"]), init, body.get("strength", 0.5))
    inputs = config.get().inputs
    inputs.mkdir(parents=True, exist_ok=True)
    path = inputs / (sdcore.safe_name(body.get("name", ""), "stub-" + uuid.uuid4().hex[:8]) + ".png")
    im.save(path)
    res = {"ok": True, "path": str(path), "seconds": round(time.perf_counter() - t0, 2), "width": w,
           "height": h, "steps": body["steps"]}
    if body.get("steps_note"):
        res["steps_note"] = body["steps_note"]
    return res


def _run_generate(body: dict) -> dict:
    res = _fake_generate(body) if config.get().fake_sd else apps.fastsd.generate(body)
    apps.sizes.mark(res["width"], res["height"])
    return res


def _slow_gate(w: int, h: int, confirm_slow: bool) -> str | None:
    """The warning to return INSTEAD of generating when this size has not been compiled yet."""
    warn = apps.sizes.warning(w, h)
    if warn and not confirm_slow:
        return (f"NOT STARTED: {warn}. Tell the user, then call again with confirm_slow=true. "
                "(512x512 'square' stays warm.)")
    return None


def _seeds(seed: int, n: int) -> list[int]:
    base = int(seed) if int(seed) >= 0 else (0 if config.get().fake_sd else random.randrange(2 ** 31))
    return [base + i for i in range(n)]


def _result(results: list[dict], prompt: str, base_name: str, extra: str = "") -> ImageResult:
    from funkbox.stills.sheet import contact_sheet
    paths = [Path(r["path"]) for r in results]
    lines = [f"  seed {r['seed']}: {p}  (loop / poster image path: {_rel(p)})  {r['width']}x{r['height']} in {r['seconds']}s"
             for r, p in zip(results, paths)]
    notes = sorted({r["steps_note"] for r in results if r.get("steps_note")})
    head = f"saved {len(paths)} image(s) for {prompt!r}" if len(paths) > 1 else f"saved {paths[0]}"
    text = "\n".join([head] + lines + notes + ([extra] if extra else []))
    if len(paths) == 1:
        return ImageResult(text, PILImage.open(paths[0]))
    ims = [PILImage.open(p) for p in paths]
    sheet = contact_sheet(ims, [f"{i + 1}: seed {r['seed']}" for i, r in enumerate(results)])
    out = config.get().out
    out.mkdir(parents=True, exist_ok=True)
    sp = out / f"sheet-{base_name}.png"
    sheet.save(sp)
    return ImageResult(text + f"\ncontact sheet: {sp}", sheet, 1000)


def generate(prompt: str, aspect: str = "square", seed: int = -1, n: int = 1, negative_prompt: str = "",
             name: str = "", steps: int = 1, confirm_slow: bool = False) -> ImageResult:
    """Text-to-image with FastSD (SD-Turbo, OpenVINO on the iGPU); auto-starts FastSD. Aspect presets:
    square 512x512, portrait 512x768, landscape 768x512, story 512x896, wide 896x512. n=2-8 returns variations
    (seeds seed, seed+1, ... or random) as a contact sheet plus one file each in the inputs folder. seed -1 =
    random (the seeds used are reported). steps 1 is best for SD-Turbo. COST OF SIZE CHANGES: the model is
    compiled per image size (~2-3 min the first time at each size); this tool answers with a warning and does
    nothing until you pass confirm_slow=true. PREFER 'square': SD-Turbo is trained at 512x512 and tall/wide sizes often duplicate the subject (two dogs stacked, a room on two floors); for 9:16 use square and let reel/stills cover-crop it (upscale_image first for sharper crops). Never request a product or text in the prompt. SD-Turbo output is
    under the Stability AI Community License (free commercial use under US$1M annual revenue; Stability asks
    commercial users to register)."""
    if not 1 <= int(n) <= 8:
        raise ValueError("n must be 1..8")
    w, h = sdcore.aspect_size(aspect)
    gate = _slow_gate(w, h, confirm_slow)
    if gate:
        return ImageResult(gate)
    seeds = _seeds(seed, int(n))
    base = sdcore.safe_name(name, f"still-{hashlib.sha256(prompt.encode()).hexdigest()[:6]}")
    results = []
    for i, sd in enumerate(seeds):
        nm = base if n == 1 else f"{base}-{i + 1}"
        body = sdcore.build_request(prompt, aspect, seed=sd, steps=steps, negative_prompt=negative_prompt, name=nm)
        res = _run_generate(body)
        res["seed"] = sd
        results.append(res)
    return _result(results, prompt, base)


def restyle(path: str, prompt: str, strength: float = 0.5, aspect: str | None = None, seed: int = -1,
            name: str = "", confirm_slow: bool = False) -> ImageResult:
    """img2img: re-render an existing picture (a file in inputs/, out/ or the funkbox folder) following `prompt`.
    strength 0.05 stays close to the original, 0.95 nearly ignores it. aspect None picks the preset closest to the
    picture's shape (it is cover-cropped to that size). SD-Turbo needs steps*strength >= 1, so steps are raised
    automatically and reported. A size not used since FastSD started needs confirm_slow=true (see generate_image).
    SD-Turbo output is under the Stability AI Community License (free commercial use under US$1M annual revenue)."""
    src = find_image(path)
    if aspect is None:
        with PILImage.open(src) as im:
            aspect = sdcore.nearest_aspect(*im.size)
    w, h = sdcore.aspect_size(aspect)
    gate = _slow_gate(w, h, confirm_slow)
    if gate:
        return ImageResult(gate)
    sd = _seeds(seed, 1)[0]
    nm = sdcore.safe_name(name, f"{src.stem[:40]}-restyle")
    body = sdcore.build_request(prompt, aspect, seed=sd, steps=1, name=nm, mode="img2img", init_image=src,
                                strength=strength, roots=apps.roots())
    res = _run_generate(body)
    res["seed"] = sd
    return _result([res], prompt, nm, f"restyled {_rel(src)} at strength {strength:g}")


def upscale(path: str, name: str = "") -> ImageResult:
    """2x upscale of an image (factor 2 only). Uses FastSD's upscaler when it works on this machine, otherwise
    LANCZOS plus a mild unsharp mask; the result says which method ran. Saved next to the other inputs."""
    src = find_image(path)
    nm = sdcore.safe_name(name, src.stem[:50] + "-2x")
    cfg = config.get()
    if cfg.fake_sd:
        with PILImage.open(src) as im:
            out = sdcore.upscale_lanczos(im)
        cfg.inputs.mkdir(parents=True, exist_ok=True)
        dest = cfg.inputs / (nm + ".png")
        out.save(dest)
        res = {"path": str(dest), "width": out.width, "height": out.height, "method": "lanczos+unsharp (stub)"}
    else:
        res = apps.fastsd.upscale({"path": str(src), "factor": 2, "name": nm})
    dest = Path(res["path"])
    return ImageResult(f"saved {dest}\nimage path: {_rel(dest)}\n{res['width']}x{res['height']} (2x of {src.name}); "
                       f"method: {res['method']}", PILImage.open(dest))


# --------------------------------------------------------------------------- #
# CLI parts: funkbox image generate | restyle | upscale   (edit lives in funkbox.stills.cli)
# --------------------------------------------------------------------------- #
def add_cli(sub) -> None:
    ap = image_group(sub)
    p = ap.add_parser("generate", help="text-to-image with FastSD (n>1 gives variations and a sheet)")
    p.add_argument("prompt")
    p.add_argument("--aspect", default="square", choices=sorted(sdcore.ASPECTS))
    p.add_argument("--seed", type=int, default=-1)
    p.add_argument("-n", type=int, default=1)
    p.add_argument("--negative-prompt", default="")
    p.add_argument("--name", default="")
    p.add_argument("--steps", type=int, default=1)
    p.add_argument("--confirm-slow", action="store_true", help="allow a size that costs a 2-3 min model recompile")
    p.set_defaults(fn=lambda a: _show(generate(a.prompt, a.aspect, a.seed, a.n, a.negative_prompt, a.name,
                                               a.steps, a.confirm_slow)))
    p = ap.add_parser("restyle", help="img2img: re-render a picture in a new style")
    p.add_argument("path")
    p.add_argument("prompt")
    p.add_argument("--strength", type=float, default=0.5)
    p.add_argument("--aspect", choices=sorted(sdcore.ASPECTS))
    p.add_argument("--seed", type=int, default=-1)
    p.add_argument("--name", default="")
    p.add_argument("--confirm-slow", action="store_true")
    p.set_defaults(fn=lambda a: _show(restyle(a.path, a.prompt, a.strength, a.aspect, a.seed, a.name,
                                              a.confirm_slow)))
    p = ap.add_parser("upscale", help="2x upscale (FastSD's upscaler, else LANCZOS + unsharp)")
    p.add_argument("path")
    p.add_argument("--name", default="")
    p.set_defaults(fn=lambda a: _show(upscale(a.path, a.name)))


def image_group(sub):
    """The shared ``funkbox image`` verb group (stills adds ``edit`` to the same group)."""
    cache = getattr(sub, "_funkbox_image", None)
    if cache is None:
        ap = sub.add_parser("image", help="images: generate, restyle, upscale (FastSD) and edit (no AI)")
        cache = ap.add_subparsers(dest="verb", required=True, metavar="{generate,restyle,upscale,edit}")
        sub._funkbox_image = cache
    return cache


def _show(res: ImageResult) -> int:
    print(res.text)
    return 1 if res.text.startswith("NOT STARTED") else 0

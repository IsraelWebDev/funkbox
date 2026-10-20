"""Localhost-only FastSD launcher for funk studio. Written for funkbox.

Why this exists instead of upstream start-mcpserver.bat:
  * upstream binds 0.0.0.0 with no auth (LAN-exposed); this binds 127.0.0.1;
  * upstream passes FastApiMCP-only kwargs to FastAPI() and crashes on current fastapi;
  * upstream /generate takes only a prompt; /studio/generate adds size, steps, seed, img2img
    (init_image + strength) and saves straight into a folder the caller names (restricted to
    STUDIO_OUT); /studio/upscale does a 2x upscale.
Forces OpenVINO + SD-Turbo + tiny autoencoder on the Intel iGPU (DEVICE=GPU).
Copy this file AND sdcore.py into the fastsdcpu folder (install.py does); run with
fastsdcpu\\env\\Scripts\\python.exe.
"""
import os
import sys
import threading
import time
import uuid
from pathlib import Path

os.environ.setdefault("DEVICE", "GPU")
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)             # sdcore.py sits next to this file
os.chdir(os.path.join(here, "src"))
sys.path.insert(0, os.getcwd())
os.makedirs("results", exist_ok=True)
STUDIO_OUT = os.path.abspath(os.environ.get("STUDIO_OUT", os.path.join(here, "results")))
os.makedirs(STUDIO_OUT, exist_ok=True)
# folders an init_image / upscale source may come from: STUDIO_OUT plus the funkbox roots
STUDIO_ROOTS = [STUDIO_OUT] + [r for r in os.environ.get("STUDIO_ROOTS", "").split(os.pathsep) if r]

import fastapi  # noqa: E402

_FastAPI = fastapi.FastAPI


class _PatchedFastAPI(_FastAPI):
    def __init__(self, *a, describe_all_responses=None, describe_full_response_schema=None, **k):
        super().__init__(*a, **k)


fastapi.FastAPI = _PatchedFastAPI

import uvicorn  # noqa: E402
from fastapi_mcp import FastApiMCP  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import backend.api.mcp_server as srv  # noqa: E402
import sdcore  # noqa: E402
from PIL import Image  # noqa: E402

S = srv.app_settings.settings
L = S.lcm_diffusion_setting
L.use_openvino = True
L.openvino_lcm_model_id = "rupeshs/sd-turbo-openvino"
L.use_tiny_auto_encoder = True
L.inference_steps = 1
L.guidance_scale = 1.0
L.image_width = L.image_height = 512
L.number_of_images = 1

_lock = threading.Lock()
_last_size = [None]


class StudioGen(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = Field(512, ge=256, le=1024)
    height: int = Field(512, ge=256, le=1024)
    steps: int = Field(1, ge=1, le=sdcore.MAX_STEPS)
    seed: int = -1
    name: str = ""
    mode: str = "txt2img"                         # "txt2img" | "img2img"
    init_image: str = ""                          # img2img: a file inside STUDIO_OUT or the funkbox roots
    strength: float = Field(0.5, ge=sdcore.STRENGTH_MIN, le=sdcore.STRENGTH_MAX)


class StudioUp(BaseModel):
    path: str
    factor: int = 2
    name: str = ""


def _out_path(name: str, fallback: str) -> str:
    return os.path.join(STUDIO_OUT, sdcore.safe_name(name, fallback) + ".png")


@srv.app.post("/studio/generate", operation_id="studio_generate")
def studio_generate(req: StudioGen) -> dict:
    w, h = req.width - req.width % 64, req.height - req.height % 64
    if req.mode not in ("txt2img", "img2img"):
        return {"ok": False, "error": "mode must be txt2img or img2img"}
    steps, note, init = req.steps, None, None
    if req.mode == "img2img":
        try:
            src = sdcore.fence_path(req.init_image, [Path(r) for r in STUDIO_ROOTS])
        except sdcore.SDError as e:
            return {"ok": False, "error": str(e)}
        init = sdcore.cover_resize(Image.open(src), w, h)
        steps, note = sdcore.clamp_steps(req.steps, req.strength)
    with _lock:
        L.prompt = req.prompt
        L.negative_prompt = req.negative_prompt
        L.image_width, L.image_height = w, h
        L.inference_steps = steps
        L.use_seed = req.seed >= 0
        if req.seed >= 0:
            L.seed = req.seed
        if req.mode == "img2img":
            L.diffusion_task = "image_to_image"
            L.init_image = init
            L.strength = req.strength
        else:
            L.diffusion_task = "text_to_image"
        reshape = _last_size[0] not in (None, (w, h))
        t0 = time.perf_counter()
        try:
            images = srv.context.generate_text_to_image(S, reshape=reshape, device=os.environ["DEVICE"])
        finally:
            L.diffusion_task = "text_to_image"
            L.init_image = None
        dt = time.perf_counter() - t0
        _last_size[0] = (w, h)
    if not images:
        return {"ok": False, "error": getattr(srv.context, "error", "generation failed")}
    path = _out_path(req.name, f"sd-{uuid.uuid4().hex[:8]}")
    images[0].save(path)
    res = {"ok": True, "path": path, "seconds": round(dt, 2), "width": w, "height": h, "steps": steps}
    if note:
        res["steps_note"] = note
    return res


@srv.app.post("/studio/upscale", operation_id="studio_upscale")
def studio_upscale(req: StudioUp) -> dict:
    """2x only. FastSD's own upscaler when it works on this machine, else LANCZOS + unsharp."""
    if req.factor != sdcore.UPSCALE_FACTOR:
        return {"ok": False, "error": "only factor 2 is supported"}
    try:
        src = sdcore.fence_path(req.path, [Path(r) for r in STUDIO_ROOTS])
    except sdcore.SDError as e:
        return {"ok": False, "error": str(e)}
    im = Image.open(src).convert("RGB")
    out, method = None, "lanczos+unsharp"
    with _lock:
        try:                                         # FastSD's EDSR ONNX x2 upscaler (CPU)
            from backend.upscale.edsr_upscale_onnx import upscale_edsr_2x
            cand = upscale_edsr_2x(str(src))
            if cand is not None and cand.size == (im.width * 2, im.height * 2):
                out, method = cand.convert("RGB"), "fastsd-edsr"
        except Exception:                            # noqa: BLE001 - any failure falls back
            out = None
    if out is None:
        out = sdcore.upscale_lanczos(im)
    path = _out_path(req.name, src.stem + "-2x")
    out.save(path)
    return {"ok": True, "path": path, "width": out.width, "height": out.height, "method": method}


@srv.app.get("/studio/health", operation_id="studio_health")
def studio_health() -> dict:
    return {"ok": True, "device": os.environ["DEVICE"], "out": STUDIO_OUT}


FastApiMCP(srv.app, name="FastSDCPU MCP", description="FastSD CPU text-to-image (local)").mount()
uvicorn.run(srv.app, host="127.0.0.1", port=int(os.environ.get("FASTSD_PORT", "8000")), log_level="warning")

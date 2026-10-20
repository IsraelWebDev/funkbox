"""Localhost-only FastSD launcher for funk studio. Written for funkbox.

Why this exists instead of upstream start-mcpserver.bat:
  * upstream binds 0.0.0.0 with no auth (LAN-exposed); this binds 127.0.0.1;
  * upstream passes FastApiMCP-only kwargs to FastAPI() and crashes on current fastapi;
  * upstream /generate takes only a prompt; /studio/generate adds size, steps, seed and
    saves straight into a folder the caller names (restricted to STUDIO_OUT).
Forces OpenVINO + SD-Turbo + tiny autoencoder on the Intel iGPU (DEVICE=GPU).
Copy into the fastsdcpu folder; run with fastsdcpu\\env\\Scripts\\python.exe.
"""
import os
import sys
import threading
import time
import uuid

os.environ.setdefault("DEVICE", "GPU")
here = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.join(here, "src"))
sys.path.insert(0, os.getcwd())
os.makedirs("results", exist_ok=True)
STUDIO_OUT = os.path.abspath(os.environ.get("STUDIO_OUT", os.path.join(here, "results")))
os.makedirs(STUDIO_OUT, exist_ok=True)

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
    steps: int = Field(1, ge=1, le=8)
    seed: int = -1
    name: str = ""


@srv.app.post("/studio/generate", operation_id="studio_generate")
def studio_generate(req: StudioGen) -> dict:
    w, h = req.width - req.width % 64, req.height - req.height % 64
    with _lock:
        L.prompt = req.prompt
        L.negative_prompt = req.negative_prompt
        L.image_width, L.image_height = w, h
        L.inference_steps = req.steps
        L.use_seed = req.seed >= 0
        if req.seed >= 0:
            L.seed = req.seed
        reshape = _last_size[0] not in (None, (w, h))
        t0 = time.perf_counter()
        images = srv.context.generate_text_to_image(S, reshape=reshape, device=os.environ["DEVICE"])
        dt = time.perf_counter() - t0
        _last_size[0] = (w, h)
    if not images:
        return {"ok": False, "error": getattr(srv.context, "error", "generation failed")}
    safe = "".join(c for c in req.name if c.isalnum() or c in "-_")[:60] or f"sd-{uuid.uuid4().hex[:8]}"
    path = os.path.join(STUDIO_OUT, safe + ".png")
    images[0].save(path)
    return {"ok": True, "path": path, "seconds": round(dt, 2), "width": w, "height": h}


@srv.app.get("/studio/health", operation_id="studio_health")
def studio_health() -> dict:
    return {"ok": True, "device": os.environ["DEVICE"], "out": STUDIO_OUT}


FastApiMCP(srv.app, name="FastSDCPU MCP", description="FastSD CPU text-to-image (local)").mount()
uvicorn.run(srv.app, host="127.0.0.1", port=int(os.environ.get("FASTSD_PORT", "8000")), log_level="warning")

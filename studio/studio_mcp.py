#!/usr/bin/env python3
"""
funk studio: one MCP server that starts, checks and stops the heavy local apps on demand.

  status()                      what's running (FastSD on :8000, GIMP MCP on :9877)
  start(service)                "fastsd" | "gimp" | "all": idempotent, waits until ready
  stop(service, force=False)    stops FastSD; GIMP only with force=True (unsaved work!)
  generate_image(prompt, aspect, n, ...)  text-to-image via FastSD (auto-starts it), saved into
                                funkbox/inputs so funk2 recipes and posters can use it directly; n>1 gives
                                variations as a contact sheet plus one file each
  restyle_image(path, prompt, strength)    img2img: re-render an existing picture in a new style
  upscale_image(path)                      2x upscale (FastSD's upscaler, else LANCZOS + unsharp)

With STUDIO_FAKE_SD=1 the three image tools use a deterministic procedural stub and never start FastSD
(that is what the tests use).

FastSD is started lazily and shut down after STUDIO_FASTSD_IDLE_MIN idle minutes (default 20) if this
server started it, so its ~4 GB of RAM is only held while you're actually generating.
GIMP is launched with its MCP plugin already listening (no menu clicking).

Register: python install.py --register desktop (or code). Logs go to stderr and <funkbox>/out/studio.log.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402  (studio/paths.py: env, then funkbox.toml, then vendor/ defaults)
import sdcore  # noqa: E402  (studio/sdcore.py: request building, size tracker, test stub)

FUNK_HOME = Path(os.environ.get("FUNK_HOME", HERE.parent)).resolve()
INPUTS = Path(os.environ.get("FUNK_INPUTS") or (FUNK_HOME / "inputs")).expanduser().resolve()
OUT = Path(os.environ.get("FUNK_OUT") or (FUNK_HOME / "out")).expanduser().resolve()
PROJECTS = Path(os.environ.get("FUNK_PROJECTS") or (FUNK_HOME / "projects")).expanduser().resolve()
#: where an init image / upscale source may live (the launcher enforces the same list)
ROOTS = [INPUTS, OUT, PROJECTS, FUNK_HOME]
LOG_PATH = FUNK_HOME / "out" / "studio.log"
FASTSD_DIR = paths.fastsd_dir()
FASTSD_PORT = int(os.environ.get("FASTSD_PORT", "8000"))
GIMP_PORT = int(os.environ.get("GIMP_MCP_PORT", "9877"))
IDLE_MIN = float(os.environ.get("STUDIO_FASTSD_IDLE_MIN", "20"))
WIN = os.name == "nt"

INPUTS.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(LOG_PATH)])
log = logging.getLogger("studio")

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore

sys.path.insert(0, str(HERE.parent))
from stills.sheet import contact_sheet  # noqa: E402  (Pillow only; no GL)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(port: int, timeout: float, also=lambda: True) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if port_open(port) and also():
            return True
        time.sleep(1.0)
    return False


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    # explicit no-proxy: corporate/system proxies must never see localhost traffic
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def _detached_flags() -> int:
    if not WIN:
        return 0
    return (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW)


def pid_on_port(port: int) -> int | None:
    if not WIN:
        return None
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            return int(parts[4])
    return None


def kill_tree(pid: int) -> None:
    if WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, 15)


def gimp_exe() -> Path | None:
    env = paths.gimp_exe_setting()
    if env and Path(env).exists():
        return Path(env)
    roots = []
    if WIN:
        roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
                 Path(os.environ.get("ProgramFiles", ""))]
    for root in roots:
        for d in sorted(root.glob("GIMP 3*"), reverse=True):
            for name in ("gimp-3.2.exe", "gimp-3.exe", "gimp.exe"):
                p = d / "bin" / name
                if p.exists():
                    return p
    found = shutil.which("gimp-3.2") or shutil.which("gimp")
    return Path(found) if found else None


def gimp_running() -> bool:
    if not WIN:
        return False
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq gimp-3*", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return "gimp-3" in out.lower()


# --------------------------------------------------------------------------- #
# services
# --------------------------------------------------------------------------- #
class FastSD:
    def __init__(self) -> None:
        self.started_by_us = False
        self.last_used = time.monotonic()
        self.lock = threading.Lock()
        self.busy = 0

    def healthy(self) -> bool:
        if not port_open(FASTSD_PORT):
            return False
        try:
            return bool(http_json("GET", f"http://127.0.0.1:{FASTSD_PORT}/studio/health", timeout=3).get("ok"))
        except Exception:
            return False

    def start(self, timeout: float = 240) -> str:
        with self.lock:
            if self.healthy():
                return "FastSD already running"
            if port_open(FASTSD_PORT):
                return (f"port {FASTSD_PORT} is taken by something that isn't the studio FastSD "
                        "launcher (upstream start-mcpserver.bat?). Stop it and retry.")
            py = paths.fastsd_python(FASTSD_DIR)
            launcher = FASTSD_DIR / "start_local_mcp.py"
            if not py.exists() or not launcher.exists():
                return f"FastSD not installed where expected ({FASTSD_DIR})"
            env = {**os.environ, "DEVICE": os.environ.get("FASTSD_DEVICE", "GPU"),
                   "STUDIO_OUT": str(INPUTS), "FASTSD_PORT": str(FASTSD_PORT),
                   "STUDIO_ROOTS": os.pathsep.join(str(r) for r in ROOTS),
                   "PATH": os.environ.get("PATH", "") + os.pathsep
                   + str(FASTSD_DIR / "env" / "Lib" / "site-packages" / "openvino" / "libs")}
            logf = open(FASTSD_DIR / "_studio_fastsd.log", "ab")
            subprocess.Popen([str(py), str(launcher)], cwd=str(FASTSD_DIR), env=env,
                             stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                             creationflags=_detached_flags(), close_fds=True)
            self.started_by_us = True
            sizes.clear()                       # a fresh process has compiled nothing yet
            self.last_used = time.monotonic()
            t0 = time.monotonic()
            log.info("starting FastSD")
            if not wait_port(FASTSD_PORT, timeout, self.healthy):
                return (f"FastSD did not come up within {timeout:.0f}s; see "
                        f"{FASTSD_DIR / '_studio_fastsd.log'}")
            return f"FastSD started in {time.monotonic() - t0:.0f}s"

    def stop(self) -> str:
        pid = pid_on_port(FASTSD_PORT)
        if not pid:
            return "FastSD not running"
        kill_tree(pid)
        self.started_by_us = False
        sizes.clear()
        return f"FastSD stopped (pid {pid})"

    def _post(self, endpoint: str, body: dict) -> dict:
        msg = self.start()
        if not self.healthy():
            raise RuntimeError(msg)
        self.last_used = time.monotonic()
        self.busy += 1
        try:
            # first call after start loads/compiles the model: allow a long time
            res = http_json("POST", f"http://127.0.0.1:{FASTSD_PORT}/studio/{endpoint}", body, timeout=900)
        finally:
            self.busy -= 1
            self.last_used = time.monotonic()
        if not res.get("ok"):
            raise RuntimeError(res.get("error", f"{endpoint} failed"))
        return res

    def generate(self, body: dict) -> dict:
        return self._post("generate", body)

    def upscale(self, body: dict) -> dict:
        return self._post("upscale", body)

    def idle_reaper(self) -> None:
        while True:
            time.sleep(30)
            if (IDLE_MIN > 0 and self.started_by_us and not self.busy and not self.lock.locked()
                    and time.monotonic() - self.last_used > IDLE_MIN * 60 and port_open(FASTSD_PORT)):
                log.info("FastSD idle for %.0f min: %s", IDLE_MIN, self.stop())


class Gimp:
    """GIMP is started by the user (desktop shortcut "GIMP + MCP", i.e. studio/GIMP-MCP.cmd), not by
    this server: anything an MCP server launches lives in Claude Desktop's sandbox and is killed with
    it, which would take unsaved GIMP work along. The shortcut opens GIMP with the MCP plug-in
    server already listening, and GIMP then lives independently of Claude."""

    SHORTCUT = "GIMP + MCP"

    def start(self, wait: float = 0) -> str:
        if port_open(GIMP_PORT):
            return "GIMP MCP already listening"
        if gimp_running():
            msg = ("GIMP is open but its MCP server isn't on. Ask the user to click "
                   "Tools > MCP > Start MCP Server in GIMP (needs an image open), or to close GIMP and "
                   f"double-click the '{self.SHORTCUT}' desktop shortcut.")
        else:
            msg = (f"GIMP isn't running. Ask the user to double-click the '{self.SHORTCUT}' shortcut on "
                   "their desktop (opens GIMP with the MCP server on, ~15 s), then call status().")
        if wait > 0 and wait_port(GIMP_PORT, wait):
            return "GIMP MCP is now listening"
        return msg

    def stop(self, force: bool) -> str:
        return ("GIMP is the user's app (it may hold unsaved work), so studio never closes it. "
                "Ask the user to close it.")


sizes = sdcore.SizeTracker()
fastsd = FastSD()
gimp = Gimp()
threading.Thread(target=fastsd.idle_reaper, daemon=True).start()

server = _Server(
    "studio",
    instructions=(
        "Starts/stops the local creative apps on demand. Call status() first. generate_image() "
        "auto-starts FastSD (first image after a cold start takes ~30-60s, then ~5s each) and saves "
        "into the funk inputs folder, so the returned file name can go straight into a funk2 recipe "
        "image layer or a poster image layer (path 'inputs/<name>.png'). Pick an aspect (square, portrait, "
        "landscape, story, wide); a size that has not been used since FastSD started costs a ~2-3 min model "
        "recompile, so the tool tells you first and waits for confirm_slow=true. Use n=3-4 to explore variations, "
        "restyle_image to re-render a picture in a new style, upscale_image for 2x. Never ask the image model for "
        "a product or for text: composite the real product and exact copy with the stills server. Before using the "
        "gimp tools call start('gimp'): if GIMP "
        "isn't up it tells you what to ask the user (double-click the 'GIMP + MCP' desktop shortcut); "
        "then call start('gimp', wait_s=60)."
    ),
)


def _status() -> dict:
    return {
        "fastsd": {"running": fastsd.healthy(), "port": FASTSD_PORT,
                   "started_by_studio": fastsd.started_by_us,
                   "idle_shutdown_min": IDLE_MIN},
        "gimp": {"mcp_listening": port_open(GIMP_PORT), "app_open": gimp_running(),
                 "port": GIMP_PORT, "exe": str(gimp_exe() or "")},
        "inputs_folder": str(INPUTS),
        "image_backend": "stub (STUDIO_FAKE_SD)" if sdcore.fake_enabled() else "fastsd",
        "compiled_sizes": sorted(f"{w}x{h}" for w, h in sizes.seen),
    }


def _safe(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # tools never raise into the client
            log.exception("tool %s failed", fn.__name__)
            return f"ERROR: {type(e).__name__}: {e}"
    return wrapper


@server.tool()
@_safe
def status() -> str:
    """Which local services are running."""
    return json.dumps(_status(), indent=2)


@server.tool()
@_safe
def start(service: str = "all", wait_s: float = 0) -> str:
    """Start 'fastsd', 'gimp' or 'all'. Idempotent. FastSD is launched and awaited. GIMP is opened by
    the user via the 'GIMP + MCP' desktop shortcut: this returns what to ask them, or, with wait_s>0,
    waits that long for GIMP's MCP port after you've asked."""
    service = service.lower().strip()
    if service not in ("fastsd", "gimp", "all"):
        return "service must be fastsd, gimp or all"
    out = []
    if service in ("gimp", "all"):
        out.append("gimp: " + gimp.start(wait=wait_s))
    if service in ("fastsd", "all"):
        out.append("fastsd: " + fastsd.start())
    return "\n".join(out)


@server.tool()
@_safe
def stop(service: str = "fastsd", force: bool = False) -> str:
    """Stop 'fastsd' (frees ~4 GB RAM). GIMP is never closed by studio (unsaved work)."""
    service = service.lower().strip()
    if service == "fastsd":
        return fastsd.stop()
    if service == "gimp":
        return gimp.stop(force)
    if service == "all":
        return fastsd.stop() + "\n" + gimp.stop(force)
    return "service must be fastsd, gimp or all"


_LICENCE = ("Licence note: the default model is SD-Turbo, under the Stability AI Community License "
            "(commercial use is free below US$1M annual revenue; Stability asks commercial users to register).")


def _png(im, side: int = 640) -> Image:
    from PIL import Image as PILImage
    t = im.convert("RGB").copy()
    t.thumbnail((side, side), PILImage.LANCZOS)
    buf = io.BytesIO()
    t.save(buf, format="PNG")
    return Image(data=buf.getvalue(), format="png")


def _rel(p: Path) -> str:
    return p.relative_to(FUNK_HOME).as_posix() if p.is_relative_to(FUNK_HOME) else str(p)


def _find_image(path: str) -> Path:
    """A path as the user/Claude gives it: absolute, or relative to inputs/, the funkbox folder or out/."""
    p = Path(str(path).replace("\\", "/")).expanduser()
    cands = [p] if p.is_absolute() else [INPUTS / p, FUNK_HOME / p, OUT / p]
    for c in cands:
        try:
            return sdcore.fence_path(c, ROOTS)
        except sdcore.SDError as e:
            last = e
    raise last


def _fake_generate(body: dict) -> dict:
    from PIL import Image as PILImage
    t0 = time.perf_counter()
    w, h = body["width"], body["height"]
    init = PILImage.open(body["init_image"]) if body.get("init_image") else None
    im = sdcore.fake_image(body["prompt"], w, h, max(0, body["seed"]), init, body.get("strength", 0.5))
    path = INPUTS / (sdcore.safe_name(body.get("name", ""), "stub-" + uuid_hex()) + ".png")
    im.save(path)
    res = {"ok": True, "path": str(path), "seconds": round(time.perf_counter() - t0, 2), "width": w,
           "height": h, "steps": body["steps"]}
    if body.get("steps_note"):
        res["steps_note"] = body["steps_note"]
    return res


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def _run_generate(body: dict) -> dict:
    res = _fake_generate(body) if sdcore.fake_enabled() else fastsd.generate(body)
    sizes.mark(res["width"], res["height"])
    return res


def _slow_gate(w: int, h: int, confirm_slow: bool) -> str | None:
    """The warning to return INSTEAD of generating when this size has not been compiled yet."""
    warn = sizes.warning(w, h)
    if warn and not confirm_slow:
        return (f"NOT STARTED: {warn}. Tell the user, then call again with confirm_slow=true. "
                "(512x512 'square' stays warm.)")
    return None


def _seeds(seed: int, n: int) -> list[int]:
    import random
    base = int(seed) if int(seed) >= 0 else (0 if sdcore.fake_enabled() else random.randrange(2 ** 31))
    return [base + i for i in range(n)]


def _result(results: list[dict], prompt: str, base_name: str, extra: str = "") -> list:
    from PIL import Image as PILImage
    paths = [Path(r["path"]) for r in results]
    lines = []
    for r, p, sd in zip(results, paths, [r["seed"] for r in results]):
        lines.append(f"  seed {sd}: {p}  (funk2 / poster image path: {_rel(p)})  {r['width']}x{r['height']} in {r['seconds']}s")
    notes = sorted({r["steps_note"] for r in results if r.get("steps_note")})
    head = f"saved {len(paths)} image(s) for {prompt!r}" if len(paths) > 1 else f"saved {paths[0]}"
    text = "\n".join([head] + (lines if len(paths) > 1 else [lines[0]]) + notes + ([extra] if extra else []))
    if len(paths) == 1:
        return [text, _png(PILImage.open(paths[0]))]
    ims = [PILImage.open(p) for p in paths]
    sheet = contact_sheet(ims, [f"{i + 1}: seed {r['seed']}" for i, r in enumerate(results)])
    OUT.mkdir(parents=True, exist_ok=True)
    sp = OUT / f"sheet-{base_name}.png"
    sheet.save(sp)
    return [text + f"\ncontact sheet: {sp}", _png(sheet, 1000)]


@server.tool()
@_safe
def generate_image(prompt: str, aspect: str = "square", seed: int = -1, n: int = 1, negative_prompt: str = "",
                   name: str = "", steps: int = 1, confirm_slow: bool = False) -> list:
    """Text-to-image with FastSD (SD-Turbo, OpenVINO on the iGPU); auto-starts FastSD. Aspect presets:
    square 512x512, portrait 512x768, landscape 768x512, story 512x896, wide 896x512. n=2-8 returns variations
    (seeds seed, seed+1, ... or random) as a contact sheet plus one file each in the inputs folder. seed -1 =
    random (the seeds used are reported). steps 1 is best for SD-Turbo. COST OF SIZE CHANGES: the model is
    compiled per image size (~2-3 min the first time at each size); this tool answers with a warning and does
    nothing until you pass confirm_slow=true. Never request a product or text in the prompt. SD-Turbo output is
    under the Stability AI Community License (free commercial use under US$1M annual revenue; Stability asks
    commercial users to register)."""
    if not 1 <= int(n) <= 8:
        return "ERROR: n must be 1..8"
    w, h = sdcore.aspect_size(aspect)
    gate = _slow_gate(w, h, confirm_slow)
    if gate:
        return gate
    seeds = _seeds(seed, int(n))
    base = sdcore.safe_name(name, f"still-{__import__('hashlib').sha256(prompt.encode()).hexdigest()[:6]}")
    results = []
    for i, sd in enumerate(seeds):
        nm = base if n == 1 else f"{base}-{i + 1}"
        body = sdcore.build_request(prompt, aspect, seed=sd, steps=steps, negative_prompt=negative_prompt, name=nm)
        res = _run_generate(body)
        res["seed"] = sd
        results.append(res)
    return _result(results, prompt, base)


@server.tool()
@_safe
def restyle_image(path: str, prompt: str, strength: float = 0.5, aspect: str | None = None, seed: int = -1,
                  name: str = "", confirm_slow: bool = False) -> list:
    """img2img: re-render an existing picture (a file in inputs/, out/ or the funkbox folder) following `prompt`.
    strength 0.05 stays close to the original, 0.95 nearly ignores it. aspect None picks the preset closest to the
    picture's shape (it is cover-cropped to that size). SD-Turbo needs steps*strength >= 1, so steps are raised
    automatically and reported. A size not used since FastSD started needs confirm_slow=true (see generate_image).
    SD-Turbo output is under the Stability AI Community License (free commercial use under US$1M annual revenue)."""
    from PIL import Image as PILImage
    src = _find_image(path)
    if aspect is None:
        with PILImage.open(src) as im:
            aspect = sdcore.nearest_aspect(*im.size)
    w, h = sdcore.aspect_size(aspect)
    gate = _slow_gate(w, h, confirm_slow)
    if gate:
        return gate
    sd = _seeds(seed, 1)[0]
    nm = sdcore.safe_name(name, f"{src.stem[:40]}-restyle")
    body = sdcore.build_request(prompt, aspect, seed=sd, steps=1, name=nm, mode="img2img", init_image=src,
                                strength=strength, roots=ROOTS)
    res = _run_generate(body)
    res["seed"] = sd
    return _result([res], prompt, nm, f"restyled {_rel(src)} at strength {strength:g}")


@server.tool()
@_safe
def upscale_image(path: str, name: str = "") -> list:
    """2x upscale of an image (factor 2 only). Uses FastSD's upscaler when it works on this machine, otherwise
    LANCZOS plus a mild unsharp mask; the result says which method ran. Saved next to the other inputs."""
    from PIL import Image as PILImage
    src = _find_image(path)
    nm = sdcore.safe_name(name, src.stem[:50] + "-2x")
    if sdcore.fake_enabled():
        with PILImage.open(src) as im:
            out = sdcore.upscale_lanczos(im)
        dest = INPUTS / (nm + ".png")
        out.save(dest)
        res = {"path": str(dest), "width": out.width, "height": out.height, "method": "lanczos+unsharp (stub)"}
    else:
        res = fastsd.upscale({"path": str(src), "factor": 2, "name": nm})
    dest = Path(res["path"])
    return [f"saved {dest}\nimage path: {_rel(dest)}\n{res['width']}x{res['height']} (2x of {src.name}); "
            f"method: {res['method']}", _png(PILImage.open(dest))]


if __name__ == "__main__":
    log.info("studio MCP starting: %s", json.dumps(_status()))
    server.run()

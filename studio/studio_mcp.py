#!/usr/bin/env python3
"""
funk studio: one MCP server that starts, checks and stops the heavy local apps on demand.

  status()                      what's running (FastSD on :8000, GIMP MCP on :9877)
  start(service)                "fastsd" | "gimp" | "all": idempotent, waits until ready
  stop(service, force=False)    stops FastSD; GIMP only with force=True (unsaved work!)
  generate_image(prompt, ...)   text-to-image via FastSD (auto-starts it), saved into
                                funkbox/inputs so funk2 recipes can use it directly; returns the image

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

FUNK_HOME = Path(os.environ.get("FUNK_HOME", HERE.parent)).resolve()
INPUTS = Path(os.environ.get("FUNK_INPUTS") or (FUNK_HOME / "inputs")).expanduser().resolve()
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
                   "PATH": os.environ.get("PATH", "") + os.pathsep
                   + str(FASTSD_DIR / "env" / "Lib" / "site-packages" / "openvino" / "libs")}
            logf = open(FASTSD_DIR / "_studio_fastsd.log", "ab")
            subprocess.Popen([str(py), str(launcher)], cwd=str(FASTSD_DIR), env=env,
                             stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                             creationflags=_detached_flags(), close_fds=True)
            self.started_by_us = True
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
        return f"FastSD stopped (pid {pid})"

    def generate(self, **kw) -> dict:
        msg = self.start()
        if not self.healthy():
            raise RuntimeError(msg)
        self.last_used = time.monotonic()
        self.busy += 1
        try:
            # first call after start loads/compiles the model: allow a long time
            res = http_json("POST", f"http://127.0.0.1:{FASTSD_PORT}/studio/generate", kw, timeout=900)
        finally:
            self.busy -= 1
            self.last_used = time.monotonic()
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "generation failed"))
        return res

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


fastsd = FastSD()
gimp = Gimp()
threading.Thread(target=fastsd.idle_reaper, daemon=True).start()

server = _Server(
    "studio",
    instructions=(
        "Starts/stops the local creative apps on demand. Call status() first. generate_image() "
        "auto-starts FastSD (first image after a cold start takes ~30-60s, then ~5s each) and saves "
        "into the funk inputs folder, so the returned file name can go straight into a funk2 recipe "
        "image layer (path 'inputs/<name>.png'). Before using the gimp tools call start('gimp'): if GIMP "
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


@server.tool()
@_safe
def generate_image(prompt: str, width: int = 512, height: int = 512, steps: int = 1,
                   seed: int = -1, negative_prompt: str = "", name: str = "") -> list:
    """Text-to-image with FastSD (SD-Turbo, OpenVINO on the iGPU). Auto-starts FastSD.
    Saves <funkbox>/inputs/<name>.png and returns the image. Sizes are rounded down to x64,
    256-1024. steps 1 is best for SD-Turbo; 2-4 adds detail slowly. seed -1 = random.
    COST OF SIZE CHANGES: the iGPU model is compiled per image size. 512x512 is warm (~3-5 s);
    the first image at any other size recompiles (~2.5 min on this laptop), then that size is fast
    until the size changes again. Prefer 512x512 and let funk2 'contain'/'cover' it."""
    res = fastsd.generate(prompt=prompt, width=width, height=height, steps=steps, seed=seed,
                          negative_prompt=negative_prompt, name=name)
    p = Path(res["path"])
    from PIL import Image as PILImage
    im = PILImage.open(p)
    im.thumbnail((640, 640))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    rel = p.relative_to(FUNK_HOME).as_posix() if p.is_relative_to(FUNK_HOME) else str(p)
    text = (f"saved {p}\nfunk2 image layer path: {rel}\n"
            f"{res['width']}x{res['height']} in {res['seconds']}s")
    return [text, Image(data=buf.getvalue(), format="png")]


if __name__ == "__main__":
    log.info("studio MCP starting: %s", json.dumps(_status()))
    server.run()

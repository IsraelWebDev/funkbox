"""funk apps: start, check and stop the heavy local apps on demand (FastSD on :8000, GIMP's MCP on :9877).

Plain functions (``status``, ``start``, ``stop``) return text. The MCP tools (``apps_*``) and the CLI
(``funkbox apps``) are thin wrappers over them. Every setting is read from ``funkbox.config`` when it is
used, never at import time.

FastSD is started lazily and shut down after STUDIO_FASTSD_IDLE_MIN idle minutes (default 20) if this process
started it, so its ~4 GB of RAM is only held while you are generating. GIMP is opened by the user (desktop
shortcut "GIMP + MCP"): anything a server launches lives in the client's sandbox and dies with it.

``funkbox/studio/start_local_mcp.py`` and ``sdcore.py`` run inside FastSD's own venv and stay standalone.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from funkbox import config
from funkbox.studio import sdcore  # numpy + Pillow only

HERE = Path(__file__).resolve().parent
WIN = os.name == "nt"
log = logging.getLogger("funkbox.studio")
_LOG_READY = False


def _ensure_log() -> None:
    """Send studio log lines to stderr and <out>/studio.log (once; stdout is the MCP protocol)."""
    global _LOG_READY
    if _LOG_READY:
        return
    _LOG_READY = True
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        out = config.get().out
        out.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(out / "studio.log"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def roots() -> list[Path]:
    """Where an init image / upscale source may live (the FastSD launcher enforces the same list)."""
    c = config.get()
    return [c.inputs, c.out, c.projects, c.home]


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


def fastsd_python(d: Path) -> Path:
    """The interpreter of FastSD's own venv (created by install.py as <dir>/env)."""
    for rel in (("env", "Scripts", "python.exe"), ("env", "bin", "python")):
        p = d.joinpath(*rel)
        if p.exists():
            return p
    return d / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def gimp_exe() -> Path | None:
    env = config.get().gimp_exe
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
def sync_launcher(fastsd_dir: Path) -> list[str]:
    """Copy studio/start_local_mcp.py and studio/sdcore.py into the FastSD folder when missing or out of
    date, so a funkbox update (git pull) never runs against a stale launcher. Returns the names copied."""
    import hashlib
    import shutil
    copied = []
    for name in ("start_local_mcp.py", "sdcore.py"):
        src, dst = HERE / name, fastsd_dir / name
        if not src.exists():
            continue
        if not dst.exists() or hashlib.sha256(src.read_bytes()).digest() != hashlib.sha256(dst.read_bytes()).digest():
            shutil.copy2(src, dst)
            copied.append(name)
    return copied


class FastSD:
    def __init__(self) -> None:
        self.started_by_us = False
        self.last_used = time.monotonic()
        self.lock = threading.Lock()
        self.busy = 0
        self._reaper = False

    def healthy(self) -> bool:
        port = config.get().fastsd_port
        if not port_open(port):
            return False
        try:
            return bool(http_json("GET", f"http://127.0.0.1:{port}/studio/health", timeout=3).get("ok"))
        except Exception:
            return False

    def start(self, timeout: float = 240) -> str:
        cfg = config.get()
        FASTSD_PORT, FASTSD_DIR = cfg.fastsd_port, cfg.fastsd_dir
        with self.lock:
            if self.healthy():
                return "FastSD already running"
            if port_open(FASTSD_PORT):
                return (f"port {FASTSD_PORT} is taken by something that isn't the studio FastSD "
                        "launcher (upstream start-mcpserver.bat?). Stop it and retry.")
            py = fastsd_python(FASTSD_DIR)
            if not py.exists():
                return f"FastSD not installed where expected ({FASTSD_DIR})"
            launcher = FASTSD_DIR / "start_local_mcp.py"
            synced = sync_launcher(FASTSD_DIR)
            if synced:
                log.info("refreshed FastSD launcher files: %s", ", ".join(synced))
            _ensure_log()
            if not self._reaper:
                self._reaper = True
                threading.Thread(target=self.idle_reaper, daemon=True).start()
            env = {**os.environ, "DEVICE": cfg.fastsd_device,
                   "STUDIO_OUT": str(cfg.inputs), "FASTSD_PORT": str(FASTSD_PORT),
                   "STUDIO_ROOTS": os.pathsep.join(str(r) for r in roots()),
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
        pid = pid_on_port(config.get().fastsd_port)
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
            res = http_json("POST", f"http://127.0.0.1:{config.get().fastsd_port}/studio/{endpoint}", body, timeout=900)
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
            cfg = config.get()
            idle = cfg.idle_min
            if (idle > 0 and self.started_by_us and not self.busy and not self.lock.locked()
                    and time.monotonic() - self.last_used > idle * 60 and port_open(cfg.fastsd_port)):
                log.info("FastSD idle for %.0f min: %s", idle, self.stop())


class Gimp:
    """GIMP is started by the user (desktop shortcut "GIMP + MCP", i.e. studio/GIMP-MCP.cmd), not by
    this server: anything an MCP server launches lives in Claude Desktop's sandbox and is killed with
    it, which would take unsaved GIMP work along. The shortcut opens GIMP with the MCP plug-in
    server already listening, and GIMP then lives independently of Claude."""

    SHORTCUT = "GIMP + MCP"

    def start(self, wait: float = 0) -> str:
        port = config.get().gimp_port
        if port_open(port):
            return "GIMP MCP already listening"
        if gimp_running():
            msg = ("GIMP is open but its MCP server isn't on. Ask the user to click "
                   "Tools > MCP > Start MCP Server in GIMP (needs an image open), or to close GIMP and "
                   f"double-click the '{self.SHORTCUT}' desktop shortcut.")
        else:
            msg = (f"GIMP isn't running. Ask the user to double-click the '{self.SHORTCUT}' shortcut on "
                   "their desktop (opens GIMP with the MCP server on, ~15 s), then call status().")
        if wait > 0 and wait_port(port, wait):
            return "GIMP MCP is now listening"
        return msg

    def stop(self, force: bool) -> str:
        return ("GIMP is the user's app (it may hold unsaved work), so studio never closes it. "
                "Ask the user to close it.")




# --------------------------------------------------------------------------- #
# the singletons and the public functions (what MCP tools and the CLI call)
# --------------------------------------------------------------------------- #
sizes = sdcore.SizeTracker()
fastsd = FastSD()
gimp = Gimp()


def status_dict() -> dict:
    c = config.get()
    return {
        "fastsd": {"running": fastsd.healthy(), "port": c.fastsd_port,
                   "started_by_studio": fastsd.started_by_us,
                   "idle_shutdown_min": c.idle_min},
        "gimp": {"mcp_listening": port_open(c.gimp_port), "app_open": gimp_running(),
                 "port": c.gimp_port, "exe": str(gimp_exe() or "")},
        "inputs_folder": str(c.inputs),
        "image_backend": "stub (STUDIO_FAKE_SD)" if c.fake_sd else "fastsd",
        "compiled_sizes": sorted(f"{w}x{h}" for w, h in sizes.seen),
    }


def status() -> str:
    """Which local services are running."""
    return json.dumps(status_dict(), indent=2)


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


def stop(service: str = "fastsd", force: bool = False) -> str:
    """Stop 'fastsd' (frees ~4 GB RAM). GIMP is never closed by funkbox (unsaved work)."""
    service = service.lower().strip()
    if service == "fastsd":
        return fastsd.stop()
    if service == "gimp":
        return gimp.stop(force)
    if service == "all":
        return fastsd.stop() + "\n" + gimp.stop(force)
    return "service must be fastsd, gimp or all"


# --------------------------------------------------------------------------- #
# CLI:  funkbox apps status | start | stop
# --------------------------------------------------------------------------- #
def add_cli(sub) -> None:
    ap = sub.add_parser("apps", help="local apps: FastSD (image model) and GIMP")
    asub = ap.add_subparsers(dest="verb", required=True, metavar="{status,start,stop}")
    p = asub.add_parser("status", help="what is running")
    p.set_defaults(fn=lambda a: print(status()) or 0)
    p = asub.add_parser("start", help="start fastsd (waits until ready) or explain how to open gimp")
    p.add_argument("service", nargs="?", default="all", choices=["fastsd", "gimp", "all"])
    p.add_argument("--wait", type=float, default=0, dest="wait_s", help="seconds to wait for GIMP's MCP port")
    p.set_defaults(fn=lambda a: print(start(a.service, a.wait_s)) or 0)
    p = asub.add_parser("stop", help="stop fastsd (GIMP is never closed)")
    p.add_argument("service", nargs="?", default="fastsd", choices=["fastsd", "gimp", "all"])
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=lambda a: print(stop(a.service, a.force)) or 0)

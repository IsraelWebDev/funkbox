#!/usr/bin/env python3
"""Render a promo day and record numbers the promo may quote, so its claims can be specific and still be checked.

    python promo/measure.py render $STATE/days/<date>   # renders final.mp4 (+ text audit), then writes measured.json
    python promo/measure.py facts  $STATE/days/<date>   # measured.json without rendering (repo facts only)

measured.json holds {"facts": {"M-...": {"value": ..., "say": "<plain sentence a promo may use>"}}}. A brief's claim
may cite an M-key instead of a FACTS.md id; the reviewer checks the words against "say". Keys:
  M-render     how long this video took to render, and on what (M-machine)
  M-machine    the machine: CPU cores and whether there was a GPU at all (software OpenGL = none)
  M-video      length, size and frame rate of this video
  M-audit      how many on-screen strings the text audit checked, and that all were exact
  M-tools, M-presets, M-shaders, M-tests   counts read from the code right now
  M-commits    what changed in funkbox since the previous promo day (subjects of commits), for "what's new"
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _run(*args: str, timeout: float = 120) -> str:
    try:
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


def machine() -> dict:
    gl = re.search(r"OpenGL\s+(.+)", _run(sys.executable, "-m", "funkbox", "doctor"))
    renderer = gl.group(1).strip() if gl else "unknown"
    software = bool(re.search(r"llvmpipe|softpipe|swiftshader|software", renderer, re.I))
    cpu = platform.processor() or ""
    try:
        cpu = next((l.split(":", 1)[1].strip() for l in Path("/proc/cpuinfo").read_text().splitlines()
                    if l.startswith("model name")), cpu)
    except OSError:
        pass
    cores = os.cpu_count() or 0
    where = (f"a {cores}-core CPU with no GPU (software OpenGL)" if software
             else f"a {cores}-core machine, graphics: {renderer.split('(')[0].strip()}")
    return {"value": {"cpu": cpu, "cores": cores, "opengl": renderer, "gpu": not software}, "say": where}


def repo_counts() -> dict:
    out = {}
    try:
        from funkbox.server import build_server
        n = len(build_server()._tool_manager.list_tools())
        out["M-tools"] = {"value": n, "say": f"Claude drives it through {n} MCP tools"}
    except Exception:                                       # noqa: BLE001 - a count we cannot read is not claimed
        pass
    listing = _run(sys.executable, "-m", "funkbox", "loop", "list")
    block = listing.split("PRESETS", 1)[1].split("\n\n", 1)[0] if "PRESETS" in listing else ""
    presets = re.findall(r"^\s{2}([a-z][a-z-]+)\s{2,}\S", block, re.M)
    shaders = sorted(p.stem for p in (ROOT / "shaders").glob("*.frag"))
    m = re.search(r"(\d+) tests? collected", _run(sys.executable, "-m", "pytest", "--collect-only", "-q", timeout=300))
    tests = int(m.group(1)) if m else 0
    if presets:
        out["M-presets"] = {"value": len(presets), "say": f"{len(presets)} ready-made loop looks"}
    out["M-shaders"] = {"value": len(shaders), "say": f"{len(shaders)} shader effects to layer"}
    if tests:
        out["M-tests"] = {"value": tests, "say": f"{tests} automated tests in the repo"}
    return out


def commits_since(day: Path) -> dict:
    prev = sorted(p.name for p in day.parent.iterdir() if p.is_dir() and p.name < day.name)
    since = prev[-1][:10] if prev else None
    log = _run("git", "log", "--no-merges", "--format=%h %s", *([f"--since={since}"] if since else ["-15"]), "--",
               "funkbox", "promo", "docs", "README.md", "install.py", "templates", "shaders")
    items = [l for l in log.splitlines() if l.strip() and not re.search(r"\bpromo\b", l)][:15]
    return {"value": items, "say": "changes since the last promo: " + ("; ".join(i.split(" ", 1)[1] for i in items)
                                                                      if items else "none")}


def video(day: Path) -> dict | None:
    from funkbox.reel import framing
    try:
        info = framing.probe_video(day / "final.mp4")
    except (OSError, ValueError):
        return None
    return {"value": {"seconds": round(info.duration or 0, 2), "width": info.width, "height": info.height,
                      "fps": round(info.fps or 0, 2)},
            "say": f"{info.duration:.0f} seconds, {info.width}x{info.height}"}


def audit(day: Path) -> dict | None:
    try:
        a = json.loads((day / "final.text_audit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    n = len(a.get("entries", []))
    return {"value": {"strings": n, "ok": bool(a.get("ok"))},
            "say": f"the text audit checked {n} on-screen strings: " + ("all exact" if a.get("ok") else "NOT all exact")}


def write(day: Path, render_s: float | None = None, frames: int | None = None) -> dict:
    facts = {"M-machine": machine(), **repo_counts(), "M-commits": commits_since(day)}
    for key, fn in (("M-video", video), ("M-audit", audit)):
        v = fn(day)
        if v:
            facts[key] = v
    if render_s is not None:
        where = facts["M-machine"]["say"]
        facts["M-render"] = {"value": {"seconds": round(render_s, 1), "frames": frames},
                             "say": f"this video rendered in {render_s:.0f} seconds on {where}"}
    doc = {"day": day.name, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "facts": facts}
    (day / "measured.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def render(day: Path) -> dict:
    t = time.perf_counter()
    r = subprocess.run([sys.executable, "-m", "funkbox", "reel", "render", str(day / "reel.json"), "--name", "final",
                        "--out", str(day), "--audit"], cwd=ROOT, capture_output=True, text=True)
    took = time.perf_counter() - t
    print((r.stdout + r.stderr).strip())
    if r.returncode:
        sys.exit(r.returncode)
    m = re.search(r"(\d+) frames rendered", r.stdout + r.stderr)
    return write(day, took, int(m.group(1)) if m else None)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("render", "facts"):
        sys.exit(__doc__)
    d = Path(sys.argv[2])
    doc = render(d) if sys.argv[1] == "render" else write(d)
    print("\n".join(f"{k}: {v['say']}" for k, v in doc["facts"].items()))

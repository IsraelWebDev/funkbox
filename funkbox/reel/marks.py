"""Recorded clips with named marks (spec 3.1).

A scene file drives a live page with Playwright and writes ``<scene>.webm`` plus ``<scene>.marks.json`` (mark name
-> seconds into the clip). A reel shot can then name a mark instead of a time, so a re-recording moves the cut to the
same mark without a reel edit:

    "in":  {"mark": "name-entered"},
    "out": {"mark": "result", "offset_ms": 300}

Recording is off by default (``funkbox reel record --record``). The browser may only load the scene's own origin.
Logs never print a step's text or a field's value.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MARK_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
STEP_KINDS = ("goto", "fill", "click", "wait_for")
STEP_KEYS = {"do", "url", "selector", "text", "pause_ms", "mark"}
OFFSET_MAX_MS = 60_000
PAUSE_MAX_MS = 10_000


class MarksError(ValueError):
    """Bad scene, bad mark reference or recorder failure. Messages name the field, never a value."""


# --------------------------------------------------------------------------- #
# Shot edges: a time or a mark, as written in the reel
# --------------------------------------------------------------------------- #
def parse_edge(v, where: str, errs: list[str]):
    """A shot's ``in`` or ``out``: seconds, or {"mark": name, "offset_ms": n}.
    Returns ("time", s) | ("mark", name, offset_s) | None (absent or bad; a bad one is added to ``errs``)."""
    if v is None:
        return None
    if isinstance(v, bool):
        errs.append(f"{where}: expected seconds or a mark, got {v!r}")
        return None
    if isinstance(v, (int, float)):
        if v < 0:
            errs.append(f"{where}: seconds must not be negative")
            return None
        return ("time", float(v))
    if isinstance(v, dict):
        bad = sorted(set(v) - {"mark", "offset_ms"})
        if bad:
            errs.append(f"{where}: unknown keys {bad}")
            return None
        name = v.get("mark")
        if not isinstance(name, str) or not MARK_RE.match(name):
            errs.append(f"{where}.mark: expected a mark name (lowercase letters, digits, dashes)")
            return None
        off = v.get("offset_ms", 0)
        if isinstance(off, bool) or not isinstance(off, (int, float)) or abs(off) > OFFSET_MAX_MS:
            errs.append(f"{where}.offset_ms: expected a number within +-{OFFSET_MAX_MS}")
            return None
        return ("mark", name, float(off) / 1000.0)
    errs.append(f'{where}: expected seconds or {{"mark": name, "offset_ms": n}}')
    return None


def marks_path(clip: Path) -> Path:
    """<scene>.webm -> <scene>.marks.json, beside it."""
    return clip.with_name(clip.stem + ".marks.json")


def resolve_trim(clip: Path, t_in, t_out, where: str, errs: list[str]) -> tuple[float, float] | None:
    """The (start, end) trim in seconds that a shot's in/out edges name in ``clip``, or None with ``errs`` filled."""
    if t_in is None or t_out is None:
        errs.append(f'{where}: "in" and "out" go together (give both, as seconds or marks)')
        return None
    marks: dict | None = None

    def at(edge, which: str) -> float | None:
        nonlocal marks
        if edge[0] == "time":
            return edge[1]
        if marks is None:
            mf = marks_path(clip)
            if not mf.exists():
                errs.append(f"{where}: no {mf.name} beside the clip; record the scene with `funkbox reel record`")
                return None
            try:
                marks = json.loads(mf.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError):
                errs.append(f"{where}: {mf.name} is not readable JSON")
                return None
            if not isinstance(marks, dict):
                errs.append(f"{where}: {mf.name} must map mark names to seconds")
                return None
        t = marks.get(edge[1])
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            errs.append(f"{where}.{which}: mark {edge[1]!r} is not in {marks_path(clip).name}")
            return None
        return max(0.0, float(t) + edge[2])

    a = at(t_in, "in")
    b = at(t_out, "out")
    if a is None or b is None:
        return None
    if b <= a:
        errs.append(f"{where}: out must come after in (in={a:.2f}s, out={b:.2f}s)")
        return None
    return (round(a, 3), round(b, 3))


# --------------------------------------------------------------------------- #
# Scene files
# --------------------------------------------------------------------------- #
def load_scene(src: str | Path) -> dict:
    """Read and check a scene file. Returns {"url", "steps"}; raises MarksError listing every problem."""
    try:
        d = json.loads(Path(src).read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as e:
        raise MarksError(f"cannot read scene {Path(src).name}: {e}") from None
    errs: list[str] = []
    if not isinstance(d, dict):
        raise MarksError("scene: must be a JSON object")
    url = d.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        errs.append("url: expected an absolute http(s) URL (the only origin the recorder may load)")
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps:
        errs.append("steps: expected a non-empty list")
        steps = []
    seen: set[str] = set()
    for i, st in enumerate(steps):
        w = f"steps[{i}]"
        if not isinstance(st, dict):
            errs.append(f"{w}: must be an object")
            continue
        bad = sorted(set(st) - STEP_KEYS)
        if bad:
            errs.append(f"{w}: unknown keys {bad}")
        kind = st.get("do")
        if kind not in STEP_KINDS:
            errs.append(f"{w}.do: expected one of {', '.join(STEP_KINDS)}")
            continue
        need = {"goto": ("url",), "fill": ("selector", "text"), "click": ("selector",), "wait_for": ("text",)}[kind]
        for k in need:
            if not isinstance(st.get(k), str) or not st.get(k):
                errs.append(f"{w}.{k}: required for {kind}")
        pause = st.get("pause_ms", 0)
        if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= PAUSE_MAX_MS:
            errs.append(f"{w}.pause_ms: expected an integer from 0 to {PAUSE_MAX_MS}")
        if "mark" in st:
            m = st["mark"]
            if not isinstance(m, str) or not MARK_RE.match(m):
                errs.append(f"{w}.mark: expected a mark name (lowercase letters, digits, dashes)")
            elif m in seen:
                errs.append(f"{w}.mark: duplicate mark name {m!r}")
            else:
                seen.add(m)
    if errs:
        raise MarksError("; ".join(errs))
    return {"url": url, "steps": steps}


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def record_scene(scene_path: str | Path, out_dir: str | Path | None = None, *, enabled: bool = False) -> dict:
    """Drive the scene's page in Chromium, write ``<out>/<scene>.webm`` and ``<out>/<scene>.marks.json``.
    Returns {"clip", "marks", "duration_s"}. Needs ``pip install playwright`` and ``playwright install chromium``."""
    if not enabled:
        raise MarksError("recording is off by default: pass --record to run the browser")
    scene = load_scene(scene_path)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise MarksError("recording needs Playwright: pip install playwright && playwright install chromium") from None
    src = Path(scene_path)
    name = src.name.split(".")[0]
    out = Path(out_dir) if out_dir else src.parent / "inputs"
    out.mkdir(parents=True, exist_ok=True)
    origin = scene["url"]
    marks: dict[str, float] = {}
    tmp = Path(tempfile.mkdtemp(prefix="funk-rec-"))

    def allowed(u: str) -> bool:
        return _same_origin(u, origin) or urlsplit(u).scheme in ("about", "data", "blob")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    record_video_dir=str(tmp),
                    record_video_size={"width": 1280, "height": 720},
                )
                ctx.route("**/*", lambda route: route.continue_() if allowed(route.request.url) else route.abort())
                page = ctx.new_page()
                t0 = time.monotonic()
                first = scene["steps"][0]
                if first.get("do") != "goto":
                    page.goto(origin)
                for st in scene["steps"]:
                    if st.get("mark"):
                        marks[st["mark"]] = round(time.monotonic() - t0, 3)
                    kind = st["do"]
                    if kind == "goto":
                        page.goto(urljoin(origin, st["url"]))
                    elif kind == "fill":
                        page.fill(st["selector"], st["text"])
                    elif kind == "click":
                        page.click(st["selector"])
                    elif kind == "wait_for":
                        page.get_by_text(st["text"]).first.wait_for()
                    if st.get("pause_ms"):
                        page.wait_for_timeout(st["pause_ms"])
                video = page.video
                ctx.close()
                clip = out / f"{name}.webm"
                video.save_as(str(clip))
            finally:
                browser.close()
    finally:
        for f in tmp.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass
    total = round(time.monotonic() - t0, 3)
    marks_file = out / f"{name}.marks.json"
    marks_file.write_text(json.dumps(marks, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"clip": str(clip), "marks": str(marks_file), "duration_s": total, "mark_names": sorted(marks)}

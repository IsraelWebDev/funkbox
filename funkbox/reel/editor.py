"""A timeline editor for ``*.reel.json`` that you and Claude share.

``funkbox reel timeline X.reel.json`` (or the ``reel_timeline`` tool) serves a page on http://127.0.0.1 that shows the
reel as a strip of shots, each as wide as its duration, with a frame of each shot. You drag shots to reorder them,
drag a shot's right edge to change its length (in beats when the reel has a beat grid), set a clip's in and out
points against frames of the source, change captions and voice lines, duplicate or delete shots, and render a draft
to watch. Saving validates the reel first (an invalid reel is never written), keeps the previous file as
``.reel.json.bak`` and appends what changed to ``<reel>.edits.jsonl``.

The file is the shared state. When Claude edits the reel, the page notices within two seconds and reloads (or, if you
have unsaved changes, asks which to keep); changes made outside the page are logged too, with ``"by": "file"``.
``reel_timeline(..., action="changes")`` gives Claude the log in plain words ("moved 'clark' from 2 to 5", "'rim':
duration beats:1 -> beats:2"), so it knows what you did before it changes anything.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

THUMB_W = 216
_SERVERS: dict[str, "Editor"] = {}


# --------------------------------------------------------------------------- what changed
def _label(shot: dict, i: int) -> str:
    return repr(shot.get("id") or f"#{i + 1}")


def describe_changes(old: dict, new: dict) -> list[str]:
    """Plain-words differences between two reel documents (shots first, then everything else)."""
    out: list[str] = []
    o = [s for s in old.get("shots", []) if isinstance(s, dict)]
    n = [s for s in new.get("shots", []) if isinstance(s, dict)]
    oid = {s.get("id"): s for s in o}
    nid = {s.get("id"): s for s in n}
    for i, s in enumerate(o):
        if s.get("id") not in nid:
            out.append(f"deleted {_label(s, i)}")
    for i, s in enumerate(n):
        if s.get("id") not in oid:
            src = s.get("source", {})
            out.append(
                f"added {_label(s, i)} at position {i + 1}"
                + (f" ({src.get('video') or src.get('image') or src.get('color')})" if src else "")
            )
    kept_old = [s.get("id") for s in o if s.get("id") in nid]
    kept_new = [s.get("id") for s in n if s.get("id") in oid]
    if kept_old != kept_new:
        m = _moved_one(kept_old, kept_new)
        if m is not None:
            out.append(f"moved {m!r} from position {kept_old.index(m) + 1} to {kept_new.index(m) + 1}")
        else:
            out.append("reordered: " + ", ".join(map(str, kept_new)))
    for sid in kept_new:
        a, b = oid[sid], nid[sid]
        for key in sorted(set(a) | set(b)):
            if key == "id" or a.get(key) == b.get(key):
                continue
            if key == "source" and isinstance(a.get(key), dict) and isinstance(b.get(key), dict):
                for k2 in sorted(set(a[key]) | set(b[key])):
                    if a[key].get(k2) != b[key].get(k2):
                        out.append(f"{sid!r}: source.{k2} {_v(a[key].get(k2))} -> {_v(b[key].get(k2))}")
            else:
                out.append(f"{sid!r}: {key} {_v(a.get(key))} -> {_v(b.get(key))}")
    for key in sorted(set(old) | set(new)):
        if key != "shots" and old.get(key) != new.get(key):
            out.append(f"{key} changed")
    return out


def _moved_one(a: list, b: list):
    """The id whose single move turns order ``a`` into ``b`` (the one that moved furthest, else forward), if any."""
    cands = [x for x in a if [y for y in a if y != x] == [y for y in b if y != x]]
    if not cands:
        return None
    return max(cands, key=lambda x: (abs(a.index(x) - b.index(x)), a.index(x) - b.index(x)))


def _v(x) -> str:
    if x is None:
        return "(none)"
    s = json.dumps(x, ensure_ascii=False)
    return s if len(s) <= 60 else s[:57] + "..."


# --------------------------------------------------------------------------- the editor
class GLPump:
    """Runs GL work on the thread that calls ``pump`` (the thread that owns the GL context, as in the CLI)."""

    def __init__(self):
        import queue

        self.q = queue.Queue()

    def __call__(self, fn, *a):
        from concurrent.futures import Future

        f = Future()
        self.q.put((f, fn, a))
        return f.result()

    def pump(self, timeout: float = 0.2) -> None:
        import queue

        try:
            f, fn, a = self.q.get(timeout=timeout)
        except queue.Empty:
            return
        try:
            f.set_result(fn(*a))
        except BaseException as e:  # handed back to the waiting request
            f.set_exception(e)


class Editor:
    """One reel file served at one local URL."""

    def __init__(self, path: str | Path, run_gl: Callable | None = None, port: int = 0):
        from funkbox.config import resolve_path

        self.path = resolve_path(str(path))
        if not self.path.name.endswith(".reel.json"):
            raise ValueError("the timeline edits a *.reel.json file")
        self.log_path = self.path.with_name(self.path.name[: -len(".reel.json")] + ".edits.jsonl")
        self.pump = None if run_gl else GLPump()  # without a GL runner, whoever runs the editor pumps it
        self.run_gl = run_gl or self.pump
        self.lock = threading.RLock()
        self.snapshot, self.mtime = self._read()
        self.thumbs: dict[str, bytes] = {}
        self.render_state: dict[str, Any] = {"status": "idle"}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(self))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="timeline-http")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def start(self) -> "Editor":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # ---- file
    def _read(self) -> tuple[dict, int]:
        st = self.path.stat()
        return json.loads(self.path.read_text(encoding="utf-8-sig")), st.st_mtime_ns

    def log(self, by: str, changes: list[str]) -> None:
        if not changes:
            return
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": by, "changes": changes}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def edits(self, last: int = 20) -> list[dict]:
        if not self.log_path.is_file():
            return []
        rows = [json.loads(l) for l in self.log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return rows[-last:]

    def refresh(self) -> bool:
        """Pick up changes made outside the page (Claude, an editor); log them. True when the file changed."""
        with self.lock:
            st = self.path.stat().st_mtime_ns
            if st == self.mtime:
                return False
            new, mt = self._read()
            self.log("file", describe_changes(self.snapshot, new))
            self.snapshot, self.mtime = new, mt
            self.thumbs.clear()
            return True

    # ---- state
    def state(self) -> dict:
        from .validate import validate

        self.refresh()
        with self.lock:
            raw, mt = copy.deepcopy(self.snapshot), self.mtime
        rep = self.run_gl(lambda: validate(raw, str(self.path.parent)))
        spans = [
            {"id": s.id, "kind": s.kind, "start": round(s.start, 3), "duration": round(s.duration, 3), "vo": s.vo_id}
            for s in rep.spans
        ]
        copy_ids = sorted(rep.deck.lines) if rep.deck is not None else []
        beat = None
        if rep.reel is not None and rep.reel.audio.bpm:
            beat = 60.0 / rep.reel.audio.bpm
        elif rep.reel is not None:
            beat = _music_beat(rep.reel)
        return {
            "path": str(self.path),
            "name": self.path.name,
            "mtime": str(mt),
            "reel": raw,
            "spans": spans,
            "errors": rep.errors,
            "warnings": rep.warnings,
            "copy_ids": copy_ids,
            "beat": beat,
            "total": round(rep.spans[-1].end, 3) if rep.spans else 0.0,
            "edits": self.edits(12),
            "render": self.render_state,
        }

    def save(self, base_mtime: str, new: dict, by: str = "timeline") -> tuple[int, dict]:
        """(http status, body). 409: the file changed since base_mtime; 422: the new reel is invalid."""
        from .validate import validate

        with self.lock:
            self.refresh()
            if str(self.mtime) != str(base_mtime):
                return 409, {"error": "the reel changed since you loaded it", "state": self.state()}
            rep = self.run_gl(lambda: validate(new, str(self.path.parent)))
            if not rep.ok:
                return 422, {"error": "not saved: the reel would be invalid", "errors": rep.errors}
            changes = describe_changes(self.snapshot, new)
            if not changes:
                return 200, self.state()
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            self.log(by, changes)
            self.snapshot, self.mtime = copy.deepcopy(new), self.path.stat().st_mtime_ns
            self.thumbs.clear()
        return 200, self.state()

    # ---- pictures
    def thumb(self, shot_id: str) -> bytes:
        """A frame from the middle of a shot, as the reel draws it (framing, captions, product)."""
        with self.lock:
            key = f"{self.mtime}:{shot_id}"
            if key in self.thumbs:
                return self.thumbs[key]
            raw = copy.deepcopy(self.snapshot)

        def go() -> bytes:
            from PIL import Image
            from . import render

            plan = render.prepare(raw, str(self.path.parent))
            st = plan.reel.settings
            r = render.Renderer(
                plan.reel,
                plan.deck,
                plan.spans,
                width=THUMB_W,
                height=round(THUMB_W * st.height / st.width),
                depth_mode="off",
            )
            try:
                span = r.span_for_shot(shot_id)
                fr = r.still(shot_id, span.duration / 2)
            finally:
                r.release()
            b = io.BytesIO()
            Image.fromarray(fr).save(b, format="JPEG", quality=82)
            return b.getvalue()

        data = self.run_gl(go)
        with self.lock:
            self.thumbs[key] = data
        return data

    def source_frame(self, src: str, t: float) -> bytes:
        from funkbox.config import resolve_path
        from funkbox.video import clip

        p = resolve_path(src, str(self.path.parent))
        b = io.BytesIO()
        clip.frame_at(p, t, 320).save(b, format="JPEG", quality=80)
        return b.getvalue()

    def probe(self, src: str) -> dict:
        from funkbox.config import resolve_path
        from funkbox.video import clip

        i = clip.info(resolve_path(src, str(self.path.parent)))
        return {"duration": i.duration, "width": i.width, "height": i.height, "fps": i.fps}

    # ---- preview render
    def start_render(self) -> dict:
        with self.lock:
            if self.render_state.get("status") == "rendering":
                return self.render_state
            raw = copy.deepcopy(self.snapshot)
            self.render_state = {"status": "rendering", "started": time.time()}

        def job():
            from funkbox.config import funk_out
            from . import render

            name = self.path.name[: -len(".reel.json")] + "-timeline"
            try:
                out = self.run_gl(
                    lambda: render.render_reel(
                        raw, name, base_dir=str(self.path.parent), out_dir=funk_out() / "timeline", draft=True
                    )
                )
                self.render_state = {
                    "status": "done",
                    "file": Path(out.mp4).name,
                    "seconds": round(time.time() - self.render_state["started"], 1),
                }
            except Exception as e:  # shown on the page, not raised
                self.render_state = {"status": "error", "error": str(e)[:500]}

        threading.Thread(target=job, daemon=True).start()
        return self.render_state


def _music_beat(reel) -> float | None:
    try:
        from .audio import beat_grid_for

        g = beat_grid_for(reel)
        return round(g[1] - g[0], 4) if g and len(g) > 1 else None
    except Exception:
        return None


def _handler(ed: Editor):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/":
                    from .editor_page import PAGE

                    self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif u.path == "/api/state":
                    self._json(200, ed.state())
                elif u.path == "/api/changed":
                    self._json(200, {"mtime": str(ed.path.stat().st_mtime_ns)})
                elif u.path == "/api/thumb":
                    self._send(200, ed.thumb(q["id"]), "image/jpeg")
                elif u.path == "/api/frame":
                    self._send(200, ed.source_frame(q["src"], float(q.get("t", 0))), "image/jpeg")
                elif u.path == "/api/probe":
                    self._json(200, ed.probe(q["src"]))
                elif u.path == "/api/render":
                    self._json(200, ed.render_state)
                elif u.path.startswith("/media/"):
                    from funkbox.config import funk_out

                    name = Path(u.path[len("/media/") :]).name
                    p = funk_out() / "timeline" / name
                    if not (name.endswith(".mp4") and p.is_file()):
                        return self._json(404, {"error": "no such preview"})
                    self._send(200, p.read_bytes(), "video/mp4")
                else:
                    self._json(404, {"error": "not found"})
            except Exception as e:
                self._json(500, {"error": str(e)[:500]})

        def do_POST(self):
            u = urlparse(self.path)
            if self.headers.get("Origin") not in (None, ed.url.rstrip("/")):
                return self._json(403, {"error": "cross-origin request refused"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/save":
                    code, out = ed.save(body.get("base_mtime", ""), body["reel"])
                    self._json(code, out)
                elif u.path == "/api/render":
                    self._json(200, ed.start_render())
                else:
                    self._json(404, {"error": "not found"})
            except Exception as e:
                self._json(500, {"error": str(e)[:500]})

    return H


def open_editor(path: str | Path, run_gl: Callable | None = None, port: int = 0) -> Editor:
    """The running editor for this reel, started if needed (one per file per process)."""
    from funkbox.config import resolve_path

    key = str(resolve_path(str(path)))
    ed = _SERVERS.get(key)
    if ed is None or not ed.thread.is_alive():
        ed = _SERVERS[key] = Editor(key, run_gl, port).start()
    return ed


def changes_text(path: str | Path, last: int = 15) -> str:
    """The edit log in plain words, newest last, plus the current shot order."""
    from funkbox.config import resolve_path

    p = resolve_path(str(path))
    logp = p.with_name(p.name[: -len(".reel.json")] + ".edits.jsonl")
    rows = []
    if logp.is_file():
        rows = [json.loads(l) for l in logp.read_text(encoding="utf-8").splitlines() if l.strip()][-last:]
    raw = json.loads(p.read_text(encoding="utf-8-sig"))
    lines = [
        f"{p.name}: {len(raw.get('shots', []))} shots: " + ", ".join(str(s.get("id")) for s in raw.get("shots", []))
    ]
    if not rows:
        lines.append("no edits logged yet")
    for r in rows:
        who = {"timeline": "you (timeline)", "file": "file changed outside the timeline"}.get(r["by"], r["by"])
        lines.append(f"{r['time']} {who}:")
        lines += [f"  - {c}" for c in r["changes"]]
    return "\n".join(lines)


def digest(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]

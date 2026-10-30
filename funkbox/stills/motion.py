"""Animated posters: the same layered poster, moving. A ``motion`` block turns a poster into a short video (MP4, and a
GIF if asked) while the still poster stays exactly what the document says: animations run *towards* the document's
values, so the last frame is the poster.

    "motion": {
      "seconds": 6, "fps": 30,
      "enter": [{"layer": "title", "effect": "rise", "at": 0.3, "dur": 0.8},
                {"layer": "bolt", "effect": "pop", "at": 0.9}],
      "tracks": {"layers.title.letter_spacing": [[0, 0.4], [1.5, 0.05, "out"]],
                 "layers.halo.opacity": [[0, 0.2], [3, 0.8, "inout"], [6, 0.2, "inout"]]},
      "loop_shaders": true
    }

``enter`` effects: fade, rise, drop, slide-left, slide-right, pop (grow from 60 % with a fade), each ``at`` seconds for
``dur`` seconds (default 0.6) with an ease-out. ``tracks`` keyframe any numeric property the variations keys reach
(``layers.<id>.<prop>``, ``post.<n>.<k>``) plus three motion-only ones per layer: ``dx`` and ``dy`` (move, canvas
fractions) and ``scale`` (grow about the layer's centre). A keyframe is [seconds, value] or [seconds, value, ease]
(linear, in, out, inout); lists of numbers and #rrggbb colours interpolate too. Shader layers loop once over the
video with ``loop_shaders`` (default true), so the background moves seamlessly.

Text is never animated letter by letter: every frame draws the whole approved text, and every frame is audited.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import render, schema
from .variations import VaryError, _find_layer, set_path

EFFECTS = ("fade", "rise", "drop", "slide-left", "slide-right", "pop")
EASES = ("linear", "in", "out", "inout")
PSEUDO = ("dx", "dy", "scale")
MOVABLE = ("text", "shape", "svg", "product", "image", "group")
MAX_SECONDS, MAX_FPS = 30, 60


def ease(name: str, u: float) -> float:
    u = min(1.0, max(0.0, u))
    if name == "in":
        return u ** 3
    if name == "out":
        return 1 - (1 - u) ** 3
    if name == "inout":
        return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2
    return u


def _lerp(a: Any, b: Any, u: float) -> Any:
    if isinstance(a, bool) or isinstance(b, bool):
        return a if u < 1 else b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * u
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [_lerp(x, y, u) for x, y in zip(a, b)]
    if isinstance(a, str) and isinstance(b, str) and schema.HEX_RE.match(a) and schema.HEX_RE.match(b):
        ca = [int(a[i:i + 2], 16) for i in range(1, len(a), 2)]
        cb = [int(b[i:i + 2], 16) for i in range(1, len(b), 2)]
        if len(ca) != len(cb):
            ca, cb = ca[:3], cb[:3]
        return "#" + "".join(f"{round(x + (y - x) * u):02x}" for x, y in zip(ca, cb))
    return a if u < 1 else b


def value_at(keys: list, t: float) -> Any:
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0, *_), (t1, v1, *e) in zip(keys, keys[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
            return _lerp(v0, v1, ease(e[0] if e else "linear", u))
    return keys[-1][1]


# --------------------------------------------------------------------------- validation
def check(doc: dict) -> list[str]:
    m = doc.get("motion")
    errs: list[str] = []
    if not isinstance(m, dict):
        return ["motion: an object with seconds, fps, enter and/or tracks"]
    bad = set(m) - {"seconds", "fps", "enter", "tracks", "loop_shaders"}
    if bad:
        errs.append(f"motion: unknown keys {sorted(bad)}")
    secs = m.get("seconds", 6)
    if not (isinstance(secs, (int, float)) and not isinstance(secs, bool) and 0.5 <= secs <= MAX_SECONDS):
        errs.append(f"motion.seconds: 0.5..{MAX_SECONDS}")
        secs = 6
    if not (isinstance(m.get("fps", 30), int) and 1 <= m.get("fps", 30) <= MAX_FPS):
        errs.append(f"motion.fps: a whole number 1..{MAX_FPS}")
    layers = {l.get("id"): l for l in schema.all_layers(doc.get("layers") or []) if isinstance(l, dict)}
    for i, e in enumerate(m.get("enter") or []):
        w = f"motion.enter[{i}]"
        if not isinstance(e, dict) or set(e) - {"layer", "effect", "at", "dur"}:
            errs.append(f"{w}: {{layer, effect, at, dur}}")
            continue
        lay = layers.get(e.get("layer"))
        if lay is None:
            errs.append(f"{w}: layer {e.get('layer')!r} is not a layer id")
        if e.get("effect") not in EFFECTS:
            errs.append(f"{w}: effect is one of {', '.join(EFFECTS)}")
        elif lay is not None and e["effect"] != "fade" and lay.get("type") not in MOVABLE:
            errs.append(f"{w}: a {lay.get('type')} layer can only fade")
        for k, lo in (("at", 0.0), ("dur", 0.05)):
            if k in e and not (isinstance(e[k], (int, float)) and lo <= e[k] <= secs):
                errs.append(f"{w}: {k} must be {lo}..{secs} seconds")
    tracks = m.get("tracks") or {}
    if not isinstance(tracks, dict):
        errs.append("motion.tracks: an object {key: [[seconds, value, ease?], ...]}")
        tracks = {}
    for key, keys in tracks.items():
        w = f"motion.tracks[{key!r}]"
        if not (isinstance(keys, list) and keys and all(isinstance(k, list) and 2 <= len(k) <= 3 for k in keys)):
            errs.append(f"{w}: a list of [seconds, value] or [seconds, value, ease]")
            continue
        ts = [k[0] for k in keys]
        if not all(isinstance(t, (int, float)) and 0 <= t <= secs for t in ts) or ts != sorted(ts):
            errs.append(f"{w}: keyframe times must rise within 0..{secs}")
        if any(len(k) == 3 and k[2] not in EASES for k in keys):
            errs.append(f"{w}: ease is one of {', '.join(EASES)}")
        parts = key.split(".")
        if parts[0] == "layers" and len(parts) == 3 and parts[2] in PSEUDO:
            lay = layers.get(parts[1])
            if lay is None:
                errs.append(f"{w}: no layer with id {parts[1]!r}")
            elif lay.get("type") not in MOVABLE:
                errs.append(f"{w}: dx, dy and scale move text, shape, svg, product, image and group layers")
            continue
        try:
            set_path(copy.deepcopy(doc), key, keys[0][1])
        except VaryError as e:
            errs.append(f"{w}: {e}")
    return errs


# --------------------------------------------------------------------------- frames
def _expand(doc: dict) -> dict[str, list]:
    """``enter`` effects as tracks, merged with the explicit tracks."""
    m = doc["motion"]
    tracks: dict[str, list] = {k: list(v) for k, v in (m.get("tracks") or {}).items()}
    for e in m.get("enter") or []:
        lid, eff = e["layer"], e["effect"]
        at, dur = float(e.get("at", 0.0)), float(e.get("dur", 0.6))
        lay = _find_layer(doc["layers"], lid)
        op = float(lay.get("opacity", 1.0))
        tracks.setdefault(f"layers.{lid}.opacity", []).extend([[0, 0.0], [at, 0.0], [at + dur, op, "out"]])
        move = {"rise": ("dy", 0.06), "drop": ("dy", -0.06), "slide-left": ("dx", 0.12), "slide-right": ("dx", -0.12),
                "pop": ("scale", 0.6)}.get(eff)
        if move:
            k, v = move
            end = 1.0 if k == "scale" else 0.0
            tracks.setdefault(f"layers.{lid}.{k}", []).extend([[0, v], [at, v], [at + dur, end, "out"]])
    for k in tracks:
        tracks[k].sort(key=lambda kf: kf[0])
    return tracks


def _centre(lay: dict) -> tuple[float, float]:
    t = lay["type"]
    if t == "shape" and lay["kind"] != "path":
        pts = np.array(lay["points"], float)
        return float(pts[:, 0].mean()), float(pts[:, 1].mean())
    if t == "shape":
        from .vpath import parse
        pts = np.concatenate([np.array(p) for p, _ in parse(lay["d"], 0.01)])
        return float(pts[:, 0].mean()), float(pts[:, 1].mean())
    if t == "text":
        mw = float(lay.get("max_width", 0.84))
        return float(lay.get("x", (1 - mw) / 2)) + mw / 2, float(lay.get("y", 0.1))
    if t == "svg":
        return float(lay.get("x", 0)) + float(lay.get("width", 0.5)) / 2, float(lay.get("y", 0))
    return float(lay.get("x", 0.5)), float(lay.get("y", 0.5))


def move_layer(lay: dict, dx: float, dy: float, s: float, W: int, H: int) -> None:
    """Shift a layer by (dx, dy) canvas fractions and scale it by ``s`` about its centre, in place."""
    from .text import TEXT_SIZE
    from .vpath import transform_d
    t = lay["type"]
    if t == "group":
        for sub in lay["layers"]:
            if sub["type"] in MOVABLE:
                move_layer(sub, dx, dy, s, W, H)
        return
    cx, cy = _centre(lay)
    if t == "shape":
        if lay["kind"] == "path":
            px = lay.get("units", "fraction") == "px"
            kx, ky = (W, H) if px else (1, 1)
            lay["d"] = transform_d(lay["d"], s, s, (cx - s * cx) + dx * kx, (cy - s * cy) + dy * ky)
        else:
            lay["points"] = [[cx + (x - cx) * s + dx, cy + (y - cy) * s + dy] for x, y in lay["points"]]
        if "width" in lay:
            lay["width"] = float(lay["width"]) * s
        if "stroke_px" in lay:
            lay["stroke_px"] = float(lay["stroke_px"]) * s
    elif t == "text":
        mw = float(lay.get("max_width", 0.84))
        x = float(lay.get("x", (1 - mw) / 2))
        lay["x"] = cx + (x - cx) * s + dx
        lay["max_width"] = mw * s
        lay["y"] = float(lay.get("y", 0.1)) + dy
        lay["size"] = float(lay.get("size", TEXT_SIZE)) * s
        if lay.get("on_path"):
            px = lay.get("path_units", "fraction") == "px"
            kx, ky = (W, H) if px else (1, 1)
            lay["on_path"] = transform_d(lay["on_path"], 1, 1, dx * kx, dy * ky)
    elif t == "svg":
        w = float(lay.get("width", 0.5))
        lay["x"] = cx + (float(lay.get("x", 0)) - cx) * s + dx
        lay["y"] = float(lay.get("y", 0)) + dy
        lay["width"] = w * s
        if "height" in lay:
            lay["height"] = float(lay["height"]) * s
    elif t == "product":
        lay["x"], lay["y"] = float(lay.get("x", 0.5)) + dx, float(lay.get("y", 0.9)) + dy
        lay["height"] = float(lay.get("height", 0.5)) * s
    elif t == "image":
        lay["x"], lay["y"] = float(lay.get("x", 0.5)) + dx, float(lay.get("y", 0.5)) + dy
        lay["scale"] = float(lay.get("scale", 1.0)) * s


def frame_doc(doc: dict, tracks: dict[str, list], t: float, W: int, H: int) -> dict:
    d = copy.deepcopy(doc)
    d.pop("motion", None)
    m = doc["motion"]
    pseudo: dict[str, dict[str, float]] = {}
    for key, keys in tracks.items():
        v = value_at(keys, t)
        parts = key.split(".")
        if parts[0] == "layers" and len(parts) == 3 and parts[2] in PSEUDO:
            pseudo.setdefault(parts[1], {})[parts[2]] = float(v)
        else:
            if key.endswith(".opacity"):
                v = min(1.0, max(0.0, float(v)))
            set_path(d, key, v)
    for lid, p in pseudo.items():
        lay = _find_layer(d["layers"], lid)
        if p.get("dx", 0) or p.get("dy", 0) or p.get("scale", 1.0) != 1.0:
            move_layer(lay, p.get("dx", 0.0), p.get("dy", 0.0), max(0.01, p.get("scale", 1.0)), W, H)
    if m.get("loop_shaders", True):
        secs = float(m.get("seconds", 6))
        for lay in schema.all_layers(d["layers"]):
            if lay["type"] == "shader" and f"layers.{lay['id']}.t" not in tracks:
                lay["t"] = (float(lay.get("t", 0.0)) + t / secs) % 1.0
    return d


@dataclass
class Animated:
    paths: dict[str, Path] = field(default_factory=dict)
    frames: int = 0
    seconds: float = 0.0
    fps: int = 30
    texts: list[str] = field(default_factory=list)
    all_exact: bool = True
    sheet_image: Any = None
    warnings: list[str] = field(default_factory=list)


def animate(doc_in, out_dir, name: str = "", base_dir=None, scale: float = 1.0, gif: bool = False,
            seconds: float | None = None, fps: int | None = None, write_files: bool = True) -> Animated:
    """Render every frame and encode ``<name>.mp4`` (+ ``.gif``), the last frame as ``<name>.png`` (the poster),
    a strip of six frames ``<name>-frames.png`` and ``<name>.motion_audit.json`` (the text of every frame)."""
    from funkbox import media
    from PIL import Image
    from .sheet import contact_sheet
    raw = doc_in if isinstance(doc_in, dict) else schema.parse(doc_in).raw
    doc = copy.deepcopy(raw)
    doc.setdefault("motion", {})
    if seconds is not None:
        doc["motion"]["seconds"] = seconds
    if fps is not None:
        doc["motion"]["fps"] = fps
    errs = schema.check(doc)
    if errs:
        raise schema.PosterError(errs)
    base = schema.parse(doc)
    W, H = base.width, base.height
    name = name or base.name
    secs = float(doc["motion"].get("seconds", 6))
    rate = int(doc["motion"].get("fps", 30))
    n = max(1, int(round(secs * rate)))
    tracks = _expand(base.raw)
    cache: dict = {}
    res = Animated(frames=n, seconds=secs, fps=rate)
    texts: dict[str, bool] = {}
    picks = {round(i * (n - 1) / 5) for i in range(6)}
    shots: list = []
    w, h = render.scaled_size(base, scale)
    w -= w % 2
    h -= h % 2

    def frames():
        for i in range(n):
            t = i / rate
            r = render.render_document(frame_doc(base.raw, tracks, t, W, H), base_dir, scale=scale, cache=cache)
            for a in r.audit:
                texts[a["text"]] = texts.get(a["text"], True) and a["text_exact"]
            for wmsg in r.warnings:
                if wmsg not in res.warnings:
                    res.warnings.append(wmsg)
            img = r.image[:h, :w]
            if i in picks:
                shots.append((Image.fromarray(img), f"{t:.1f}s"))
            yield img
    if write_files:
        d = render.fence_out(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        out = media.encode(frames(), w, h, rate, d / name, mp4=True, gif=gif, gif_width=min(480, w), poster=False)
        res.paths = {k: Path(v) for k, v in out.items()}
    else:
        for _ in frames():
            pass
    res.texts = list(texts)
    res.all_exact = all(texts.values())
    res.sheet_image = contact_sheet([s[0] for s in shots], [s[1] for s in shots], tile=300, cols=6)
    if write_files:
        last = render.render_document(base, base_dir, scale=scale)
        res.paths["png"] = d / f"{name}.png"
        last.pil.save(res.paths["png"])
        res.paths["frames"] = d / f"{name}-frames.png"
        res.sheet_image.save(res.paths["frames"])
        res.paths["audit"] = d / f"{name}.motion_audit.json"
        res.paths["audit"].write_text(json.dumps({"frames": n, "seconds": secs, "fps": rate,
                                                  "texts": [{"text": k, "exact_every_frame": v}
                                                            for k, v in texts.items()]},
                                                 indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return res

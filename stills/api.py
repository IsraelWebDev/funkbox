"""Shared front door for the MCP server and the CLI: load a poster, preview it, make a still, check an .ora."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

from funkgl.recipe import funk_out, resolve_path

from . import ora, render, schema

PREVIEW_SIDE = 720


def posters_dir() -> Path:
    return funk_out() / "posters"


def stills_dir() -> Path:
    return funk_out() / "stills"


def load_poster(poster: Any, project: str = "") -> tuple[dict, Path | None]:
    """A poster is a JSON object, a JSON string, or a path to a ``*.poster.json`` inside the allowed folders.
    Returns (document, base_dir for relative paths)."""
    base = None
    if isinstance(poster, str):
        s = poster.strip()
        if s.startswith("{"):
            poster = json.loads(s)
        else:
            p = resolve_path(s)
            poster, base = json.loads(p.read_text(encoding="utf-8-sig")), p.parent
    if not isinstance(poster, dict):
        raise ValueError("poster must be a JSON object or a path to a .poster.json file")
    doc = dict(poster)
    if project:
        if doc.get("project") not in (None, project):
            raise ValueError(f"the poster says project {doc['project']!r} but project={project!r} was given")
        doc["project"] = project
    return doc, base


def describe(r: render.Rendered) -> str:
    lines = [f"{r.width}x{r.height}" + (" (transparent)" if r.alpha is not None else "")
             + f", {len(r.layers)} layers"]
    if r.audit:
        lines.append(f"text audit: {len(r.audit)} string(s), " + ("all byte-equal to their source"
                     if all(a.get("text_exact", True) for a in r.audit) else "MISMATCH, see the audit file"))
    for k, v in r.info.items():
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines += [f"warning: {w}" for w in r.warnings]
    return "\n".join(lines)


def preview(poster: Any, project: str = "", max_side: int = PREVIEW_SIDE) -> tuple[render.Rendered, Image.Image]:
    doc, base = load_poster(poster, project)
    d = schema.parse(doc)
    w, h = d.width, d.height
    scale = min(1.0, max(64, int(max_side)) / max(w, h))
    r = render.render_document(doc, base, scale=scale)
    return r, r.pil


def make_still_doc(source: Any, t: float = 0.25, preset: str = "square", seed: int = 1) -> dict:
    """A one-layer poster from a funk2 recipe (dict / .json path) or a shader (.frag path or GLSL source)."""
    if preset not in schema.PRESETS:
        raise ValueError(f"preset {preset!r}: use one of {', '.join(schema.PRESETS)}")
    lay: dict[str, Any] = {"id": "art", "type": "shader", "t": float(t)}
    if isinstance(source, dict):
        lay["recipe"] = source
    elif isinstance(source, str):
        s = source.strip()
        if s.startswith("{"):
            lay["recipe"] = json.loads(s)
        elif "effect(" in s or "mainImage" in s:
            lay["source"] = source
        elif s.lower().endswith(".json"):
            lay["recipe"] = s
        else:
            lay["shader"] = s
    else:
        raise ValueError("give a recipe (JSON object or .json path) or a shader (.frag path or GLSL source)")
    return {"version": 1, "canvas": {"preset": preset}, "seed": int(seed), "layers": [lay]}


def funk_still(source: Any, t: float = 0.25, preset: str = "square", name: str = "",
               out_dir: str | Path | None = None) -> tuple[dict[str, Path], render.Rendered]:
    doc = make_still_doc(source, t, preset)
    if not name:
        name = "still-" + hashlib.sha256(json.dumps([doc, t], sort_keys=True, default=str).encode()).hexdigest()[:8]
    r = render.render_document(doc)
    paths = render.write_outputs(r, out_dir or stills_dir(), name, ["png"])
    return paths, r


def ora_report(path: str | Path) -> str:
    info = ora.validate_ora(path)
    rows = [f"valid OpenRaster {info['w']}x{info['h']}, {len(info['layers'])} layers (top first):"]
    rows += [f"  {l['name']}  {l['op']}  opacity {l['opacity']:g}" for l in info["layers"]]
    return "\n".join(rows)


NAME_OK = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")

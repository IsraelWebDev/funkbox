"""Poster variations: re-render a poster with different seeds or layer properties and lay the results out as a sheet."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from . import render, schema
from .sheet import contact_sheet

MAX_VARIANTS = 12
_IDX = re.compile(r"^\d+$")


class VaryError(ValueError):
    pass


@dataclass
class Variations:
    sheet: Path | None
    sheet_image: Image.Image
    files: list[dict[str, Path]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    docs: list[dict] = field(default_factory=list)
    audits: list[list[dict]] = field(default_factory=list)


def _find_layer(layers: list[dict], lid: str) -> dict | None:
    for l in layers:
        if l.get("id") == lid:
            return l
        if l.get("type") == "group":
            hit = _find_layer(l.get("layers", []), lid)
            if hit:
                return hit
    return None


def set_path(doc: dict, key: str, value: Any) -> None:
    """Set ``seed``, ``canvas.<k>``, ``post.<i>.<k>`` or ``layers.<id>.<prop>[.<sub>...]`` in a poster dict."""
    parts = key.split(".")
    if parts == ["seed"]:
        doc["seed"] = value
        return
    if parts[0] == "layers" and len(parts) >= 3:
        node = _find_layer(doc.get("layers", []), parts[1])
        if node is None:
            raise VaryError(f"vary key {key!r}: no layer with id {parts[1]!r}")
        path = parts[2:]
    elif parts[0] == "canvas" and len(parts) == 2:
        node, path = doc.setdefault("canvas", {}), parts[1:]
    elif parts[0] == "post" and len(parts) == 3 and _IDX.match(parts[1]):
        try:
            node = doc["post"][int(parts[1])]
        except (KeyError, IndexError):
            raise VaryError(f"vary key {key!r}: the poster has no post pass #{parts[1]}") from None
        path = parts[2:]
    else:
        raise VaryError(f"vary key {key!r}: use 'seed', 'layers.<id>.<property>', 'canvas.<key>' or 'post.<n>.<key>'")
    for p in path[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise VaryError(f"vary key {key!r}: {p!r} is not an object")
    node[path[-1]] = value


def expand(doc: dict, vary: dict) -> tuple[list[dict], list[str]]:
    """The poster dicts and labels for ``vary`` = {key: [values...]}; several keys are zipped (equal lengths)."""
    if isinstance(vary, str):
        vary = json.loads(vary)
    if not isinstance(vary, dict) or not vary:
        raise VaryError("vary must be an object like {\"seed\": [1, 2, 3]} or {\"layers.bg.palette\": [\"vapor\", \"acid\"]}")
    lens = set()
    for k, v in vary.items():
        if not isinstance(v, list) or not v:
            raise VaryError(f"vary[{k!r}] must be a non-empty list of values")
        lens.add(len(v))
    if len(lens) != 1:
        raise VaryError(f"vary lists must all have the same length (got {sorted(lens)}); each position is one variant")
    n = lens.pop()
    if n > MAX_VARIANTS:
        raise VaryError(f"at most {MAX_VARIANTS} variants (got {n})")
    docs, labels = [], []
    for i in range(n):
        d = copy.deepcopy(doc)
        bits = []
        for k, v in vary.items():
            set_path(d, k, v[i])
            bits.append(f"{k.split('.')[-1]}={v[i]}" if len(vary) > 1 or k != "seed" else f"seed {v[i]}")
        docs.append(d)
        labels.append(f"{i + 1}: " + ", ".join(bits))
    return docs, labels


def poster_variations(doc: dict, vary: dict, out_dir: str | Path | None = None, name: str = "var",
                      base_dir: str | Path | None = None, scale: float = 1.0, write_files: bool = True) -> Variations:
    """Render every variant. With ``write_files`` each gets ``<name>-<i>.png`` (+ sidecar and audit) in ``out_dir`` and
    the sheet is saved as ``<name>-sheet.png``; ``scale`` < 1 renders smaller (previews)."""
    docs, labels = expand(doc, vary)
    for i, d in enumerate(docs):                                     # report every broken variant before rendering any
        errs = schema.check(d)
        if errs:
            raise schema.PosterError([f"variant {i + 1} ({labels[i]}): {e}" for e in errs])
    res = Variations(None, Image.new("RGB", (1, 1)), labels=labels, docs=docs)
    ims = []
    for i, d in enumerate(docs):
        r = render.render_document(d, base_dir, scale=scale)
        ims.append(r.pil)
        res.audits.append(r.audit)
        if write_files:
            res.files.append(render.write_outputs(r, out_dir, f"{name}-{i + 1}", ["png"]))
    res.sheet_image = contact_sheet([_flat(im) for im in ims], labels, tile=360)
    if write_files:
        p = render.fence_out(out_dir) / f"{name}-sheet.png"
        res.sheet_image.save(p)
        res.sheet = p
    return res


def _flat(im: Image.Image) -> Image.Image:
    """RGBA -> RGB over a light checkerboard so transparency is visible on a sheet."""
    if im.mode == "RGB":
        return im
    from .edit import checker_flatten
    return checker_flatten(im)

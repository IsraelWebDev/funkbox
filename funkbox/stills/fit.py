"""Fit a poster to a reference picture: nudge the numbers you name until poster_compare's score stops rising.

    res = fit(doc, "reference.png", {"layers.band.points.2.1": 0.02, "layers.title.size": [0.06, 0.09]})
    res.doc        the best poster found (also written as <name>.fit.poster.json)
    res.score      its compare scores; res.history: (evaluation, overall) as it improved

Each parameter is a variations key (``layers.<id>.<prop>[.<index>...]``, ``canvas.<k>``, ``post.<n>.<k>``) with either
a step (the search starts by trying +-step) or a [low, high] range (the step starts at a quarter of it and values stay
inside). The search is coordinate descent with a shrinking step, on small previews, so it is fast and deterministic:
it improves what you point it at; it does not invent a design. Every candidate is validated like any poster, and
text stays exact (a text layer's words are never a parameter).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from . import compare as cmp
from . import render, schema
from .variations import VaryError, get_path, set_path

MAX_PARAMS = 64


@dataclass
class Fit:
    doc: dict
    score: dict
    start: dict
    history: list[tuple[int, float]] = field(default_factory=list)
    changed: dict = field(default_factory=dict)
    evaluations: int = 0


def _bad_key(key: str) -> bool:
    last = key.split(".")[-1]
    return last in ("text", "copy_id", "match", "type", "id", "path", "image", "font", "d", "on_path")


def fit(doc, reference, params: dict, rounds: int = 4, scale: float | None = None, base_dir=None,
        fit_mode: str = "cover") -> Fit:
    raw = copy.deepcopy(schema.parse(doc).raw if not isinstance(doc, schema.Doc) else doc.raw)
    if not params or len(params) > MAX_PARAMS:
        raise ValueError(f"params: 1..{MAX_PARAMS} keys, each with a step or a [low, high] range")
    space = {}
    for key, spec in params.items():
        if _bad_key(key):
            raise ValueError(f"{key}: only numbers can be fitted (never text, ids, files or path data)")
        v = get_path(raw, key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"{key}: is {v!r}; fit needs a number that is already set in the poster")
        if isinstance(spec, (list, tuple)) and len(spec) == 2:
            lo, hi = float(spec[0]), float(spec[1])
            if not lo <= v <= hi:
                raise ValueError(f"{key}: current value {v} is outside [{lo}, {hi}]")
            space[key] = (lo, hi, (hi - lo) / 4)
        elif isinstance(spec, (int, float)) and spec > 0:
            space[key] = (None, None, float(spec))
        else:
            raise ValueError(f"{key}: give a step (> 0) or a [low, high] range")
    ref = cmp._load(reference)
    s = scale or min(1.0, 420 / max(schema.parse(raw).width, schema.parse(raw).height))
    n = [0]
    cache: dict = {}                                   # layers a candidate did not change are drawn once

    def score(d: dict) -> float | None:
        if schema.check(d):
            return None
        try:
            r = render.render_document(d, base_dir, scale=s, cache=cache)
        except (schema.PosterError, ValueError):
            return None
        n[0] += 1
        return float(cmp.compare(r.pil, ref, fit_mode).scores["overall"])

    best = score(raw)
    if best is None:
        raise ValueError("the poster does not render as it is; fix it before fitting")
    res = Fit(raw, {}, {}, [(0, round(best, 2))])
    steps = {k: v[2] for k, v in space.items()}
    for _ in range(rounds):
        for key, (lo, hi, _) in space.items():
            cur = get_path(raw, key)
            for direction in (1, -1):
                cand_v = cur + direction * steps[key]
                if lo is not None:
                    cand_v = min(hi, max(lo, cand_v))
                if cand_v == cur:
                    continue
                cand = copy.deepcopy(raw)
                try:
                    set_path(cand, key, round(cand_v, 6))
                except VaryError:
                    continue
                sc = score(cand)
                if sc is not None and sc > best + 1e-3:
                    raw, best = cand, sc
                    res.history.append((n[0], round(sc, 2)))
                    break
        steps = {k: v / 2 for k, v in steps.items()}
    full = render.render_document(raw, base_dir)
    res.doc = raw
    res.score = {k: round(float(v), 1) for k, v in cmp.compare(full.pil, ref, fit_mode).scores.items()}
    start_doc = schema.parse(doc).raw if not isinstance(doc, schema.Doc) else doc.raw
    res.start = {k: round(float(v), 1) for k, v in
                 cmp.compare(render.render_document(start_doc, base_dir).pil, ref, fit_mode).scores.items()}
    res.changed = {k: (get_path(start_doc, k), get_path(raw, k)) for k in space if get_path(start_doc, k) != get_path(raw, k)}
    res.evaluations = n[0]
    return res

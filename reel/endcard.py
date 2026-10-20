"""End-card templates (JSON in templates/endcards): brand-split and centered-logo.

Every template declares its copy slots; the reel maps each slot to a copy ID. All text goes
through captions.draw_block, so the exactness audit sees every string.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from . import captions as C
from . import product as P
from .motion import Camera
from .schema import Brand, ProductSpec, Reel

NAME_RE = C.NAME_RE


def list_templates() -> list[str]:
    return sorted(p.stem for p in (C.TEMPLATES / "endcards").glob("*.json"))


def load_template(name: str) -> dict:
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad end-card template name {name!r}")
    p = C.TEMPLATES / "endcards" / f"{name}.json"
    if not p.exists():
        raise ValueError(f"unknown end-card template {name!r}; have {list_templates()}")
    return json.loads(p.read_text(encoding="utf-8"))


def check(reel: Reel) -> list[str]:
    """Slot errors: every declared slot needs a copy id; no undeclared slots."""
    ec = reel.end_card
    if ec is None:
        return []
    try:
        tpl = load_template(ec.template)
    except ValueError as e:
        return [f"end_card.template: {e}"]
    errs = []
    slots = tpl["slots"]
    for s in slots:
        if s not in ec.copy:
            errs.append(f"end_card.copy: template {ec.template!r} needs a copy id for slot {s!r}")
    for s in ec.copy:
        if s not in slots:
            errs.append(f"end_card.copy.{s}: template {ec.template!r} has no slot {s!r}; slots are {slots}")
    if ec.template == "centered-logo" and not reel.brand.logo:
        errs.append("end_card: template 'centered-logo' needs brand.logo")
    return errs


@dataclass
class Item:
    slot: str
    instance: str
    copy_id: str
    lay: C.Layout
    reveal: str
    start: float


@dataclass
class EndCardRenderer:
    reel: Reel
    deck: object
    W: int
    H: int
    fonts: C.Fonts
    resolve: Callable[[str], Path]
    items: list[Item] = field(default_factory=list)

    def __post_init__(self) -> None:
        ec = self.reel.end_card
        assert ec is not None
        self.tpl = load_template(ec.template)
        self.brand: Brand = self.reel.brand
        W, H = self.W, self.H
        t0 = float(self.tpl.get("start", 0.25))
        sty = lambda k: C.parse_style(self.tpl[k], f"{ec.template}.{k}")  # noqa: E731
        txt = lambda slot: self.deck.text(ec.copy[slot])  # noqa: E731
        self.bar_h = int(round(self.tpl["bar_height"] * H))
        self.static = self._background()
        self.product = None
        if ec.template == "brand-split":
            head = C.layout_block(ec.copy["headline"], txt("headline"), sty("headline"), self.fonts, self.brand, W, H,
                                  safe_area="none")
            hs = C.reveal_seconds(head, ec.headline_reveal)
            self.items.append(Item("headline", "main", ec.copy["headline"], head, ec.headline_reveal, t0))
            ss = sty("sub")
            sub = C.layout_block(ec.copy["sub"], txt("sub"), ss, self.fonts, self.brand, W, H, safe_area="none")
            sub = replace(sub, y0=head.y0 + head.box_h + int(round(float(self.tpl["sub"].get("gap", 0.04)) * H)))
            self.items.append(Item("sub", "main", ec.copy["sub"], sub, "pop", t0 + hs + 0.2))
            pj = self.tpl["product"]
            if ec.product:
                spec = ProductSpec(image=ec.product, x=pj["x"], y=pj["y"], height=pj["height"],
                                   depth=pj["depth"], shadow=pj["shadow"], match_color=0.0)
                self.product = P.ProductCompositor(self.resolve(ec.product), spec, W, H)
        else:   # centered-logo
            head = C.layout_block(ec.copy["headline"], txt("headline"), sty("headline"), self.fonts, self.brand, W, H,
                                  safe_area="none")
            self.items.append(Item("headline", "main", ec.copy["headline"], head, ec.headline_reveal, t0))
            if self.reel.brand.logo:
                logo = Image.open(self.resolve(self.reel.brand.logo)).convert("RGBA")
                lh = int(round(self.tpl["logo"]["height"] * H))
                lw = max(1, int(round(logo.width * lh / logo.height)))
                self.logo = (np.asarray(logo.resize((lw, lh), Image.LANCZOS)),
                             (W - lw) // 2, int(round(self.tpl["logo"]["y"] * H)))
        us = sty("url")
        url = C.layout_block(ec.copy["url"], txt("url"), us, self.fonts, self.brand, W, H, safe_area="none")
        for inst, cy in (("top", self.bar_h / 2), ("bottom", H - self.bar_h / 2)):
            self.items.append(Item("url", inst, ec.copy["url"], replace(url, y0=int(round(cy - url.box_h / 2))),
                                   "none", 0.0))

    # ---- static art
    def _background(self) -> np.ndarray:
        W, H, b = self.W, self.H, self.brand
        sec, pri = C.hex_rgb(b.secondary), C.hex_rgb(b.primary)
        ec = self.reel.end_card
        assert ec is not None
        fr = np.zeros((H, W, 3), np.uint8)
        if ec.template == "brand-split":
            fr[:] = sec
            fr[:, : int(round(self.tpl["panel_width"] * W))] = pri
        else:
            fr[:] = pri
        fr[: self.bar_h] = sec
        fr[H - self.bar_h:] = sec
        return fr

    # ---- frames
    def frame(self, t: float, note=None) -> np.ndarray:
        """End-card frame ``t`` seconds in. ``note(item, sprite)`` is called for every drawn text."""
        fr = self.static.copy()
        if self.product is not None:
            fr = self.product.draw(fr, Camera("kenburns", 1.0, (0.0, 0.0)))
        if getattr(self, "logo", None) is not None:
            arr, x, y = self.logo
            fr = C.Sprite(arr, x, y).over(fr)
        for it in self.items:
            spr = C.render_caption(it.lay, it.reveal, t - it.start)
            if spr is None:
                continue
            fr = spr.over(fr)
            if note is not None:
                note(it, spr)
        return fr

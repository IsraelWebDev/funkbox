"""Whole-reel validation: schema, copy deck IDs, files, templates, caption fit, timing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from funkgl.recipe import resolve_path

from . import collision, copydeck, project, timeline
from .copydeck import CopyDeck
from .schema import Reel, ReelError, parse_reel


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    reel: Reel | None = None
    deck: CopyDeck | None = None
    spans: list[timeline.ShotSpan] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def text(self) -> str:
        head = "OK" if self.ok else f"{len(self.errors)} error(s)"
        out = [head]
        out += [f"  error: {e}" for e in self.errors]
        out += [f"  warning: {w}" for w in self.warnings]
        if self.info.get("summary"):
            out.append("  " + self.info["summary"])
        return "\n".join(out)


def load_reel(src: dict | str | Path, base_dir: str | Path | None = None) -> Reel:
    """Parse a reel from a dict or a *.reel.json path (paths then resolve against its folder)."""
    if isinstance(src, (str, Path)):
        p = resolve_path(src, base_dir)
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return parse_reel(data, str(p.parent))
    return parse_reel(src, str(base_dir) if base_dir else None)


def _res(path: str, reel: Reel) -> Path:
    return resolve_path(path, reel.base_dir)


def load_deck(reel: Reel) -> CopyDeck:
    return copydeck.load_copy(_res(reel.copy, reel))


def validate(src: dict | str | Path | Reel, base_dir: str | Path | None = None, *,
             vo_durations: dict[str, float] | None = None,
             beats: list[float] | None = None, deep: bool = True) -> Report:
    rep = Report()
    try:
        reel = src if isinstance(src, Reel) else load_reel(src, base_dir)
    except ReelError as e:
        rep.errors += e.errors
        return rep
    except (ValueError, OSError) as e:
        rep.errors.append(f"cannot read reel: {e}")
        return rep
    rep.reel = reel
    try:
        cp = _res(reel.copy, reel)
        data = cp.read_bytes()                # ONE read: the bytes that are hash-checked are the bytes that are parsed
        # v3.1: inside a project only the approved, locked copy.json may be the deck (see project.check_reel_copy)
        lock = project.check_reel_copy(cp, reel.base_dir, data)
        if lock:
            rep.errors.append(lock)
            return rep
        rep.deck = copydeck.parse_copy(json.loads(data.decode("utf-8-sig")), str(cp))
    except (ReelError, ValueError, OSError) as e:
        rep.errors += e.errors if isinstance(e, ReelError) else [f"copy deck {reel.copy!r}: {e}"]
        return rep
    rep.errors += copydeck.check(reel, rep.deck)
    if deep:
        _check_files(reel, rep)
        _check_templates_and_fit(reel, rep)
    _check_timeline(reel, rep, vo_durations, beats)
    if deep and rep.ok:
        _check_reveal_timing(reel, rep)
    return rep


def _check_files(reel: Reel, rep: Report) -> None:
    def need(what: str, p: str | None) -> None:
        if not p:
            return
        try:
            _res(p, reel)
        except (ValueError, FileNotFoundError) as e:
            rep.errors.append(f"{what}: {e}")

    need("brand.font_caption", reel.brand.font_caption)
    need("brand.font_display", reel.brand.font_display)
    need("brand.logo", reel.brand.logo)
    need("audio.music", reel.audio.music)
    for i, sh in enumerate(reel.shots):
        w = f"shots[{i}] ({sh.id})"
        if sh.source.kind != "color":
            need(f"{w}.source.{sh.source.kind}", sh.source.value)
        if sh.source.kind == "funk2":
            _check_funk2_text(reel, sh.source.value, w, rep)
        if sh.product:
            need(f"{w}.product.image", sh.product.image)
    if reel.end_card and reel.end_card.product:
        need("end_card.product", reel.end_card.product)


def _check_funk2_text(reel: Reel, path: str, where: str, rep: Report) -> None:
    """A funk2 recipe must not draw text: text reaches pixels only through the copy deck."""
    try:
        d = json.loads(_res(path, reel).read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return                      # missing file is reported by need()
    t = d.get("text") if isinstance(d, dict) else None
    if (isinstance(t, dict) and t.get("text")) or (isinstance(t, str) and t):
        rep.errors.append(f"{where}.source.funk2: recipe {path!r} draws text; remove its `text` block "
                          f"(on-screen text comes only from the copy deck)")


def _check_templates_and_fit(reel: Reel, rep: Report) -> None:
    """Caption styles exist, text fits at >= 0.032 x height (errors name the copy id), end-card slots."""
    from . import captions, endcard
    errs_before = len(rep.errors)
    try:
        fonts = captions.Fonts(_res(reel.brand.font_caption, reel), _res(reel.brand.font_display, reel))
    except (ValueError, FileNotFoundError):
        return                      # already reported by _check_files
    W, H = reel.settings.width, reel.settings.height
    assert rep.deck is not None
    for i, sh in enumerate(reel.shots):
        c = sh.caption
        if c is None or c.copy_id not in rep.deck.lines:
            continue
        err = captions.check_caption(c.copy_id, rep.deck.text(c.copy_id), c.style, c.anchor, fonts, reel.brand, W, H,
                                     reel.settings.safe_area)
        if err:
            rep.errors.append(f"shots[{i}] ({sh.id}).caption: {err}")
    for i, sh in enumerate(reel.shots):                       # v3.1: caption/product collision rule
        c = sh.caption
        if c is None or sh.product is None or c.copy_id not in rep.deck.lines:
            continue
        try:
            lay = captions.layout_block(c.copy_id, rep.deck.text(c.copy_id), captions.load_style(c.style), fonts,
                                        reel.brand, W, H, anchor=c.anchor, safe_area=reel.settings.safe_area)
            asp = collision.image_aspect(_res(sh.product.image, reel))
        except (captions.CaptionFitError, ValueError, OSError):
            continue                                          # reported elsewhere
        pb = collision.product_box(sh.product.x, sh.product.y, sh.product.height, asp, W, H)
        ov = collision.overlap_fraction(collision.caption_box(lay), pb)
        if ov > collision.MAX_OVERLAP + 1e-9:
            rep.errors.append(f"shots[{i}] ({sh.id}): caption {c.copy_id!r} overlaps the product by {ov:.0%} of the "
                              f"caption area (limit {collision.MAX_OVERLAP:.0%}); move the caption anchor "
                              f"(top/bottom), or move/shrink the product")
    rep.errors += endcard.check(reel)
    if reel.end_card and len(rep.errors) == errs_before and all(
            c in rep.deck.lines for c in reel.end_card.copy.values()):
        try:
            endcard.EndCardRenderer(reel, rep.deck, W, H, fonts, lambda p: _res(p, reel))
        except (captions.CaptionFitError, ValueError, OSError) as e:
            rep.errors.append(f"end_card: {e}")


def _check_timeline(reel: Reel, rep: Report, vo: dict[str, float] | None,
                    beats: list[float] | None) -> None:
    try:
        try:
            from . import audio
        except ImportError:                 # audio module optional until R6
            audio = None
        if audio is not None:
            if vo is None and reel.audio.voiceover:
                vo = audio.vo_durations(reel, rep.deck)
            if beats is None and reel.audio.music:
                beats = audio.beat_grid_for(reel)
        rep.spans = timeline.build_timeline(reel, vo or {}, beats)
    except timeline.TimelineError as e:
        rep.errors.append(str(e))
        return
    except (ValueError, OSError, RuntimeError) as e:
        rep.errors.append(f"timeline: {e}")
        return
    rep.warnings += timeline.warnings(rep.spans)
    total = timeline.total_seconds(rep.spans)
    rep.info.update(total_seconds=total, shots=len(reel.shots),
                    summary=f"{len(reel.shots)} shots, {total:.2f}s total")


def _check_reveal_timing(reel: Reel, rep: Report) -> None:
    """Warn when a typewriter/words caption cannot finish before its shot ends (the audit would flag it)."""
    from . import captions
    assert rep.deck is not None
    try:
        fonts = captions.Fonts(_res(reel.brand.font_caption, reel), _res(reel.brand.font_display, reel))
    except (ValueError, FileNotFoundError):
        return
    for sp in rep.spans:
        if sp.kind != "shot":
            continue
        sh = reel.shots[sp.index]
        c = sh.caption
        if c is None or c.reveal not in ("typewriter", "words"):
            continue
        try:
            lay = captions.layout_block(c.copy_id, rep.deck.text(c.copy_id), captions.load_style(c.style), fonts,
                                        reel.brand, reel.settings.width, reel.settings.height, anchor=c.anchor,
                                        safe_area=reel.settings.safe_area)
        except (captions.CaptionFitError, ValueError):
            continue
        need = captions.CAPTION_DELAY + captions.reveal_seconds(lay, c.reveal, sp.vo_dur)
        if need > sp.duration + 1e-6:
            rep.warnings.append(f"shot {sh.id}: caption {c.copy_id!r} needs {need:.2f}s to finish its "
                                f"{c.reveal} reveal but the shot lasts {sp.duration:.2f}s")

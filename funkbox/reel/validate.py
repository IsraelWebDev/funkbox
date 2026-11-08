"""Whole-reel validation: schema, copy deck IDs, files, templates, caption fit, timing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from funkbox.config import resolve_path

from . import collision, copydeck, project, timeline
from . import grid as grid_mod
from .copydeck import CopyDeck
from .schema import Reel, ReelError, format_ids, materialize, parse_reel


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


def load_raw(src: dict | str | Path, base_dir: str | Path | None = None) -> tuple[dict, str | None]:
    """The reel's JSON object and the folder its paths resolve against."""
    if isinstance(src, (str, Path)):
        p = resolve_path(src, base_dir)
        return json.loads(p.read_text(encoding="utf-8-sig")), str(p.parent)
    return src, (str(base_dir) if base_dir else None)


def load_reel(src: dict | str | Path, base_dir: str | Path | None = None, fmt: str | None = None) -> Reel:
    """Parse a reel from a dict or a *.reel.json path (paths then resolve against its folder). ``fmt`` picks one
    of the reel's ``formats`` (its size, safe area and per-shot overrides)."""
    raw, bd = load_raw(src, base_dir)
    if fmt:
        if not isinstance(raw, dict):
            raise ReelError(["reel: must be a JSON object"])
        reel = parse_reel(materialize(raw, fmt), bd)
        reel.format_id = fmt
        return reel
    return parse_reel(raw, bd)


def formats_of(src: dict | str | Path | Reel, base_dir: str | Path | None = None) -> list[str]:
    """Format ids a reel declares ([] = it renders only at settings.width x settings.height)."""
    if isinstance(src, Reel):
        return [f.id for f in src.formats]
    try:
        return format_ids(load_raw(src, base_dir)[0])
    except (ValueError, OSError):
        return []


def _res(path: str, reel: Reel) -> Path:
    return resolve_path(path, reel.base_dir)


def validate(
    src: dict | str | Path | Reel,
    base_dir: str | Path | None = None,
    *,
    vo_durations: dict[str, float] | None = None,
    beats: list[float] | None = None,
    deep: bool = True,
    fmt: str | None = None,
) -> Report:
    """Validate a reel. Without ``fmt``, a reel with ``formats`` is also checked in every format (caption fit,
    framing and the end card depend on the frame size); those problems are prefixed with ``[format]``."""
    rep = _validate_one(src, base_dir, vo_durations=vo_durations, beats=beats, deep=deep, fmt=fmt)
    if fmt is None and rep.reel is not None and rep.reel.formats and not isinstance(src, Reel):
        for f in rep.reel.formats:
            sub = _validate_one(src, base_dir, vo_durations=vo_durations, beats=beats, deep=deep, fmt=f.id)
            rep.errors += [f"[{f.id}] {e}" for e in sub.errors if e not in rep.errors]
            rep.warnings += [f"[{f.id}] {w}" for w in sub.warnings if w not in rep.warnings]
        rep.info["formats"] = [f"{f.id} {f.width}x{f.height}" for f in rep.reel.formats]
        if rep.info.get("summary"):
            rep.info["summary"] += "; formats " + ", ".join(rep.info["formats"])
    return rep


def _validate_one(src, base_dir, *, vo_durations, beats, deep: bool, fmt: str | None) -> Report:
    rep = Report()
    try:
        reel = src if isinstance(src, Reel) else load_reel(src, base_dir, fmt)
    except ReelError as e:
        rep.errors += e.errors
        return rep
    except (ValueError, OSError) as e:
        rep.errors.append(f"cannot read reel: {e}")
        return rep
    rep.reel = reel
    rep.warnings += reel.warnings
    try:
        cp = _res(reel.copy, reel)
        data = cp.read_bytes()  # ONE read: the bytes that are hash-checked are the bytes that are parsed
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
    if deep:
        _check_grid(reel, rep, beats)
        _check_grid_geometry(reel, rep)
    if deep and rep.ok:
        _check_grid_label_crops(reel, rep)
        _check_framing(reel, rep)
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

    def need_font(what: str, spec: str | None) -> None:
        if not spec:
            return
        from .. import fonts as fontsmod

        try:
            fontsmod.resolve(spec, reel.base_dir)
        except (ValueError, FileNotFoundError, OSError) as e:  # FontError is a ValueError
            rep.errors.append(f"{what}: {e}")

    need_font("brand.font_caption", reel.brand.font_caption)
    need_font("brand.font_display", reel.brand.font_display)
    need_font("brand.font_fallback", reel.brand.font_fallback)
    need("brand.logo", reel.brand.logo)
    need("audio.music", reel.audio.music)
    for i, sh in enumerate(reel.shots):
        w = f"shots[{i}] ({sh.id})"
        if sh.source.grid is not None:
            for j, cell in enumerate(sh.source.grid.cells):
                need(f"{w}.source.grid.cells[{j}].video", cell.video)
        elif sh.source.kind != "color":
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
        return  # missing file is reported by need()
    t = d.get("text") if isinstance(d, dict) else None
    if (isinstance(t, dict) and t.get("text")) or (isinstance(t, str) and t):
        rep.errors.append(
            f"{where}.source.funk2: recipe {path!r} draws text; remove its `text` block "
            f"(on-screen text comes only from the copy deck)"
        )


def _check_templates_and_fit(reel: Reel, rep: Report) -> None:
    """Caption styles exist, text fits at >= 0.032 x height (errors name the copy id), end-card slots."""
    from . import captions, endcard
    from .. import fonts as fontsmod

    errs_before = len(rep.errors)
    try:
        fonts = captions.fonts_for(reel.brand, reel.base_dir)
    except (ValueError, FileNotFoundError):
        return  # already reported by _check_files
    W, H = reel.settings.width, reel.settings.height
    assert rep.deck is not None
    for i, sh in enumerate(reel.shots):
        c = sh.caption
        if c is None or c.copy_id not in rep.deck.lines:
            continue
        err = captions.check_caption(
            c.copy_id, rep.deck.text(c.copy_id), c.style, c.anchor, fonts, reel.brand, W, H, reel.settings.safe_area
        )
        if err:
            rep.errors.append(f"shots[{i}] ({sh.id}).caption: {err}")
        used = captions.fallback_chars(fonts, "caption", rep.deck.text(c.copy_id))
        if used:
            rep.warnings.append(
                f"shots[{i}] ({sh.id}).caption: copy {c.copy_id!r} draws {fontsmod.describe(used)} in the fallback "
                f"font {fonts.fallback.name} (the caption font {fonts.caption.name} has no glyph for them)"
            )
    if reel.hook:  # the hook title is shot 1's caption, large and boxed: it must fit this format
        c0 = reel.shots[0].caption
        d0, target = reel.shots[0].duration, reel.shot_target_s
        if (isinstance(d0, float) and d0 < captions.HOOK_SECONDS) or (d0 == "auto" and target is not None and target < captions.HOOK_SECONDS):
            rep.errors.append(f"shots[0] ({reel.shots[0].id}).duration: the hook needs at least {captions.HOOK_SECONDS} s on shot 1")
        if c0 is None or c0.copy_id not in rep.deck.lines:
            rep.errors.append("hook: true needs a caption with a copy line on shots[0]; its text is the hook title")
        else:
            try:
                captions.layout_block(
                    c0.copy_id,
                    rep.deck.text(c0.copy_id),
                    captions.hook_style(captions.load_style(c0.style)),
                    fonts,
                    reel.brand,
                    W,
                    H,
                    anchor="center",
                    safe_area=reel.settings.safe_area,
                )
            except (captions.CaptionFitError, ValueError) as e:
                rep.errors.append(f"shots[0] ({reel.shots[0].id}).hook: {e}")
    for i, sh in enumerate(reel.shots):  # grid labels: the same fit check, on the cell
        g = sh.source.grid
        if g is None:
            continue
        if grid_mod.geometry_error(g.cols, g.rows, g.gap, W, H):
            continue  # reported by _check_grid_geometry
        rects = grid_mod.cell_rects(g.cols, g.rows, g.gap, W, H)
        for j, (cell, rect) in enumerate(zip(g.cells, rects)):
            if cell.label and cell.label not in rep.deck.lines:
                continue
            lab = grid_mod.label_for(cell, j, rep.deck.text)
            if lab is None:
                continue
            where = f"shots[{i}] ({sh.id}).source.grid.cells[{j}].label"
            try:
                grid_mod.layout_label(
                    lab[0],
                    lab[1],
                    cell.label_style,
                    fonts,
                    reel.brand,
                    rect,
                    W,
                    H,
                    reel.settings.safe_area,
                )
            except (captions.CaptionFitError, ValueError) as e:  # GlyphError is a ValueError
                rep.errors.append(
                    f"{where}: " + (f"copy {lab[0]!r}: {e}" if isinstance(e, captions.GlyphError) else str(e))
                )
    for i, sh in enumerate(reel.shots):  # v3.1: caption/product collision rule
        c = sh.caption
        if c is None or sh.product is None or c.copy_id not in rep.deck.lines:
            continue
        try:
            # no frame here: "auto" is checked as "bottom" (the render may pick another anchor from the frame)
            anchor = "bottom" if c.anchor == "auto" else c.anchor
            lay = captions.layout_block(
                c.copy_id,
                rep.deck.text(c.copy_id),
                captions.load_style(c.style),
                fonts,
                reel.brand,
                W,
                H,
                anchor=anchor,
                safe_area=reel.settings.safe_area,
            )
            asp = collision.image_aspect(_res(sh.product.image, reel))
        except (captions.CaptionFitError, ValueError, OSError):
            continue  # reported elsewhere
        pb = collision.product_box(sh.product.x, sh.product.y, sh.product.height, asp, W, H)
        ov = collision.overlap_fraction(collision.caption_box(lay), pb)
        if ov > collision.MAX_OVERLAP + 1e-9:
            rep.errors.append(
                f"shots[{i}] ({sh.id}): caption {c.copy_id!r} overlaps the product by {ov:.0%} of the "
                f"caption area (limit {collision.MAX_OVERLAP:.0%}); move the caption anchor "
                f"(top/bottom), or move/shrink the product"
            )
    rep.errors += endcard.check(reel)
    if (
        reel.end_card
        and len(rep.errors) == errs_before
        and all(c in rep.deck.lines for c in reel.end_card.copy.values())
    ):
        try:
            endcard.EndCardRenderer(reel, rep.deck, W, H, fonts, lambda p: _res(p, reel))
        except (captions.CaptionFitError, ValueError, OSError) as e:
            rep.errors.append(f"end_card: {e}")


def _check_timeline(reel: Reel, rep: Report, vo: dict[str, float] | None, beats: list[float] | None) -> None:
    try:
        try:
            from . import audio
        except ImportError:  # audio module optional until R6
            audio = None
        if audio is not None:
            if vo is None and reel.audio.voiceover:
                vo = audio.vo_durations(reel, rep.deck)
            if beats is None and (reel.audio.music or reel.audio.bpm):
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
    rep.info.update(total_seconds=total, shots=len(reel.shots), summary=f"{len(reel.shots)} shots, {total:.2f}s total")


def _check_grid(reel: Reel, rep: Report, beats: list[float] | None) -> None:
    """A grid's ``beats:N`` stagger needs a beat grid; a cell that cannot finish arriving before its
    shot ends is an error (it is never drawn, or cut mid-fade, and its label is never audited)."""
    shots = [(i, sh) for i, sh in enumerate(reel.shots) if sh.source.grid is not None]
    if not shots:
        return
    period = None
    try:
        if beats is None and (reel.audio.music or reel.audio.bpm):
            from . import audio

            beats = audio.beat_grid_for(reel)
        if beats and len(beats) >= 2:
            period = timeline.beat_period(beats)
    except (ImportError, ValueError, OSError, RuntimeError, timeline.TimelineError):
        period = None  # the timeline check reports a broken beat grid
    for i, sh in shots:
        g = sh.source.grid
        assert g is not None
        try:
            stagger = grid_mod.stagger_seconds(g, period)
        except ValueError as e:
            if not (reel.audio.music or reel.audio.bpm):  # with music or bpm set, the timeline check has the cause
                rep.errors.append(f"shots[{i}] ({sh.id}).source.grid.stagger: {e}")
            continue
        sp = next((s for s in rep.spans if s.kind == "shot" and s.index == i), None)
        if g.reveal != "sequence" or sp is None:
            continue
        late = [
            j
            for j in range(len(g.cells))
            if not grid_mod.arrives_in_time(grid_mod.arrival(g, j, stagger), sp.duration, reel.settings.fps)
        ]
        if late:
            rep.errors.append(
                f"shots[{i}] ({sh.id}).source.grid: cell(s) {late} arrive at "
                f"{grid_mod.arrival(g, late[0], stagger):.2f}s or later but the shot lasts "
                f"{sp.duration:.2f}s: they could not finish arriving (a cell is drawn from the frame "
                f"after its arrival, and its label is audited); shorten the stagger or lengthen the shot"
            )


def _check_grid_geometry(reel: Reel, rep: Report) -> None:
    """A grid whose gap leaves no room in the frame, or whose cells would be tiny, cannot be laid out: an error."""
    W, H = reel.settings.width, reel.settings.height
    for i, sh in enumerate(reel.shots):
        g = sh.source.grid
        if g is None:
            continue
        err = grid_mod.geometry_error(g.cols, g.rows, g.gap, W, H)
        if err:
            rep.errors.append(f"shots[{i}] ({sh.id}).source.grid: {err}")


def _check_grid_label_crops(reel: Reel, rep: Report) -> None:
    """Warn when the shot's motion (it moves the whole composed grid) cuts a cell label off at the frame edge."""
    from . import captions

    assert rep.deck is not None
    try:
        fonts = captions.fonts_for(reel.brand, reel.base_dir)
    except (ValueError, FileNotFoundError):
        return
    W, H = reel.settings.width, reel.settings.height
    for i, sh in enumerate(reel.shots):
        g = sh.source.grid
        sp = next((s for s in rep.spans if s.kind == "shot" and s.index == i), None)
        if g is None or sp is None or sh.motion.type not in ("kenburns", "frame"):
            continue
        for j, (cell, rect) in enumerate(zip(g.cells, grid_mod.cell_rects(g.cols, g.rows, g.gap, W, H))):
            if cell.label and cell.label not in rep.deck.lines:
                continue
            lab = grid_mod.label_for(cell, j, rep.deck.text)
            if lab is None:
                continue
            try:
                lay = grid_mod.layout_label(
                    lab[0],
                    lab[1],
                    cell.label_style,
                    fonts,
                    reel.brand,
                    rect,
                    W,
                    H,
                    reel.settings.safe_area,
                )
            except (captions.CaptionFitError, ValueError):
                continue  # reported by the fit check
            if grid_mod.motion_crops(
                sh.motion, (rect[0] + lay.x0, rect[1] + lay.y0, lay.box_w, lay.box_h), W, H, sp.duration
            ):
                rep.warnings.append(
                    f"shots[{i}] ({sh.id}).source.grid.cells[{j}].label: the shot's "
                    f"{sh.motion.type} motion (it moves the whole grid) cuts this label off at the "
                    f"frame edge; use a smaller zoom/frame, or no motion on a labelled grid"
                )


def _check_framing(reel: Reel, rep: Report) -> None:
    """Warn when a `frame` rectangle cannot be shown whole at this aspect (cover fit crops it), and when a
    trimmed / sped-up clip runs out before its shot ends (its last frame is held)."""
    from . import framing

    W, H = reel.settings.width, reel.settings.height
    for i, sh in enumerate(reel.shots):
        src = sh.source
        if src.kind not in ("image", "video"):
            continue
        try:
            path = _res(src.value, reel)
            if src.kind == "video":
                info = framing.probe_video(path)
                sw, shh = framing.decode_size(info)
            else:
                sw, shh = framing.image_size(path)
                info = None
        except (ValueError, OSError):
            continue
        where = f"shot {sh.id}"
        if sh.motion.type == "frame" and src.fit == "cover":
            worst = max(
                (
                    framing.window(k.rect, sw, shh, W, H, fit="cover").cut
                    for k in sh.motion.keys
                    if k.rect != framing.FULL
                ),
                default=0.0,
            )  # the full frame is the normal cover crop
            if worst > 0.02:
                rep.warnings.append(
                    f"{where}: at {W}x{H} a framed rectangle is {worst:.0%} cut off (the rect is too wide "
                    f'or too tall for this aspect); use fit "contain" (with pad) or a per-format '
                    f"motion override"
                )
        if info is not None and info.duration and not src.loop:
            start, end = src.trim if src.trim else (0.0, info.duration)
            sp = next((s for s in rep.spans if s.kind == "shot" and s.index == i), None)
            if sp is not None:
                need = framing.source_time(sp.duration, sp.duration, src.speed)
                if need > (end - start) + 0.05:
                    rep.warnings.append(
                        f"{where}: plays {need:.2f}s of {src.value} at speed {list(src.speed)} but only "
                        f"{end - start:.2f}s are available; the last frame is held (set loop, trim "
                        f"or a faster speed)"
                    )


def _check_reveal_timing(reel: Reel, rep: Report) -> None:
    """Warn when a typewriter/words caption cannot finish before its shot ends (the audit would flag it)."""
    from . import captions

    assert rep.deck is not None
    try:
        fonts = captions.fonts_for(reel.brand, reel.base_dir)
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
            lay = captions.layout_block(
                c.copy_id,
                rep.deck.text(c.copy_id),
                captions.load_style(c.style),
                fonts,
                reel.brand,
                reel.settings.width,
                reel.settings.height,
                anchor=c.anchor,
                safe_area=reel.settings.safe_area,
            )
        except (captions.CaptionFitError, ValueError):
            continue
        need = captions.CAPTION_DELAY + captions.reveal_seconds(lay, c.reveal, sp.vo_dur, sp.duration)
        if need > sp.duration + 1e-6:
            rep.warnings.append(
                f"shot {sh.id}: caption {c.copy_id!r} needs {need:.2f}s to finish its "
                f"{c.reveal} reveal but the shot lasts {sp.duration:.2f}s"
            )

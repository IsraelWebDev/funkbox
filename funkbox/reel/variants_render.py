"""Reel variants, card 2 and 3: render every variant, audit it, write a sidecar and a manifest, and build the contact sheet.

Layout under ``<out_dir>/<name>-variants/``: ``vNN-<format>.mp4`` (one per format the reel declares, or ``vNN.mp4``),
``vNN-<format>.text_audit.json`` (one audit per rendered file), ``vNN.reel.json`` (the expanded variant reel),
``contact_sheet.png`` and ``contact_sheet.text_audit.json`` (with ``sheet=True``), and ``variants.json`` (the manifest).

Every variant is validated, copy lock included, before the first render. A failed text audit, or a failed sheet, is
recorded in the manifest and raised after it is written, so a caller cannot ship a bad variant by ignoring the result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import traceback
from pathlib import Path

from .variants import ReelVariantError, expand_reel, variant_hash

SHEET_SECONDS = 3.0  # the sheet shot's length: each cell plays its variant's opening from the start


def grid_shape(n: int) -> tuple[int, int]:
    """(cols, rows) with cols * rows == n and cols >= rows, as square as the count allows (a prime gives one row)."""
    best = (n, 1)
    for rows in range(1, n + 1):
        if n % rows == 0:
            cols = n // rows
            if cols >= rows and cols - rows < best[0] - best[1]:
                best = (cols, rows)
    return best


def build_sheet_reel(base: dict, cells: list[str]) -> dict:
    """A contact sheet: one grid shot, one cell per video in ``cells``, each cell labelled with its 1-based number."""
    cols, rows = grid_shape(len(cells))
    reel = copy.deepcopy(base)
    for key in ("end_card", "formats"):
        reel.pop(key, None)
    reel["audio"] = {"voiceover": False}
    grid_cells = [{"video": v, "fit": "cover", "label": {"index": True}} for v in cells]
    reel["shots"] = [{
        "id": "sheet",
        "source": {"grid": {"cols": cols, "rows": rows, "cells": grid_cells}},
        "motion": {"type": "none"},
        "duration": SHEET_SECONDS,
    }]
    return reel


def _manifest_hash(base_hash: str, vary: dict, flags: dict, variants: list[dict], sheet: dict | None) -> str:
    """Hash of what the run decided (inputs, flags, reel hashes, outputs, audit results): no paths, no clock.

    The sheet enters the hash only when there is one, so a run without a sheet hashes as it did before the sheet existed.
    """
    core = {
        "base_reel_sha256": base_hash,
        "vary": vary,
        "flags": flags,
        "variants": [
            {"index": v["index"], "reel_sha256": v["reel_sha256"], "audit_ok": v["audit_ok"], "outputs": v["outputs"]}
            for v in variants
        ],
    }
    if sheet is not None:
        core["sheet"] = {k: sheet.get(k) for k in ("cols", "rows", "audit_ok", "error")}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _clear_stale(vdir: Path) -> None:
    """Remove the outputs of an earlier run here, so no file outlives the manifest that describes it."""
    for p in vdir.glob("v[0-9][0-9]*"):
        p.unlink(missing_ok=True)
    for name in ("variants.json", "variants.json.tmp", "contact_sheet.png", "contact_sheet.mp4",
                 "contact_sheet.text_audit.json"):
        (vdir / name).unlink(missing_ok=True)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _sheet(reel: dict, entries: list[dict], vdir: Path, base_dir, draft: bool, sheet_video: bool) -> tuple[dict, str | None]:
    """Render the contact sheet. Returns (sheet info, error): a failure is recorded in the info and returned, never raised here."""
    from . import render

    cols, rows = grid_shape(len(entries))
    missing = [e["index"] for e in entries if not any(n.endswith(".mp4") for n in e["outputs"])]
    if missing:
        msg = f"contact sheet: variant {missing[0]} has no video to show (see {vdir})"
        return {"cols": cols, "rows": rows, "audit_ok": False, "error": msg}, msg
    cells = [str(vdir / next(n for n in e["outputs"] if n.endswith(".mp4"))) for e in entries]
    try:
        out = render.render_reel(
            build_sheet_reel(reel, cells), "contact_sheet",
            base_dir=base_dir, out_dir=vdir, draft=draft, audit=True, with_audio=False,
        )
    except Exception as e:  # recorded with its traceback in the manifest, and raised after it, so it is never silent
        msg = f"contact sheet: {type(e).__name__}: {e}"
        return {"cols": cols, "rows": rows, "audit_ok": False, "error": msg, "traceback": traceback.format_exc()}, msg
    if not sheet_video:
        (vdir / "contact_sheet.mp4").unlink(missing_ok=True)
    info = {
        "png": "contact_sheet.png",
        "audit": Path(out.paths["text_audit"]).name,
        "cols": cols,
        "rows": rows,
        "audit_ok": out.audit_ok,
    }
    return info, None

def render_variants(
    reel: dict,
    vary: dict,
    name: str,
    *,
    base_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    draft: bool = False,
    audit: bool = True,
    with_audio: bool = True,
    sheet: bool = False,
    sheet_video: bool = False,
) -> dict:
    """Render each variant of ``reel`` (and the contact sheet with ``sheet``) and return the manifest.

    Raises ``ReelVariantError`` before any render starts if a variant is invalid, and after the manifest is
    written if a text audit or the sheet fails.
    """
    from ..config import funk_out
    from . import loudness, render

    variants, labels = expand_reel(reel, vary, base_dir=base_dir, deep=True)
    root = Path(out_dir) if out_dir else funk_out()
    vdir = root / f"{name}-variants"
    vdir.mkdir(parents=True, exist_ok=True)
    _clear_stale(vdir)
    flags = {"draft": draft, "audit": audit, "with_audio": with_audio}
    if sheet:
        flags["sheet"] = True
    render._log(f"rendering {len(variants)} variants ({'draft' if draft else 'full'}) to {vdir}")

    entries: list[dict] = []
    for i, (variant, label) in enumerate(zip(variants, labels), start=1):
        stem = f"v{i:02d}"
        reel_hash = variant_hash(variant)
        (vdir / f"{stem}.reel.json").write_text(json.dumps(variant, indent=2, ensure_ascii=False), encoding="utf-8")
        t0 = time.perf_counter()
        outs = render.render_formats(
            variant, stem, base_dir=base_dir, out_dir=vdir, draft=draft, audit=audit, with_audio=with_audio
        )
        # Names come from disk, not from the render result, so an unmuxed silent file is listed too.
        outputs = sorted(
            p.name for p in vdir.glob(f"{stem}*") if p.suffix == ".mp4" or p.name.endswith(".text_audit.json")
        )
        # Measure exactly the MP4s this render produced (not a folder glob: stale files and v10/v100 clashes).
        target = loudness.target_for(variant)
        videos = [o.paths["mp4"] for o in outs if o.paths.get("mp4")]
        loud = loudness.measure_outputs(videos, target) if with_audio else {}
        entries.append({
            "index": i,
            "label": label,
            "vary": {k: vals[i - 1] for k, vals in vary.items()},
            "reel": f"{stem}.reel.json",
            "reel_sha256": reel_hash,
            "outputs": outputs,
            "audit_ok": (all(o.audit_ok for o in outs) if audit else None),
            "loudness": loud,  # a report: measured on the file, not part of the manifest hash
            "loudness_ok": loudness.all_ok(loud),
            "render_seconds": round(time.perf_counter() - t0, 2),
        })

    sheet_info, sheet_error = None, None
    if sheet:
        sheet_info, sheet_error = _sheet(reel, entries, vdir, base_dir, draft, sheet_video)

    manifest = {
        "name": name,
        "count": len(entries),
        "flags": flags,
        "base_reel_sha256": variant_hash(reel),
        "vary": vary,
        "manifest_sha256": _manifest_hash(variant_hash(reel), vary, flags, entries, sheet_info),
        "variants": entries,
    }
    if sheet:
        manifest["sheet"] = sheet_info
    _write_atomic(vdir / "variants.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    failed = [f"variant {e['index']}: text audit failed (see {vdir}/v{e['index']:02d}*.text_audit.json)"
              for e in entries if e["audit_ok"] is False]
    if sheet_error:
        failed.append(sheet_error)
    elif sheet_info is not None and sheet_info["audit_ok"] is False:
        failed.append(f"contact sheet: text audit failed (see {vdir}/contact_sheet.text_audit.json)")
    if failed:
        raise ReelVariantError(failed)
    return manifest
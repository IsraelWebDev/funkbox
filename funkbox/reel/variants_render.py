"""Reel variants, card 2: render every variant, audit it, and write a sidecar and a manifest.

Layout under ``<out_dir>/<name>-variants/``: ``vNN-<format>.mp4`` (one per format the reel declares, or
``vNN.mp4``), ``vNN-<format>.text_audit.json`` (one audit per rendered file), ``vNN.reel.json`` (the expanded
variant reel, before the timeline is built), and ``variants.json`` (the manifest).

Every variant is validated, copy lock included, before the first render. A failed text audit is recorded in the
manifest and raised after it is written, so a caller cannot ship a bad variant by ignoring the return value.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .variants import ReelVariantError, expand_reel, variant_hash


def _manifest_hash(base_hash: str, vary: dict, flags: dict, variants: list[dict]) -> str:
    """Hash of what the run decided (inputs, flags, reel hashes, outputs, audit results): no paths, no clock."""
    core = {
        "base_reel_sha256": base_hash,
        "vary": vary,
        "flags": flags,
        "variants": [
            {"index": v["index"], "reel_sha256": v["reel_sha256"], "audit_ok": v["audit_ok"], "outputs": v["outputs"]}
            for v in variants
        ],
    }
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _clear_stale(vdir: Path) -> None:
    """Remove the outputs of an earlier run here, so no file outlives the manifest that describes it."""
    for p in vdir.glob("v[0-9][0-9]*"):
        p.unlink(missing_ok=True)
    (vdir / "variants.json").unlink(missing_ok=True)
    (vdir / "variants.json.tmp").unlink(missing_ok=True)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
) -> dict:
    """Render each variant of ``reel`` and return the manifest (also written to ``variants.json``).

    Raises ``ReelVariantError`` before any render starts if a variant is invalid, and after the manifest is
    written if any variant's text audit fails.
    """
    from ..config import funk_out
    from . import render

    variants, labels = expand_reel(reel, vary, base_dir=base_dir, deep=True)
    root = Path(out_dir) if out_dir else funk_out()
    vdir = root / f"{name}-variants"
    vdir.mkdir(parents=True, exist_ok=True)
    _clear_stale(vdir)
    flags = {"draft": draft, "audit": audit, "with_audio": with_audio}
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
        entries.append({
            "index": i,
            "label": label,
            "vary": {k: vals[i - 1] for k, vals in vary.items()},
            "reel": f"{stem}.reel.json",
            "reel_sha256": reel_hash,
            "outputs": outputs,
            "audit_ok": (all(o.audit_ok for o in outs) if audit else None),
            "render_seconds": round(time.perf_counter() - t0, 2),
        })

    manifest = {
        "name": name,
        "count": len(entries),
        "flags": flags,
        "base_reel_sha256": variant_hash(reel),
        "vary": vary,
        "manifest_sha256": _manifest_hash(variant_hash(reel), vary, flags, entries),
        "variants": entries,
    }
    _write_atomic(vdir / "variants.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    failed = [e["index"] for e in entries if e["audit_ok"] is False]
    if failed:
        raise ReelVariantError([
            f"variant {i}: text audit failed (see {vdir}/v{i:02d}*.text_audit.json)" for i in failed
        ])
    return manifest
"""Reel variants, card 3: the contact sheet, one labelled cell per variant (``label: {"index": true}``)."""

import json
from types import SimpleNamespace

import pytest

from funkbox.reel import render
from funkbox.reel.validate import validate
from funkbox.reel.variants import ReelVariantError
from funkbox.reel.variants_render import grid_shape, render_variants


def plain_reel(demo):
    """The demo reel with voiceover and music off and 3 s shots (a caption needs room to finish typing)."""
    d = json.loads((demo / "demo.reel.json").read_text(encoding="utf-8"))
    d["audio"] = {"voiceover": False}
    for sh in d["shots"]:
        sh["duration"] = 3.0
        sh.pop("vo", None)
    return d


WARM = "#f6f7f4,#0f7c6e"
DARK = "#0f1216,#4fd1b9"


@pytest.mark.parametrize("n, shape", [(1, (1, 1)), (2, (2, 1)), (4, (2, 2)), (5, (5, 1)), (6, (3, 2)), (12, (4, 3))])
def test_grid_shape_is_as_square_as_the_count_allows(n, shape):
    assert grid_shape(n) == shape


def test_index_label_is_accepted_and_other_label_forms_are_refused(work, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(work))
    (work / "copy.json").write_text(json.dumps({"brand_name": "T", "lines": {"hook": {"text": "HI."}}}), encoding="utf-8")

    def reel(label):
        cells = [{"video": "a.mp4", "label": label}, {"video": "b.mp4"}]
        return {
            "version": 3,
            "settings": {"width": 1080, "height": 1920, "fps": 30},
            "brand": {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff"},
            "copy": "copy.json",
            "audio": {"voiceover": False},
            "shots": [{"id": "s1", "source": {"grid": {"cols": 2, "rows": 1, "cells": cells}},
                       "motion": {"type": "none"}, "duration": 2}],
        }

    for good in ({"index": True}, "hook"):  # the index form, and an ordinary copy id
        rep = validate(reel(good), base_dir=work, deep=False)
        assert not any("cells[0].label" in e for e in rep.errors), (good, rep.errors)
    for bad in ({"index": False}, {"index": True, "extra": 1}, {"index": 1}, {"text": "1"}, 3, "variant_index"):
        rep = validate(reel(bad), base_dir=work, deep=False)
        assert not rep.ok and any("cells[0].label" in e for e in rep.errors), (bad, rep.errors)


def test_contact_sheet_has_one_index_entry_per_variant_with_the_matching_digits(demo, work):
    manifest = render_variants(plain_reel(demo), {"brand.palette": [WARM, DARK]}, "demo", base_dir=demo,
                               out_dir=work, draft=True, with_audio=False, sheet=True)
    vdir = work / "demo-variants"
    assert (vdir / "contact_sheet.png").exists()
    assert not (vdir / "contact_sheet.mp4").exists(), "the sheet video is kept only when asked for"
    audit = json.loads((vdir / "contact_sheet.text_audit.json").read_text(encoding="utf-8"))
    assert audit["ok"] is True, audit["missing"] + audit["inexact"]
    cells = [e for e in audit["entries"] if e["slot"] == "variant_index"]
    assert [e["instance"] for e in cells] == ["0", "1"]
    assert [e["text"] for e in cells] == ["1", "2"]
    assert all(e["text_exact"] and e["copy_id"] is None for e in cells)
    assert manifest["sheet"]["audit_ok"] is True and (manifest["sheet"]["cols"], manifest["sheet"]["rows"]) == (2, 1)


def test_a_failed_sheet_is_raised_after_the_manifest_is_written(demo, work, monkeypatch):
    real = render.render_reel

    def fake(src, name, *, out_dir, **kw):
        if name != "contact_sheet":
            return real(src, name, out_dir=out_dir, **kw)
        audit = out_dir / "contact_sheet.text_audit.json"
        audit.write_text("{}", encoding="utf-8")
        return SimpleNamespace(paths={"text_audit": str(audit)}, audit_ok=False)

    monkeypatch.setattr(render, "render_reel", fake)
    with pytest.raises(ReelVariantError, match="contact sheet: text audit failed"):
        render_variants(plain_reel(demo), {"brand.palette": [WARM, DARK]}, "demo", base_dir=demo,
                        out_dir=work, draft=True, with_audio=False, sheet=True)
    manifest = json.loads((work / "demo-variants" / "variants.json").read_text(encoding="utf-8"))
    assert manifest["sheet"]["audit_ok"] is False
    assert len(manifest["variants"]) == 2
"""Reel variants, card 3: the contact sheet, one labelled cell per variant (``label: {"index": true}``)."""

import json

import pytest

from funkbox.reel.validate import validate
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


def test_index_label_is_accepted_and_other_label_objects_are_refused(work, monkeypatch):
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

    ok = validate(reel({"index": True}), base_dir=work, deep=False)
    assert not any("label" in e for e in ok.errors), ok.errors
    for bad in ({"index": False}, {"index": True, "extra": 1}, {"text": "1"}, 3):
        rep = validate(reel(bad), base_dir=work, deep=False)
        assert not rep.ok and any("label" in e for e in rep.errors), bad


def test_contact_sheet_has_one_index_entry_per_variant_with_the_matching_digits(demo, work):
    reel = plain_reel(demo)
    manifest = render_variants(reel, {"brand.palette": [WARM, DARK]}, "demo", base_dir=demo, out_dir=work,
                               draft=True, with_audio=False, sheet=True)
    vdir = work / "demo-variants"
    assert (vdir / "contact_sheet.png").exists()
    assert not (vdir / "contact_sheet.mp4").exists(), "the sheet video is kept only when asked for"
    audit = json.loads((vdir / "contact_sheet.text_audit.json").read_text(encoding="utf-8"))
    assert audit["ok"] is True, audit["missing"] + audit["inexact"]
    cells = [e for e in audit["entries"] if e["slot"] == "variant_index"]
    assert [e["instance"] for e in cells] == ["0", "1"]
    assert [e["text"] for e in cells] == ["1", "2"]
    assert all(e["text_exact"] for e in cells)
    assert manifest["sheet"]["audit_ok"] is True and (manifest["sheet"]["cols"], manifest["sheet"]["rows"]) == (2, 1)
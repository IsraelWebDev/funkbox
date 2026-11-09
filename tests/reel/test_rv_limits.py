"""Reel variants, known limits: a second run into a folder that is being written is refused, and a run that
fails partway leaves ``variants.partial.json`` describing the variants that finished."""

import json

import pytest

from funkbox.reel import render
from funkbox.reel.variants import ReelVariantError
from funkbox.reel.variants_render import render_variants
from test_rv3_contact_sheet import plain_reel

WARM = "#f6f7f4,#0f7c6e"
DARK = "#0f1216,#4fd1b9"


def test_a_second_run_into_a_folder_being_written_is_refused(demo, work):
    vdir = work / "lk-variants"
    vdir.mkdir()
    lock = vdir / ".run.lock"
    lock.write_text("pid 1\n", encoding="utf-8")
    with pytest.raises(ReelVariantError, match="another run"):
        render_variants(plain_reel(demo), {"brand.palette": [WARM, DARK]}, "lk", base_dir=demo, out_dir=work,
                        draft=True, with_audio=False)
    assert lock.exists(), "the refused run must not remove the lock it did not take"


def test_a_failed_run_writes_a_partial_record_and_releases_the_lock(demo, work, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("encoder died")

    monkeypatch.setattr(render, "render_formats", boom)
    with pytest.raises(RuntimeError, match="encoder died"):
        render_variants(plain_reel(demo), {"brand.palette": [WARM, DARK]}, "pt", base_dir=demo, out_dir=work,
                        draft=True, with_audio=False)
    vdir = work / "pt-variants"
    partial = json.loads((vdir / "variants.partial.json").read_text(encoding="utf-8"))
    assert partial["complete"] is False and partial["error"] == "encoder died" and partial["variants"] == []
    assert not (vdir / "variants.json").exists()
    assert not (vdir / ".run.lock").exists(), "the lock is released when the run ends, even on failure"

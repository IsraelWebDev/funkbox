"""Style-file and end-card gaps left after the validation card: a bad caption style must be a reel error on every
path that loads it (grid labels included), and an end-card style block without a slot must be reported."""

import json

from conftest import make_plain_reel
from test_style_file_errors import _copy_templates, _style_dir
from funkbox.reel import captions
from funkbox.reel.validate import validate


def _grid_reel(demo, label_style):
    d = make_plain_reel(demo)
    cells = [{"video": "a.mp4", "label": "hook"}, {"video": "b.mp4"}]
    d["shots"][0]["source"] = {"grid": {"cols": 2, "rows": 1, "cells": cells}}
    d["shots"][0]["motion"] = {"type": "none"}
    d["shots"][0].pop("caption", None)
    d["shots"][0]["source"]["grid"]["cells"][0]["label_style"] = label_style
    return d


def test_bad_highlight_in_a_grid_label_style_is_a_reel_error(demo, tmp_path, monkeypatch):
    _style_dir(tmp_path, monkeypatch, "badgrid", {"highlight": {"colour": "primary"}})
    rep = validate(_grid_reel(demo, "badgrid"), base_dir=demo)
    assert not rep.ok
    assert any("cells[0].label" in e and "colour" in e for e in rep.errors), rep.text()


def test_a_grid_label_with_a_valid_style_adds_no_error(demo):
    rep = validate(_grid_reel(demo, "bold-pop"), base_dir=demo)
    assert not [e for e in rep.errors if "label" in e], rep.text()


def _endcard_with_extra_block(tmp_path, monkeypatch, name, block):
    """brand-split plus a headline style block the slot list does not declare."""
    tpl = json.loads((captions.TEMPLATES / "endcards" / "centered-logo.json").read_text(encoding="utf-8"))
    tpl[block] = dict(tpl["headline"])
    d = _copy_templates(tmp_path, monkeypatch)
    (d / "endcards" / f"{name}.json").write_text(json.dumps(tpl), encoding="utf-8")


def test_a_sub_style_block_with_no_sub_slot_is_a_reel_error(demo, tmp_path, monkeypatch):
    _endcard_with_extra_block(tmp_path, monkeypatch, "strayblock", "sub")
    d = make_plain_reel(demo)
    d["brand"] = {**d.get("brand", {}), "logo": "examples/cat.png"}
    d["end_card"] = {"template": "strayblock", "duration": 2.0, "copy": {"headline": "hook", "url": "hook"}}
    rep = validate(d, base_dir=demo)
    assert not rep.ok
    assert any("strayblock" in e and "'sub'" in e and "no slot" in e for e in rep.errors), rep.text()


def test_the_shipped_end_cards_have_no_stray_style_blocks(demo):
    """Every shipped template whose headline, sub or url block exists also declares that slot."""
    for f in (captions.TEMPLATES / "endcards").glob("*.json"):
        tpl = json.loads(f.read_text(encoding="utf-8"))
        for s in ("headline", "sub", "url"):
            if isinstance(tpl.get(s), dict):
                assert s in tpl["slots"], f"{f.name}: style block {s!r} with no slot"

"""Style gaps after the validation card: the end-card reverse check (a headline, sub or url style block with no
slot is dead config and a reel error) and regression guards for caption styles on the grid-label path."""

import json

import pytest

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
    """Regression guard for existing behaviour: a grid label's style is checked like any caption style."""
    _style_dir(tmp_path, monkeypatch, "badgrid", {"highlight": {"colour": "primary"}})
    rep = validate(_grid_reel(demo, "badgrid"), base_dir=demo)
    assert not rep.ok
    assert any("cells[0].label" in e and "colour" in e for e in rep.errors), rep.text()


def test_a_grid_label_with_a_valid_style_adds_no_error(demo):
    rep = validate(_grid_reel(demo, "bold-pop"), base_dir=demo)
    assert not [e for e in rep.errors if "label" in e], rep.text()


def _template_without_slot(tmp_path, monkeypatch, name, dropped):
    """brand-split with the slot ``dropped`` removed from its slot list; its style block stays."""
    tpl = json.loads((captions.TEMPLATES / "endcards" / "brand-split.json").read_text(encoding="utf-8"))
    tpl["slots"] = [s for s in tpl["slots"] if s != dropped]
    d = _copy_templates(tmp_path, monkeypatch)
    (d / "endcards" / f"{name}.json").write_text(json.dumps(tpl), encoding="utf-8")


@pytest.mark.parametrize("dropped", ["headline", "sub", "url"])
def test_a_style_block_with_no_slot_is_a_reel_error(demo, tmp_path, monkeypatch, dropped):
    name = f"stray_{dropped}"
    _template_without_slot(tmp_path, monkeypatch, name, dropped)
    d = make_plain_reel(demo)
    slots = [s for s in ("headline", "sub", "url") if s != dropped]
    d["end_card"] = {"template": name, "duration": 2.0, "copy": {s: "hook" for s in slots}}
    rep = validate(d, base_dir=demo)
    assert not rep.ok
    assert any(name in e and f"{dropped!r}" in e and "no slot" in e for e in rep.errors), rep.text()


def test_the_shipped_end_cards_have_no_stray_style_blocks():
    """Every shipped template whose headline, sub or url block exists also declares that slot. Reads JSON only,
    so it runs without a GL context."""
    for f in (captions.TEMPLATES / "endcards").glob("*.json"):
        tpl = json.loads(f.read_text(encoding="utf-8"))
        for s in ("headline", "sub", "url"):
            if isinstance(tpl.get(s), dict):
                assert s in tpl["slots"], f"{f.name}: style block {s!r} with no slot"

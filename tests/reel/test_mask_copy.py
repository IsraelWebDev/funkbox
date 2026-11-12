"""Shot source ``funk2`` with ``mask_copy_id``: the copy line is the recipe's one text mask.

The recipe's text-masked layer keeps its font and size but has no text of its own. The copy deck supplies the text, so
the copy lock and the text audit cover it like a caption.
"""

import json
from pathlib import Path

import pytest

from funkbox.reel import copydeck
from funkbox.reel.schema import ReelError, parse_reel
from funkbox.reel.validate import validate

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "layer_mask" / "funkbox-text-chrome.json"
W, H, FPS = 540, 960, 30


def recipe_dict(text=None):
    d = json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))
    d["layers"][0]["mask"].pop("text", None)
    if text is not None:
        d["layers"][0]["mask"]["text"] = text
    return d


def write_recipe(demo, name, d):
    p = demo / "mask_copy" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")
    return f"mask_copy/{name}"


def mask_reel(source):
    return {
        "copy": "inputs/brand/copy.json",
        "settings": {"width": W, "height": H, "fps": FPS},
        "shots": [{"id": "k", "source": source, "duration": 2.0}],
    }


def test_mask_copy_id_is_read_from_a_funk2_source():
    reel = parse_reel(mask_reel({"funk2": "mask_copy/x.json", "mask_copy_id": "end_head"}))
    assert reel.shots[0].source.mask_copy_id == "end_head"


def test_mask_copy_id_needs_a_funk2_source():
    with pytest.raises(ReelError, match="only a funk2 source"):
        parse_reel(mask_reel({"color": "#204060", "mask_copy_id": "end_head"}))


def test_the_copy_line_is_a_copy_reference():
    reel = parse_reel(mask_reel({"funk2": "mask_copy/x.json", "mask_copy_id": "end_head"}))
    assert ("shots[0] (k).source.mask_copy_id", "end_head") in copydeck.used_ids(reel)


def test_a_recipe_without_mask_text_validates(demo):
    src = {"funk2": write_recipe(demo, "ok.json", recipe_dict()), "mask_copy_id": "end_head"}
    rep = validate(mask_reel(src), demo)
    assert rep.ok, rep.errors


def test_an_unknown_copy_id_is_refused(demo):
    src = {"funk2": write_recipe(demo, "ok.json", recipe_dict()), "mask_copy_id": "nope"}
    rep = validate(mask_reel(src), demo)
    assert any("unknown copy id 'nope'" in e for e in rep.errors), rep.errors


def test_a_recipe_with_its_own_mask_text_is_refused(demo):
    src = {"funk2": write_recipe(demo, "own.json", recipe_dict(text="OWN")), "mask_copy_id": "end_head"}
    rep = validate(mask_reel(src), demo)
    assert not rep.ok
    assert any("sets its own mask text" in e for e in rep.errors), rep.errors


def test_a_recipe_needs_exactly_one_text_mask(demo):
    d = recipe_dict()
    d["layers"] = d["layers"] * 2
    src = {"funk2": write_recipe(demo, "two.json", d), "mask_copy_id": "end_head"}
    rep = validate(mask_reel(src), demo)
    assert any("exactly one text-masked layer (found 2)" in e for e in rep.errors), rep.errors

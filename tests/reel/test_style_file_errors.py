"""Bad caption style files are reported by validate as reel errors, not raised at render time."""

import json

from conftest import make_plain_reel
from funkbox.reel import captions
from funkbox.reel.validate import validate


def _copy_templates(tmp_path, monkeypatch):
    """A copy of the real templates folder, so only the test's file differs; returns the copy."""
    import shutil

    d = tmp_path / "templates"
    shutil.copytree(captions.TEMPLATES, d)
    monkeypatch.setattr(captions, "TEMPLATES", d)
    return d


def _style_dir(tmp_path, monkeypatch, name, extra):
    base = json.loads((captions.TEMPLATES / "captions" / "red-box.json").read_text(encoding="utf-8"))
    base.update(extra)
    d = _copy_templates(tmp_path, monkeypatch)
    (d / "captions" / f"{name}.json").write_text(json.dumps(base), encoding="utf-8")


def _reel(demo, style):
    d = make_plain_reel(demo)
    d["shots"][0]["caption"]["style"] = style
    return d


def test_bad_highlight_in_a_caption_style_is_a_reel_error(demo, tmp_path, monkeypatch):
    _style_dir(tmp_path, monkeypatch, "badhl", {"highlight": {"colour": "primary"}})
    rep = validate(_reel(demo, "badhl"), base_dir=demo)
    assert not rep.ok
    assert any("highlight" in e and "colour" in e for e in rep.errors), rep.text()


def test_bad_highlight_radius_in_a_caption_style_is_a_reel_error(demo, tmp_path, monkeypatch):
    _style_dir(tmp_path, monkeypatch, "badrad", {"highlight": {"radius": 0.9}})
    rep = validate(_reel(demo, "badrad"), base_dir=demo)
    assert not rep.ok
    assert any("radius" in e for e in rep.errors), rep.text()


def _endcard_template(tmp_path, monkeypatch, name, drop):
    """brand-split with one style block removed, its slot list unchanged."""

    tpl = json.loads((captions.TEMPLATES / "endcards" / "brand-split.json").read_text(encoding="utf-8"))
    del tpl[drop]
    d = _copy_templates(tmp_path, monkeypatch)
    (d / "endcards" / f"{name}.json").write_text(json.dumps(tpl), encoding="utf-8")


def _endcard_reel(demo, name, slots):
    d = make_plain_reel(demo)
    d["end_card"] = {"template": name, "duration": 2.0, "copy": {s: "hook" for s in slots}}
    return d


def test_slot_without_a_style_block_is_a_reel_error_not_a_crash(demo, tmp_path, monkeypatch):
    _endcard_template(tmp_path, monkeypatch, "nosub", "sub")
    rep = validate(_endcard_reel(demo, "nosub", ["headline", "sub", "url"]), base_dir=demo)
    assert not rep.ok
    assert any("nosub" in e and "'sub'" in e and "style block" in e for e in rep.errors), rep.text()


def test_url_slot_without_a_style_block_is_a_reel_error(demo, tmp_path, monkeypatch):
    _endcard_template(tmp_path, monkeypatch, "nourl", "url")
    rep = validate(_endcard_reel(demo, "nourl", ["headline", "sub", "url"]), base_dir=demo)
    assert not rep.ok
    assert any("nourl" in e and "'url'" in e and "style block" in e for e in rep.errors), rep.text()


def test_a_hook_that_overflows_in_one_format_only_is_an_error_for_that_format(work):
    """A 21-letter word fits the 1080x1920 frame but not a narrow 180x320 one: the error is prefixed with its format."""
    from funkbox.reel.validate import validate as v

    (work / "copy.json").write_text(json.dumps({"lines": {"hook": {"text": "ABCDEFGHIJKLMNOPQRSTU"}}}), "utf-8")
    d = {
        "version": 3,
        "copy": "copy.json",
        "hook": True,
        "formats": [{"id": "narrow", "width": 180, "height": 320}],
        "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": 1.0, "caption": {"copy_id": "hook"}}],
    }
    rep = v(d, base_dir=work)
    assert rep.errors, "the narrow format should fail"
    assert all(e.startswith("[narrow]") for e in rep.errors), rep.text()
    assert any("hook" in e for e in rep.errors), rep.text()

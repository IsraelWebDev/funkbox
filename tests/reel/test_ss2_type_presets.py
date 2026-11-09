"""SS-2 card 2: type presets (templates/types) and the fonts they ship with."""

import copy
import json
from pathlib import Path

import pytest

from funkbox import fonts as fontsmod
from funkbox.reel import captions
from funkbox.reel import schema as S
from funkbox.reel.validate import validate
from funkbox.reel.variants import variant_hash

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "fonts"


def reel(**over):
    """A one-shot reel with a caption on copy line ``hook`` and no style, so a preset can supply one."""
    d = {
        "version": 3,
        "copy": "copy.json",
        "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": 1.0, "caption": {"copy_id": "hook"}}],
    }
    d.update(over)
    return d


def preset(name):
    return json.loads((S.TYPES_DIR / f"{name}.json").read_text(encoding="utf-8"))


SHIPPED = sorted(p.stem for p in S.TYPES_DIR.glob("*.json"))


# --- override precedence -------------------------------------------------------------------------------------------- #
def test_no_type_is_unchanged():
    d = reel()
    before = copy.deepcopy(d)
    r = S.parse_reel(d)
    assert S.apply_type(d, []) is d
    assert d == before
    assert r.preset is None
    assert r.brand.font_caption == "fonts/Oswald-SemiBold.ttf"
    assert r.audio.music_gain_db == -14.0
    assert r.shots[0].caption.style == "red-box"


def test_type_fills_unset_fields_and_leaves_the_input_alone():
    d = reel(type="tech")
    before = copy.deepcopy(d)
    r = S.parse_reel(d)
    assert d == before
    assert r.preset == "tech"
    assert r.brand.font_caption == "fonts/Inter-SemiBold.ttf"
    assert r.brand.font_display == "fonts/SpaceGrotesk-Bold.ttf"
    assert r.brand.font_fallback == "fonts/Rubik-Bold.ttf"
    assert r.shots[0].caption.style == "bold-pop"
    assert r.audio.snap_cuts_to_beats is True  # tech is music-led (spec)


def test_explicit_fields_win_over_the_preset():
    d = reel(type="tech", brand={"font_display": "fonts/DMSerifDisplay-Regular.ttf"}, audio={"music_gain_db": -20})
    d["shots"][0]["caption"]["style"] = "red-box"
    r = S.parse_reel(d)
    assert r.brand.font_display == "fonts/DMSerifDisplay-Regular.ttf"
    assert r.audio.music_gain_db == -20.0
    assert r.brand.font_caption == "fonts/Inter-SemiBold.ttf"  # unset, so the preset fills it
    assert r.shots[0].caption.style == "red-box"


def test_unknown_type_names_the_choices():
    with pytest.raises(S.ReelError, match="no such type 'nope'") as e:
        S.parse_reel(reel(type="nope"))
    assert "explainer" in str(e.value) and "tech" in str(e.value)


def test_a_preset_cannot_set_an_unknown_key(tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text(json.dumps({"font_caption": "fonts/Inter-SemiBold.ttf", "cps": 30}), "utf-8")
    monkeypatch.setattr(S, "TYPES_DIR", tmp_path)
    with pytest.raises(S.ReelError, match="unknown key"):
        S.parse_reel(reel(type="bad"))


# --- shipped presets ------------------------------------------------------------------------------------------------ #
def test_the_starter_types_ship():
    assert {"explainer", "tech"} <= set(SHIPPED)


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_preset_has_known_keys_a_description_and_a_hebrew_line(name):
    p = preset(name)
    assert set(p) <= set(S.TYPE_KEYS) | set(S.TYPE_META_KEYS), set(p)
    assert p["description"] and p["hebrew_test"]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_preset_keeps_a_fallback_that_draws_its_hebrew_line(name):
    p = preset(name)
    assert p["font_fallback"], "every preset keeps brand.font_fallback"
    assert fontsmod.missing(ROOT / p["font_fallback"], p["hebrew_test"]) == []


@pytest.mark.parametrize("name", SHIPPED)
def test_preset_fonts_are_bundled_files(name):
    p = preset(name)
    for k in S.TYPE_BRAND_KEYS:
        assert p[k].startswith("fonts/"), f"{k}: {p[k]} is not a bundled font"
        assert (ROOT / p[k]).is_file(), f"{k}: {p[k]} is missing"


def test_every_bundled_font_ships_its_ofl_licence():
    files = sorted(FONTS.glob("*.ttf"))
    assert files
    for f in files:
        lic = FONTS / f"OFL-{f.name.split('-')[0].lower()}.txt"
        assert lic.is_file(), f"{f.name} has no licence beside it ({lic.name})"
        assert "SIL OPEN FONT LICENSE" in lic.read_text(encoding="utf-8")


def test_tech_fonts_draw_latin_text():
    for f in ("Inter-SemiBold.ttf", "SpaceGrotesk-Bold.ttf"):
        assert fontsmod.missing(FONTS / f, "ABC xyz 0123 !? GLOWMOP.EXAMPLE") == []


# --- fallback reporting --------------------------------------------------------------------------------------------- #
def test_fallback_chars_lists_only_what_the_fallback_draws():
    fb = captions.Fonts(FONTS / "Oswald-SemiBold.ttf", FONTS / "DMSerifDisplay-Regular.ttf", FONTS / "Rubik-Bold.ttf")
    assert captions.fallback_chars(fb, "caption", "Hi") == []
    assert set(captions.fallback_chars(fb, "caption", "שלום")) == set("שלום")
    no_fb = captions.Fonts(FONTS / "Oswald-SemiBold.ttf", FONTS / "DMSerifDisplay-Regular.ttf", None)
    assert captions.fallback_chars(no_fb, "caption", "שלום") == []


def test_validate_reports_a_hebrew_caption_drawn_in_the_fallback(work):
    (work / "copy.json").write_text(json.dumps({"lines": {"hook": {"text": "שלום עולם"}}}), "utf-8")
    rep = validate(reel(type="explainer"), base_dir=work)
    assert not [e for e in rep.errors if "caption" in e]
    assert [w for w in rep.warnings if "shots[0] (s1).caption" in w and "fallback" in w]


def test_validate_is_quiet_when_the_caption_font_draws_the_text(work):
    (work / "copy.json").write_text(json.dumps({"lines": {"hook": {"text": "LOOK CLOSER"}}}), "utf-8")
    rep = validate(reel(type="explainer"), base_dir=work)
    assert rep.deck is not None and "hook" in rep.deck.lines
    assert not [w for w in rep.warnings if "fallback" in w]


def test_null_counts_as_unset():
    r = S.parse_reel(reel(type="tech", brand={"font_caption": None}))
    assert r.brand.font_caption == "fonts/Inter-SemiBold.ttf"


def test_a_broken_preset_is_a_reel_error(tmp_path, monkeypatch):
    (tmp_path / "bad.json").write_text("{not json", "utf-8")
    monkeypatch.setattr(S, "TYPES_DIR", tmp_path)
    with pytest.raises(S.ReelError, match="bad.json"):
        S.parse_reel(reel(type="bad"))


def test_a_changed_preset_changes_the_variant_hash(tmp_path, monkeypatch):
    for f in S.TYPES_DIR.glob("*.json"):
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), "utf-8")
    monkeypatch.setattr(S, "TYPES_DIR", tmp_path)
    before = variant_hash(reel(type="tech"))
    p = json.loads((tmp_path / "tech.json").read_text(encoding="utf-8"))
    p["music_gain_db"] = -16
    (tmp_path / "tech.json").write_text(json.dumps(p), "utf-8")
    assert variant_hash(reel(type="tech")) != before

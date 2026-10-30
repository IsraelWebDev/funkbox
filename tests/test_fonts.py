"""Fonts by name (cached, offline after first use), the glyph check, the fallback font and right-to-left text."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from funkbox import fonts
from funkbox.reel import captions
from funkbox.reel.schema import Brand

ROOT = Path(__file__).resolve().parents[1]
OSWALD = ROOT / "fonts" / "Oswald-SemiBold.ttf"
RUBIK = ROOT / "fonts" / "Rubik-Bold.ttf"
HEBREW = "צבאות ה׳ לישראל"


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A tiny local stand-in for the google/fonts repository, and an empty font cache."""
    fam = tmp_path / "gf" / "ofl" / "testsans"
    fam.mkdir(parents=True)
    shutil.copy(OSWALD, fam / "TestSans-SemiBold.ttf")
    (fam / "OFL.txt").write_text("SIL Open Font License")
    (fam / "METADATA.pb").write_text('name: "Test Sans"\nfonts {\n  name: "Test Sans"\n  style: "normal"\n'
                                      '  weight: 600\n  filename: "TestSans-SemiBold.ttf"\n}\n')
    monkeypatch.setenv("FUNK_FONTS_URL", (tmp_path / "gf").as_uri())
    monkeypatch.setenv("FUNK_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(fonts, "cache_dir", lambda: (tmp_path / "home" / "fonts-cache").mkdir(exist_ok=True)
                        or tmp_path / "home" / "fonts-cache")
    return tmp_path


def test_names_and_paths_are_told_apart():
    assert fonts.is_name("Rubik") and fonts.is_name("Rubik:800") and fonts.is_name("Exo 2:700:italic")
    assert not fonts.is_name("fonts/Oswald-SemiBold.ttf") and not fonts.is_name("Brand.otf")
    assert fonts.parse("Exo 2:700:italic") == ("Exo 2", 700, "italic") and fonts.parse("Rubik") == ("Rubik", 400, "normal")
    with pytest.raises(fonts.FontError, match="expected a file path or a name"):
        fonts.parse("Rubik:bold")


def test_a_name_is_fetched_once_with_its_licence_then_works_offline(mirror):
    p = fonts.fetch("Test Sans:600")
    cache = mirror / "home" / "fonts-cache"
    assert p.parent == cache and p.read_bytes() == OSWALD.read_bytes()
    assert (cache / "TestSans-OFL.txt").is_file()
    assert json.loads((cache / "index.json").read_text())[p.name]["licence"] == "ofl"
    shutil.rmtree(mirror / "gf")                                          # the network is gone
    assert fonts.fetch("Test Sans:600") == p and fonts.resolve("Test Sans:600") == p
    with pytest.raises(fonts.FontError, match="not in Google Fonts"):
        fonts.fetch("Other Font")


def test_a_missing_weight_is_named(mirror):
    with pytest.raises(fonts.FontError, match=r"no weight 700; it has \[600\]"):
        fonts.fetch("Test Sans:700")


def test_missing_glyphs_are_listed_by_name():
    assert fonts.missing(RUBIK, HEBREW + " Army of Hashem!") == []
    miss = fonts.missing(OSWALD, "צהל ok")
    assert miss == ["צ", "ה", "ל"]
    assert "Hebrew Letter Tsadi" in fonts.describe(miss)


def test_the_fallback_draws_what_the_main_font_cannot():
    f = captions.Fonts(OSWALD, OSWALD, RUBIK)
    assert f.path_for("caption", "WE WANT MOSHIACH NOW!") == OSWALD
    assert f.path_for("caption", HEBREW) == RUBIK
    with pytest.raises(captions.GlyphError, match=r"nor the fallback \(Rubik-Bold.ttf\) has '漢' \(U\+6F22"):
        f.path_for("caption", "צהל 漢")
    with pytest.raises(captions.GlyphError, match="has no glyph for 'צ'.*font_fallback"):
        captions.Fonts(OSWALD, OSWALD).path_for("caption", "צהל")


@pytest.mark.parametrize("engine", ["raqm", "basic"])
def test_hebrew_is_drawn_right_to_left(engine, monkeypatch):
    """The lamed rises above the other letters, so the column where ink starts highest is the lamed. In צהל it is
    the last letter, so right to left it is drawn at the LEFT end; in להצ (lamed first) at the right end. With
    Pillow's basic layout (Windows wheels have no libraqm) funkbox reorders the line itself."""
    from PIL import features
    from funkbox import bidi
    if engine == "raqm" and not features.check("raqm"):
        pytest.skip("this Pillow has no libraqm")
    layout = ImageFont.Layout.RAQM if engine == "raqm" else ImageFont.Layout.BASIC
    f = ImageFont.truetype(str(RUBIK), 64, layout_engine=layout)
    monkeypatch.setattr(bidi, "pillow_has_raqm", lambda: engine == "raqm")

    def lamed_side(text):
        im = Image.new("L", (400, 120))
        ImageDraw.Draw(im).text((10, 100), bidi.shown(text), font=f, fill=255, anchor="ls")   # as funkbox draws
        a = np.asarray(im) > 0
        cols = np.nonzero(a.any(0))[0]
        top = np.array([np.argmax(a[:, c]) for c in cols])
        x = cols[np.argmin(top)]
        return "left" if x < (cols.min() + cols.max()) / 2 else "right"
    assert lamed_side("צהל") == "left"
    assert lamed_side("להצ") == "right"


def test_a_hebrew_caption_lays_out_with_the_default_fallback():
    fs = captions.fonts_for(Brand(), ROOT)
    assert fs.fallback == RUBIK
    assert captions.check_caption("top", HEBREW, "red-box", "top", fs, Brand(), 1080, 1920, "reels") is None
    no_fb = captions.Fonts(fs.caption, fs.display)
    err = captions.check_caption("top", HEBREW, "red-box", "top", no_fb, Brand(), 1080, 1920, "reels")
    assert err.startswith("copy 'top': the caption font (Oswald-SemiBold.ttf) has no glyph for 'צ'")


def test_a_hebrew_poster_renders_exactly_and_a_missing_glyph_stops_it():
    from funkbox.stills import render, schema
    d = {"version": 1, "canvas": {"width": 540, "height": 720, "background": "#203040"}, "casual": True,
         "layers": [{"id": "t", "type": "text", "text": HEBREW, "size": 0.07, "x": 0.08, "y": 0.3, "align": "right"}]}
    r = render.render_document(d)
    assert r.audit[0]["text"] == HEBREW and r.audit[0]["text_exact"]
    d["brand"] = {"font_fallback": "fonts/Oswald-SemiBold.ttf"}                # a fallback without Hebrew
    with pytest.raises(schema.PosterError, match="nor the fallback"):
        render.render_document(d)


def test_bundled_fonts_resolve_when_the_data_folder_is_elsewhere(tmp_path, monkeypatch):
    home = tmp_path / "funkbox-data"                                      # like the plugin's ~/funkbox-data
    home.mkdir()
    monkeypatch.setenv("FUNK_HOME", str(home))
    got = fonts.resolve("fonts/Oswald-SemiBold.ttf")
    assert got == ROOT / "fonts" / "Oswald-SemiBold.ttf"
    assert fonts.resolve("fonts/Rubik-Bold.ttf") == RUBIK
    with pytest.raises(FileNotFoundError):
        fonts.resolve("fonts/NoSuchFont.ttf")

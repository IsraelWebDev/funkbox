"""SS-2 follow-up: the beauty / wellness and kids type presets (fonts and licences checked for each)."""

import json
from pathlib import Path

from funkbox.reel import schema as S

ROOT = Path(__file__).resolve().parents[2]


def preset(name):
    return json.loads((ROOT / "templates" / "types" / f"{name}.json").read_text(encoding="utf-8"))


def test_beauty_and_kids_ship():
    assert {"beauty", "kids"} <= set(S.list_types())


def test_beauty_is_calm_serif_display_with_medium_captions():
    p = preset("beauty")
    assert p["font_display"] == "fonts/DMSerifDisplay-Regular.ttf"
    assert p["font_caption"] == "fonts/Rubik-Medium.ttf"
    assert p["shot_target_s"] == 5.0 and p["music_gain_db"] == -16 and p["snap_cuts_to_beats"] is False


def test_kids_is_fredoka_and_music_led_with_snapped_cuts():
    p = preset("kids")
    assert p["font_caption"] == "fonts/Fredoka-SemiBold.ttf" and p["font_display"] == "fonts/Fredoka-Bold.ttf"
    assert p["snap_cuts_to_beats"] is True


def test_fredoka_ships_with_its_ofl_licence():
    for f in ("Fredoka-SemiBold.ttf", "Fredoka-Bold.ttf", "Rubik-Medium.ttf"):
        assert (ROOT / "fonts" / f).is_file(), f
    text = (ROOT / "fonts" / "OFL-fredoka.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in text and "Fredoka" in text

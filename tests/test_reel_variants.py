"""Reel variants, card 1: expand and validate vary keys into variant reels. No rendering."""

import copy
import json
from pathlib import Path

import pytest

from funkbox.reel.variants import ReelVariantError, expand_reel, variant_hash
from funkbox.stills.variations import MAX_VARIANTS


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A project folder with the copy deck the reel points at. Paths must sit inside FUNK_INPUTS (the fence)."""
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    deck = {"brand_name": "TEST", "lines": {"hook": {"text": "LOOK CLOSER."}, "fresh": {"text": "FRESH."}}}
    (tmp_path / "copy.json").write_text(json.dumps(deck), encoding="utf-8")
    return tmp_path


def make_reel() -> dict:
    """Self-contained: colour sources only, no voiceover, no music, so validation needs no media files."""
    return {
        "version": 3,
        "settings": {"width": 1080, "height": 1920, "fps": 30},
        "brand": {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff"},
        "copy": "copy.json",
        "audio": {"voiceover": False},
        "shots": [
            {
                "id": "s1",
                "source": {"image": "shot.png"},
                "motion": {"type": "none"},
                "caption": {"copy_id": "hook", "style": "red-box"},
                "duration": 2,
            },
            {"id": "s2", "source": {"color": "#222222"}, "motion": {"type": "none"}, "duration": 2},
        ],
    }


def expand(vary: dict, project: Path, reel: dict | None = None):
    return expand_reel(reel or make_reel(), vary, base_dir=project, deep=False)


def test_key_outside_allowed_fields_is_rejected(project):
    with pytest.raises(ReelVariantError, match="not a variable field"):
        expand({"layers.bg.palette": ["a", "b"]}, project)


def test_literal_text_key_is_rejected(project):
    with pytest.raises(ReelVariantError, match="literal text"):
        expand({"captions.s1.text": ["a", "b"]}, project)


def test_copy_deck_key_is_rejected(project):
    with pytest.raises(ReelVariantError, match="copy deck"):
        expand({"shots.s1.copy_id": ["hook", "fresh"]}, project)


def test_caption_copy_id_is_an_allowed_key(project):
    try:
        expand({"captions.s1.copy_id": ["hook", "fresh"]}, project)
    except ReelVariantError as e:
        assert "vary key" not in str(e)


def test_unequal_lists_are_rejected(project):
    with pytest.raises(ReelVariantError, match="same length"):
        expand({"brand.palette": ["#111111,#222222"], "audio.music": ["a.music.json", "b.music.json"]}, project)


def test_more_than_the_limit_is_rejected(project):
    assert MAX_VARIANTS == 12
    with pytest.raises(ReelVariantError, match="at most 12"):
        expand({"shots.s1.motion": ["none"] * 13}, project)


def test_the_limit_itself_is_accepted(project):
    variants, labels = expand({"shots.s1.motion": ["none"] * 12}, project)
    assert len(variants) == 12
    assert len(labels) == 12


def test_palette_and_motion_expand_per_variant(project):
    variants, _ = expand(
        {
            "brand.palette": ["#111111,#222222", "#333333,#444444"],
            "shots.s1.motion": ["kenburns", "none"],
        },
        project,
    )
    assert variants[0]["brand"]["primary"] == "#111111"
    assert variants[0]["brand"]["secondary"] == "#222222"
    assert variants[1]["brand"]["primary"] == "#333333"
    assert variants[0]["shots"][0]["motion"]["type"] == "kenburns"
    assert variants[1]["shots"][0]["motion"]["type"] == "none"


def test_bad_palette_is_rejected(project):
    with pytest.raises(ReelVariantError, match="brand.palette"):
        expand({"brand.palette": ["red,blue"]}, project)


def test_source_kind_follows_the_file_extension(project):
    variants, _ = expand({"shots.s1.source": ["inputs/shots/a.png", "inputs/shots/b.mp4"]}, project)
    assert variants[0]["shots"][0]["source"] == {"image": "inputs/shots/a.png"}
    assert variants[1]["shots"][0]["source"] == {"video": "inputs/shots/b.mp4"}


def test_path_outside_the_project_is_rejected(project):
    with pytest.raises(ReelVariantError, match="outside the project"):
        expand({"shots.s1.source": ["../secret.png"]}, project)


def test_one_invalid_variant_stops_the_run_and_names_it(project):
    with pytest.raises(ReelVariantError) as info:
        expand({"shots.s1.motion": ["kenburns", "nosuchmotion"]}, project)
    message = str(info.value)
    assert "variant 2" in message
    assert "variant 1" not in message


def test_hash_is_stable_and_changes_with_a_value(project):
    a, _ = expand({"brand.palette": ["#111111,#222222"]}, project)
    b, _ = expand({"brand.palette": ["#111111,#222222"]}, project)
    c, _ = expand({"brand.palette": ["#111111,#333333"]}, project)
    assert variant_hash(a[0]) == variant_hash(b[0])
    assert variant_hash(a[0]) != variant_hash(c[0])


def test_input_reel_is_not_mutated(project):
    reel = make_reel()
    before = copy.deepcopy(reel)
    expand({"brand.palette": ["#111111,#222222"], "shots.s1.motion": ["none"]}, project, reel)
    assert reel == before

def test_shot_ids_that_look_like_copy_are_not_rejected(project):
    reel = make_reel()
    reel["shots"][0]["id"] = "copy_1"
    variants, _ = expand_reel(reel, {"shots.copy_1.motion": [{"type": "none"}, {"type": "kenburns"}]}, base_dir=project, deep=False)
    assert [v["shots"][0]["motion"]["type"] for v in variants] == ["none", "kenburns"]
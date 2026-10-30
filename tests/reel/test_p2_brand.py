import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import synth
from funkbox.reel import brand, ingest, project as P


@pytest.fixture
def penv(work, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(work / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(work / "inbox"))
    return work


def make(penv, label=synth.LABEL_RED, body=(60, 66, 78), name="demo"):
    P.new_project(name)
    synth.bottle(label=label, body=body).save(P.inbox_dir() / "product.png")
    ingest.ingest(name)
    return name


def test_primary_is_the_red_label(penv):
    make(penv)
    b = brand.brand_from_assets("demo")
    assert brand.delta_e(b["primary"], synth.LABEL_RED) < 10, b
    assert brand.contrast(b["text"], b["primary"]) >= 4.5
    assert b["secondary"] != b["primary"]
    # secondary is the darkest sizeable cluster (the cap / dark body)
    assert brand.luminance(brand.hex_to_rgb(b["secondary"])) < 0.1
    assert Image.open(b["swatch"]).size == (900, 420)
    m = P.load_meta("demo")
    assert m["brand"]["primary"] == b["primary"] and m["status"] == "branded"
    assert b["font_caption"] == "fonts/Oswald-SemiBold.ttf" and b["font_display"] == "fonts/DMSerifDisplay-Regular.ttf"


def test_kmeans_is_seeded_and_deterministic(penv):
    make(penv)
    a = brand.brand_from_assets("demo")
    b = brand.brand_from_assets("demo")
    assert (a["primary"], a["secondary"], a["palette"]) == (b["primary"], b["secondary"], b["palette"])


def test_light_primary_gets_dark_text_or_is_adjusted(penv):
    """A pale yellow label: white text has contrast < 4.5, so dark text wins (no adjustment needed)."""
    make(penv, label=(250, 220, 60), name="yellow")
    b = brand.brand_from_assets("yellow")
    assert b["text"] == "#111111" and brand.contrast(b["text"], b["primary"]) >= 4.5
    assert brand.delta_e(b["primary"], (250, 220, 60)) < 10


def test_mid_tone_primary_is_adjusted_until_contrast_holds(penv):
    # luminance ~0.19: neither white (4.35) nor #111 (4.34) reaches 4.5 on the raw colour
    make(penv, label=(205, 80, 70), name="orange")
    b = brand.brand_from_assets("orange")
    assert brand.contrast(b["text"], b["primary"]) >= 4.5
    assert any("darkened" in n or "lightened" in n for n in b["notes"]), b["notes"]


def test_contrast_math():
    assert brand.contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert brand.contrast("#777777", "#777777") == pytest.approx(1.0)


def test_user_fonts_win_and_set_brand_overrides(penv):
    make(penv)
    import shutil
    from funkbox.config import ROOT as FUNKBOX
    fd = P.project_dir("demo") / "brand" / "fonts"
    shutil.copy(FUNKBOX / "fonts" / "Oswald-SemiBold.ttf", fd / "Brandsans.ttf")
    shutil.copy(FUNKBOX / "fonts" / "DMSerifDisplay-Regular.ttf", fd / "Brand-Display.ttf")
    b = brand.brand_from_assets("demo")
    assert b["font_caption"] == "brand/fonts/Brandsans.ttf" and b["font_display"] == "brand/fonts/Brand-Display.ttf"
    b = brand.set_brand("demo", primary="#0a7d3c", text="#ffffff")
    assert b["primary"] == "#0a7d3c" and b["contrast"] >= 4.5
    assert P.load_meta("demo")["brand"]["primary"] == "#0a7d3c"
    with pytest.raises(P.ProjectError):
        brand.set_brand("demo", primary="red")
    with pytest.raises(P.ProjectError):
        brand.set_brand("demo", font_caption="brand/fonts/missing.ttf")
    with pytest.raises(P.ProjectError):
        brand.set_brand("demo", font_caption="../../../etc/passwd")
    b = brand.set_brand("demo", font_fallback="fonts/Rubik-Bold.ttf")    # a bundled fallback, by path
    assert b["font_fallback"] == "fonts/Rubik-Bold.ttf"
    with pytest.raises(P.ProjectError, match="font name like"):
        brand.set_brand("demo", font_display="brand/fonts/logo.png")
    with pytest.raises(P.ProjectError, match="font name like 'Rubik:800'"):
        brand.set_brand("demo", font_caption="Rubik:bold")
    b = brand.set_brand("demo", primary="#ffee00")                      # white-on-yellow: warned, not blocked
    assert any("warning" in n for n in b["notes"])


def test_no_assets_is_a_clear_error(penv):
    P.new_project("empty")
    with pytest.raises(P.ProjectError, match="ingest"):
        brand.brand_from_assets("empty")

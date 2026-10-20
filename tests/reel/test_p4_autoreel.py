import json
from pathlib import Path

import pytest
from PIL import Image

import synth
from reel import approval, autoreel, captions, collision, project as P
from reel.schema import ReelError
from reel.validate import validate


@pytest.fixture
def penv(work, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(work / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(work / "inbox"))
    return work


def layout_boxes(res):
    """(caption box, product box) per shot, recomputed independently from the generated reel."""
    d = P.project_dir(res.slug)
    reel, deck = res.report.reel, res.report.deck
    from funkgl.recipe import resolve_path
    fonts = captions.Fonts(resolve_path(reel.brand.font_caption, d), resolve_path(reel.brand.font_display, d))
    out = []
    W, H = reel.settings.width, reel.settings.height
    for sh in reel.shots:
        if sh.caption and sh.product:
            lay = captions.layout_block(sh.caption.copy_id, deck.text(sh.caption.copy_id),
                                        captions.load_style(sh.caption.style), fonts, reel.brand, W, H,
                                        anchor=sh.caption.anchor, safe_area="reels")
            asp = collision.image_aspect(resolve_path(sh.product.image, d))
            out.append((sh.id, collision.caption_box(lay),
                        collision.product_box(sh.product.x, sh.product.y, sh.product.height, asp, W, H)))
    return out


def test_synthetic_project_gives_a_valid_20s_reel(penv):
    synth.make_project()
    res = autoreel.auto_reel("demo")
    assert res.report.ok, res.report.text()
    reel, rep = res.report.reel, res.report
    total = rep.info["total_seconds"]
    assert 18.0 <= total <= 22.0, total
    shots = reel.shots
    # product in >= 50% of shots, always in the hook shot and the end card
    with_prod = [s for s in shots if s.product]
    assert len(with_prod) / len(shots) >= 0.5 and shots[0].product and reel.end_card.product
    # hook first (the first copy line), body lines follow in order, none repeated
    caps = [s.caption.copy_id for s in shots if s.caption]
    assert caps[0] == "hook" and caps[1:] == ["tackle", "care", "fresh"]
    assert shots[0].caption.copy_id == "hook" and shots[0].caption.reveal == "typewriter"
    # CTA lines are in the end card
    assert reel.end_card.copy == {"headline": "end_head", "sub": "end_sub", "url": "url"}
    assert reel.end_card.duration == 4.0 and reel.end_card.headline_reveal == "typewriter"
    # clips <= 40% of shots
    n_clip = sum(1 for s in shots if s.source.kind == "video")
    assert 1 <= n_clip <= 0.4 * len(shots)
    # no caption/product overlap > 8% of the caption area (recomputed from layout boxes)
    boxes = layout_boxes(res)
    assert boxes
    for sid, cb, pb in boxes:
        assert collision.overlap_fraction(cb, pb) <= 0.08, sid
    # every shot references copy by id only; VO on (stub voice), music from music/
    assert reel.audio.voiceover and reel.audio.music == "music/track.mp3" and reel.audio.snap_cuts_to_beats
    assert all(s.vo == s.caption.copy_id for s in shots if s.caption)
    # placements alternate bottom-centre / right-third / left-third
    xs = [round(s.product.x, 1) for s in with_prod[:3]]
    assert xs == [0.5, 0.7, 0.3]
    # reasons for motion are recorded; no funk2 filler (4 real visuals)
    assert res.stats["filler"] == 0 and not (P.project_dir("demo") / "funk2").exists()
    assert P.load_meta("demo")["status"] == "reel-built"


def test_auto_reel_is_deterministic_and_editable(penv):
    synth.make_project()
    a = autoreel.auto_reel("demo").path.read_text()
    b = autoreel.auto_reel("demo").path.read_text()
    assert a == b
    assert json.loads(a)["copy"] == "copy.json"               # relative: the reel lives in the project folder


def test_filler_funk2_is_used_and_reported_when_fewer_than_4_visuals(penv, gl):
    synth.make_project(scenes=1, clips=1)
    res = autoreel.auto_reel("demo")
    assert res.report.ok, res.report.text()
    assert res.stats["filler"] == 2
    assert any("funk2 filler" in n and "brand colours" in n for n in res.notes), res.notes
    kinds = [s.source.kind for s in res.report.reel.shots]
    assert "funk2" in kinds
    d = P.project_dir("demo")
    assert (d / "funk2" / "filler-1.json").exists()
    assert "funk2 filler" in res.text()
    # the filler recipe has no text block and renders in brand colours (pixels are not grey)
    rec = json.loads((d / "funk2" / "filler-1.json").read_text())
    assert "text" not in rec
    from reel import render
    plan = render.prepare(str(res.path))
    sid = next(s.id for s in plan.reel.shots if s.source.kind == "funk2")
    r = render.Renderer(plan.reel, plan.deck, plan.spans, width=135, height=240, depth_mode="off")
    try:
        import numpy as np
        fr = r.still(sid, 0.3)
    finally:
        r.release()
    Image.fromarray(fr).save(penv / "filler.png")
    px = fr.reshape(-1, 3).astype(float)
    assert (px.max(1) - px.min(1)).mean() > 15                  # coloured, not grey


def test_no_filler_for_real_assets_and_clip_cap(penv):
    synth.make_project(scenes=2, clips=6)          # 8 visuals -> no filler; 6 clips but only 40% may be clips
    res = autoreel.auto_reel("demo")
    assert res.stats["filler"] == 0
    shots = res.report.reel.shots
    assert sum(1 for s in shots if s.source.kind == "video") <= 0.4 * len(shots)
    assert any("at most 40%" in n for n in res.notes)


def test_calm_pace_uses_longer_shots_and_crossfades(penv):
    synth.make_project()
    res = autoreel.auto_reel("demo", pace="calm", length_s=20)
    shots = res.report.reel.shots
    assert 6 <= len(shots) <= 9
    assert all(s.transition_in == "crossfade" for s in shots[1:])
    assert 18 <= res.report.info["total_seconds"] <= 22
    fast = autoreel.auto_reel("demo", pace="fast")
    assert len(fast.report.reel.shots) > len(shots)


def test_copy_must_be_approved(penv):
    synth.make_project(approve=False)
    with pytest.raises(ReelError, match="needs approved copy"):
        autoreel.auto_reel("demo")
    r = approval.propose_copy("demo", synth.COPY_LINES)
    with pytest.raises(ReelError, match="needs approved copy"):
        autoreel.auto_reel("demo")                             # a draft is not enough
    approval.approve("demo", r["draft_sha256"], "chat")
    assert autoreel.auto_reel("demo").report.ok
    cj = P.project_dir("demo") / "copy.json"
    cj.write_text(cj.read_text(encoding="utf-8").replace("CLEAN", "CLEAR"), encoding="utf-8")
    with pytest.raises(ReelError, match="changed since approval"):
        autoreel.auto_reel("demo")


def test_missing_end_lines_error_names_what_to_add(penv):
    synth.make_project(lines={"hook": {"text": "HELLO THERE."}, "end_head": {"text": "Big Sale"}})
    with pytest.raises(ReelError) as e:
        autoreel.auto_reel("demo")
    assert "end_sub" in str(e.value) and "propose_copy" in str(e.value)


def test_no_end_lines_means_no_end_card(penv):
    synth.make_project(lines={"hook": {"text": "HELLO THERE."}, "b1": {"text": "ONE MORE LINE."}})
    res = autoreel.auto_reel("demo", length_s=14)
    assert res.report.reel.end_card is None and res.report.ok
    assert 12 <= res.report.info["total_seconds"] <= 16


def test_cta_ids_override(penv):
    synth.make_project()
    res = autoreel.auto_reel("demo", cta_ids=["care", "fresh", "url"])
    assert res.report.reel.end_card.copy == {"headline": "care", "sub": "fresh", "url": "url"}
    caps = [s.caption.copy_id for s in res.report.reel.shots if s.caption]
    assert "care" not in caps and "fresh" not in caps
    with pytest.raises(ReelError, match="not in the approved copy"):
        autoreel.auto_reel("demo", cta_ids=["nope"])


def test_no_product_still_builds(penv):
    synth.make_project(product=False)
    res = autoreel.auto_reel("demo")
    assert res.report.ok and not any(s.product for s in res.report.reel.shots)
    assert any("no product" in n for n in res.notes)


# ---- the collision rule -----------------------------------------------------
def collide_reel(caption_anchor="bottom", product_y=0.8, product_h=0.38):
    return {"version": 3, "settings": {"width": 1080, "height": 1920, "fps": 30},
            "brand": {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff"}, "copy": "copy.json",
            "shots": [{"id": "s1", "source": {"color": "#202020"}, "duration": 1.5,
                       "product": {"image": "product/product.png", "x": 0.5, "y": product_y, "height": product_h},
                       "caption": {"copy_id": "tackle", "style": "red-box", "reveal": "none",
                                   "anchor": caption_anchor}}]}


def test_validator_rejects_caption_product_overlap_over_8_percent(penv):
    synth.make_project()
    d = P.project_dir("demo")
    bad = d / "bad.reel.json"
    bad.write_text(json.dumps(collide_reel("bottom")))
    rep = validate(str(bad))
    assert not rep.ok and "overlaps the product" in rep.text() and "limit 8%" in rep.text()
    ok = d / "ok.reel.json"
    ok.write_text(json.dumps(collide_reel("top")))
    assert validate(str(ok)).ok, validate(str(ok)).text()


def test_overlap_fraction_math():
    assert collision.overlap_fraction((0, 0, 10, 10), (5, 5, 20, 20)) == pytest.approx(0.25)
    assert collision.overlap_fraction((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0
    assert collision.overlap_fraction((0, 0, 10, 10), (-5, -5, 50, 50)) == 1.0


def test_resolver_flips_anchor_then_shrinks_then_errors(penv):
    synth.make_project()
    d = P.project_dir("demo")
    deck = autoreel.copydeck.load_copy(d / "copy.json")
    from funkgl.recipe import resolve_path
    fonts = captions.Fonts(resolve_path("fonts/Oswald-SemiBold.ttf", d), resolve_path("fonts/DMSerifDisplay-Regular.ttf", d))
    from reel.schema import Brand
    asp = collision.image_aspect(d / "product" / "product.png")
    # 1. bottom collides with a low product -> flips to top
    sh = {"id": "s1", "caption": {"copy_id": "tackle", "style": "red-box", "reveal": "none", "anchor": "bottom"},
          "product": {"x": 0.5, "y": 0.78, "height": 0.38}}
    autoreel._resolve_collision(sh, deck, "red-box", fonts, Brand(), asp)
    assert sh["caption"]["anchor"] == "top" and sh["product"]["height"] == 0.38
    # 2. a tall product collides at both anchors -> shrunk once to 0.85x, then fits
    sh = {"id": "s2", "caption": {"copy_id": "tackle", "style": "red-box", "reveal": "none", "anchor": "top"},
          "product": {"x": 0.5, "y": 0.86, "height": 0.60}}
    autoreel._resolve_collision(sh, deck, "red-box", fonts, Brand(), asp)
    assert sh["product"]["height"] == pytest.approx(0.51, abs=1e-3)
    # 3. still colliding after one shrink -> error
    sh = {"id": "s3", "caption": {"copy_id": "tackle", "style": "red-box", "reveal": "none", "anchor": "top"},
          "product": {"x": 0.5, "y": 0.9, "height": 0.9}}
    with pytest.raises(ReelError, match="collides with the product"):
        autoreel._resolve_collision(sh, deck, "red-box", fonts, Brand(), asp)


def test_storyboard_of_the_auto_reel(penv, gl):
    synth.make_project()
    res = autoreel.auto_reel("demo")
    from reel import render
    from reel.storyboard import storyboard
    plan = render.prepare(str(res.path))
    sb = storyboard(plan, P.project_dir("demo") / "out" / "demo-storyboard.png", tile_w=200)
    assert len(plan.spans) == len(res.report.reel.shots) + 1 and sb.width >= 4 * 200

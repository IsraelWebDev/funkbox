import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

import synth
from funkbox.reel import ingest, project as P


@pytest.fixture
def penv(work, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(work / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(work / "inbox"))
    return work


def kinds(res):
    return {Path(i.src).name: (i.kind, i.dest) for i in res.items}


def test_new_project_layout_and_slug_rules(penv):
    m = P.new_project("Glow Mop 2")
    assert m["slug"] == "glow-mop-2"
    d = P.project_dir("glow-mop-2")
    for sub in ("product", "brand", "brand/fonts", "shots", "clips", "music", "out"):
        assert (d / sub).is_dir()
    assert json.loads((d / "project.json").read_text())["status"] == "new"
    assert P.inbox_dir().is_dir()                        # inbox is created on first run
    with pytest.raises(P.ProjectError):
        P.new_project("Glow Mop 2")
    for bad in ("../x", "A B", "", "x" * 60, "-a"):
        with pytest.raises(P.ProjectError):
            P.project_dir(bad)
    assert [p["slug"] for p in P.list_projects()] == ["glow-mop-2"]


def test_projects_root_is_an_allowed_path_root(penv, monkeypatch):
    from funkbox.config import allowed_roots
    assert P.projects_root() in allowed_roots()


def test_classification_of_a_mixed_inbox(penv):
    P.new_project("demo")
    synth.mixed_folder(P.inbox_dir())
    res = ingest.ingest("demo")
    k = kinds(res)
    assert k["track.mp3"] == ("music", "music/track.mp3")
    assert k["sting.wav"][0] == "music"
    assert k["move.mp4"] == ("clip", "clips/move.mp4")
    assert k["logo.png"] == ("logo", "brand/logo.png")
    assert [k[f"scene{i}.jpg"][0] for i in (1, 2, 3)] == ["shot"] * 3
    # the alpha png outranks the white-background jpg; the jpg is kept as an alternative, not a shot
    assert k["cutout.png"][0] == "product" and k["cutout.png"][1] == "product/product.png"
    assert k["shelf-photo.jpg"][1].startswith("product/alt/")
    d = P.project_dir("demo")
    assert (d / "product" / "product.png").exists() and list((d / "product").glob("original.*"))
    assert res.product["status"] == "set" and res.product["source"] == "alpha"
    assert res.counts["music"] == 2 and res.counts["clip"] == 1 and res.counts["shot"] == 3
    # inbox files were moved
    assert not list(P.inbox_dir().glob("*"))
    assert res.contact_sheet and Image.open(res.contact_sheet).width > 400
    assert P.load_meta("demo")["status"] == "ingested"


def test_white_background_product_alone_is_found_and_cut_out(penv):
    P.new_project("demo")
    synth.bottle(bg=(255, 255, 255)).save(P.inbox_dir() / "IMG_0001.jpg", quality=95)
    synth.scene(P.inbox_dir() / "beach.jpg", 3)
    res = ingest.ingest("demo")
    assert res.product["status"] == "set" and res.product["source"] == "border"
    q = res.product["cutout"]
    assert q["method"] == "cutout" and q["score"] > 0.85 and q["ok"], q
    assert q["touches_edge"] is False
    assert "cut-out score" in res.text()
    out = Image.open(P.project_dir("demo") / "product" / "product.png")
    assert out.mode == "RGBA" and out.height > 500
    assert kinds(res)["beach.jpg"][0] == "shot"


def test_bad_cutout_is_reported_with_the_gimp_route(penv):
    """A product photographed on a busy background cannot be cut out: the tool says so."""
    P.new_project("demo")
    synth.scene(P.inbox_dir() / "product.jpg", 4)
    res = ingest.ingest("demo")
    q = res.product["cutout"]
    assert res.product["status"] == "set" and q["ok"] is False
    assert "GIMP" in q["advice"] and "GIMP" in res.text()


def test_duplicates_are_skipped_and_reingest_is_idempotent(penv):
    P.new_project("demo")
    synth.scene(P.inbox_dir() / "a.jpg", 1)
    shutil.copy(P.inbox_dir() / "a.jpg", P.inbox_dir() / "a-copy.jpg")
    res = ingest.ingest("demo")
    assert res.counts["shot"] == 1 and res.counts["duplicates skipped"] == 1
    # same bytes dropped again later: skipped against the project's index
    synth.scene(P.inbox_dir() / "again.jpg", 1)
    res2 = ingest.ingest("demo")
    assert res2.counts["shot"] == 0 and "again.jpg" in res2.duplicates
    assert (P.inbox_dir() / "again.jpg").exists()                      # duplicates are never deleted
    assert len(list((P.project_dir("demo") / "shots").glob("*.jpg"))) == 1


def test_external_folder_is_copied_not_moved(penv):
    P.new_project("demo")
    ext = synth.mixed_folder(penv / "somewhere_else")
    before = sorted(p.name for p in ext.iterdir())
    res = ingest.ingest("demo", source=str(ext))
    assert res.moved is False
    assert sorted(p.name for p in ext.iterdir()) == before                  # untouched
    assert (P.project_dir("demo") / "clips" / "move.mp4").exists()
    # even with move=True an external source is only copied
    P.new_project("demo2")
    res = ingest.ingest("demo2", source=str(ext), move=True)
    assert res.moved is False and sorted(p.name for p in ext.iterdir()) == before
    assert any("copied" in w for w in res.warnings)


def test_ambiguous_product_returns_candidates_and_set_product_resolves(penv):
    P.new_project("demo")
    synth.bottle().save(P.inbox_dir() / "product-front.png")
    synth.bottle(label=(20, 160, 60)).save(P.inbox_dir() / "product-back.png")
    synth.scene(P.inbox_dir() / "s.jpg", 2)
    res = ingest.ingest("demo")
    assert res.product["status"] == "ambiguous"
    assert sorted(Path(c).name for c in res.product["candidates"]) == ["product-back.png", "product-front.png"]
    assert not (P.project_dir("demo") / "product" / "product.png").exists()      # no guessing
    assert "set_product" in res.text()
    prod = ingest.set_product("demo", "product-back.png")
    assert (P.project_dir("demo") / "product" / "product.png").exists()
    assert prod["source"] == "manual" and prod["cutout"]["score"] >= 0.85
    with pytest.raises(P.ProjectError):
        ingest.set_product("demo", "nope.png")
    with pytest.raises(P.ProjectError):
        ingest.set_product("demo", "../../etc/passwd")


def test_unsupported_left_in_place_exif_and_downscale(penv):
    P.new_project("demo")
    (P.inbox_dir() / "notes.txt").write_text("hi")
    (P.inbox_dir() / "brand.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    synth.scene(penv / "tmp_scene.png", 5)
    im = Image.open(penv / "tmp_scene.png").resize((3000, 1500))
    ex = Image.Exif()
    ex[0x0112] = 6                                                     # rotate 90 cw
    im.save(P.inbox_dir() / "big.jpg", exif=ex)
    res = ingest.ingest("demo")
    assert (P.inbox_dir() / "notes.txt").exists()
    assert [Path(f).name for f, _ in res.unsupported] == ["notes.txt"]
    assert any("SVG" in w and "PNG" in w for w in res.warnings)
    out = Image.open(P.project_dir("demo") / "shots" / "big.jpg")
    assert out.size == (1200, 2400)                                    # rotated, long side 2400
    assert (P.project_dir("demo") / "shots" / "_orig" / "big.jpg").exists()


def test_ingest_cannot_write_outside_the_project_layout(penv):
    P.new_project("demo")
    (P.inbox_dir() / "copy.json").write_text('{"lines": {"x": {"text": "evil"}}}')
    res = ingest.ingest("demo")
    assert not (P.project_dir("demo") / "copy.json").exists()
    assert [Path(f).name for f, _ in res.unsupported] == ["copy.json"]

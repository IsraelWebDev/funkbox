"""Reel variants, card 2: the render loop. Each variant renders, is audited, and writes a sidecar and a manifest."""

import json
from types import SimpleNamespace

import pytest

from funkbox.reel import approval
from funkbox.reel import project as P
from funkbox.reel import render
from funkbox.reel.variants import ReelVariantError, variant_hash
from funkbox.reel.variants_render import render_variants


def plain_reel(demo):
    """The demo reel with voiceover and music off and 3 s shots (a caption needs room to finish typing)."""
    d = json.loads((demo / "demo.reel.json").read_text(encoding="utf-8"))
    d["audio"] = {"voiceover": False}
    for sh in d["shots"]:
        sh["duration"] = 3.0
        sh.pop("vo", None)
    return d


WARM = "#f6f7f4,#0f7c6e"
DARK = "#0f1216,#4fd1b9"


def long_reel(base):
    """The plain demo reel with 3 s shots: a caption needs room to finish typing, or the audit rightly fails it."""
    reel = plain_reel(base)
    for sh in reel["shots"]:
        sh["duration"] = 3.0
    return reel


def run(demo, out, name="demo", vary=None, **kw):
    vary = vary if vary is not None else {"brand.palette": [WARM, DARK]}
    kw = {"draft": True, "with_audio": False, **kw}
    manifest = render_variants(long_reel(demo), vary, name, base_dir=demo, out_dir=out, **kw)
    return manifest, out / f"{name}-variants"


def test_each_variant_writes_video_audit_sidecar_and_manifest(demo, work):
    manifest, vdir = run(demo, work)
    assert manifest["count"] == 2
    for i, palette in ((1, WARM), (2, DARK)):
        stem = f"v{i:02d}"
        assert list(vdir.glob(f"{stem}*.mp4")), f"no video for {stem}"
        assert list(vdir.glob(f"{stem}*.text_audit.json")), f"no audit for {stem}"
        sidecar = json.loads((vdir / f"{stem}.reel.json").read_text(encoding="utf-8"))
        assert sidecar["brand"]["primary"] == palette.split(",")[0]
    on_disk = json.loads((vdir / "variants.json").read_text(encoding="utf-8"))
    assert on_disk["manifest_sha256"] == manifest["manifest_sha256"]
    for entry in manifest["variants"]:
        sidecar = json.loads((vdir / entry["reel"]).read_text(encoding="utf-8"))
        assert variant_hash(sidecar) == entry["reel_sha256"]
        # Without audio muxing the silent video is still a listed output, not a missing one.
        assert any(name.endswith(".mp4") for name in entry["outputs"]), entry["outputs"]


def test_every_variant_audit_is_clean_against_the_copy_deck(demo, work):
    manifest, vdir = run(demo, work)
    audits = sorted(vdir.glob("*.text_audit.json"))
    assert len(audits) >= 2
    decks = set()
    for path in audits:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["ok"] is True, path.name
        assert doc["missing"] == [] and doc["inexact"] == [], path.name
        decks.add(doc["copy_deck"])
    assert len(decks) == 1, "every variant must audit against the same copy deck"
    assert all(entry["audit_ok"] for entry in manifest["variants"])


def test_same_inputs_give_the_same_manifest_hash(demo, work):
    a, _ = run(demo, work / "a")
    b, _ = run(demo, work / "b")
    assert a["manifest_sha256"] == b["manifest_sha256"]
    assert [e["reel_sha256"] for e in a["variants"]] == [e["reel_sha256"] for e in b["variants"]]


def test_the_manifest_hash_records_what_kind_of_render_it_was(demo, work):
    draft, _ = run(demo, work / "draft", draft=True)
    full, _ = run(demo, work / "full", draft=False)
    assert draft["manifest_sha256"] != full["manifest_sha256"]


def test_one_invalid_variant_stops_the_run_before_any_render(demo, work):
    vary = {"brand.palette": [WARM, "not-a-palette"]}
    with pytest.raises(ReelVariantError, match="brand.palette"):
        render_variants(long_reel(demo), vary, "bad", base_dir=demo, out_dir=work, draft=True, with_audio=False)
    assert not (work / "bad-variants").exists(), "no output folder may be created for an invalid run"


def test_a_failed_audit_is_raised_after_the_manifest_is_written(demo, work, monkeypatch):
    def fake_render_formats(src, stem, *, out_dir, **kw):
        mp4 = out_dir / f"{stem}.mp4"
        audit = out_dir / f"{stem}.text_audit.json"
        mp4.write_bytes(b"")
        audit.write_text("{}", encoding="utf-8")
        return [SimpleNamespace(paths={"mp4": str(mp4), "text_audit": str(audit)}, audit_ok=(stem != "v02"))]

    monkeypatch.setattr(render, "render_formats", fake_render_formats)
    with pytest.raises(ReelVariantError, match="variant 2: text audit failed"):
        run(demo, work)
    manifest = json.loads((work / "demo-variants" / "variants.json").read_text(encoding="utf-8"))
    assert [e["audit_ok"] for e in manifest["variants"]] == [True, False]


def test_editing_the_approved_copy_refuses_the_whole_run(work, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(work / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(work / "inbox"))
    P.new_project("lockdemo")
    draft = approval.propose_copy("lockdemo", {"hook": {"text": "LOOK CLOSER."}})
    approval.approve("lockdemo", draft["draft_sha256"], "chat")
    d = P.project_dir("lockdemo")
    reel = {
        "version": 3,
        "settings": {"width": 270, "height": 480, "fps": 10},
        "brand": {"primary": "#c8102e", "secondary": "#1a1a1a", "text": "#ffffff"},
        "copy": "copy.json",
        "shots": [
            {
                "id": "s1",
                "source": {"color": "#202020"},
                "duration": 1.0,
                "caption": {"copy_id": "hook", "style": "red-box", "reveal": "none"},
            }
        ],
    }
    cj = d / "copy.json"
    cj.write_text(cj.read_text(encoding="utf-8").replace("LOOK CLOSER.", "LOOK CLOSER!"), encoding="utf-8")
    with pytest.raises(ReelVariantError, match="changed since approval"):
        render_variants(
            reel,
            {"brand.palette": [WARM, DARK]},
            "lock",
            base_dir=d,
            out_dir=work / "out",
            draft=True,
            with_audio=False,
        )
    assert not (work / "out").exists(), "nothing may be created when the copy lock refuses the run"

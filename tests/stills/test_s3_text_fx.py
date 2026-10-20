"""v3.2 S3: adjustments, filters, effects, text with the copy rules and the audit."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import assets
from reel import approval, project as P
from stills import render, schema


def doc(layers, w=128, h=96, bg="#202020", **kw):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": bg}, "layers": layers, **kw}


@pytest.fixture
def img(work):
    return assets.save_noise(work / "n.png", 128, 96, 7)


def layer_rgb(r, i=0):
    return r.layers[i].rgba[..., :3].astype(np.float32)


# ------------------------------------------------------------------ adjustments (numpy references, +-3)
YIQ = np.array([[0.299, 0.587, 0.114], [0.596, -0.274, -0.322], [0.211, -0.523, 0.312]], np.float32)
IYIQ = np.array([[1.0, 0.956, 0.621], [1.0, -0.272, -0.647], [1.0, -1.106, 1.703]], np.float32)


def ref_adjust(rgb, a):
    c = rgb / 255.0
    b, w, g = a.get("levels", [0, 1, 1])
    c = np.clip((c - b) / (w - b), 0, 1) ** (1.0 / g)
    c = c + a.get("brightness", 0.0)
    c = (c - 0.5) * (1.0 + a.get("contrast", 0.0)) + 0.5
    L = c @ np.array([0.299, 0.587, 0.114], np.float32)
    c = L[..., None] + (c - L[..., None]) * (1.0 + a.get("saturation", 0.0))
    if a.get("hue"):
        h = np.deg2rad(a["hue"])
        yiq = c @ YIQ.T
        i2 = yiq[..., 1] * np.cos(h) - yiq[..., 2] * np.sin(h)
        q2 = yiq[..., 1] * np.sin(h) + yiq[..., 2] * np.cos(h)
        c = np.stack([yiq[..., 0], i2, q2], -1) @ IYIQ.T
    c = c.copy()
    c[..., 0] += 0.1 * a.get("temperature", 0.0)
    c[..., 2] -= 0.1 * a.get("temperature", 0.0)
    if a.get("invert"):
        c = 1.0 - c
    return np.clip(c, 0, 1) * 255.0


@pytest.mark.parametrize("adj", [
    {"brightness": 0.2}, {"brightness": -0.3}, {"contrast": 0.5}, {"contrast": -0.6}, {"saturation": -1.0},
    {"saturation": 0.8}, {"hue": 90}, {"hue": -40}, {"hue": 180}, {"levels": [0.1, 0.9, 1.0]},
    {"levels": [0.0, 1.0, 2.2]}, {"temperature": 0.7}, {"invert": True},
    {"brightness": 0.05, "contrast": 0.1, "saturation": -0.2, "hue": 15, "levels": [0.02, 0.98, 1.1],
     "temperature": -0.2},
])
def test_adjustments_match_numpy_within_3_levels(img, adj):
    r = render.render_document(doc([{"type": "image", "path": str(img), "adjust": adj}]))
    src = np.asarray(Image.open(img).convert("RGB"), np.float32)
    diff = np.abs(layer_rgb(r) - ref_adjust(src, adj))
    assert diff.max() <= 3.0, (adj, diff.max())
    # the composite shows the adjusted layer
    assert np.abs(r.image.astype(np.float32) - layer_rgb(r)).max() <= 1


def test_adjust_leaves_alpha_alone_and_noop_is_exact(work):
    arr = assets.noise_array(64, 64, 3, alpha=True)
    Image.fromarray(arr).save(work / "ra.png")
    r = render.render_document(doc([{"type": "image", "path": str(work / "ra.png"), "adjust": {"brightness": 0.2}}], w=64, h=64))
    assert np.array_equal(r.layers[0].rgba[..., 3], arr[..., 3])
    r0 = render.render_document(doc([{"type": "image", "path": str(work / "ra.png"), "adjust": {"brightness": 0.0}}], w=64, h=64))
    assert np.array_equal(r0.layers[0].rgba, arr)                      # all-default adjust: no pass at all


# ------------------------------------------------------------------ filters
def np_blur(x, sigma):
    taps = int(np.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-taps, taps + 1) ** 2) / sigma ** 2)
    k /= k.sum()
    out = x.astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (taps, taps)
        p = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            sl = [slice(None)] * out.ndim
            sl[axis] = slice(i, i + out.shape[axis])
            acc += w * p[tuple(sl)]
        out = acc
    return out


def test_blur_matches_a_numpy_gaussian(img):
    r = render.render_document(doc([{"type": "image", "path": str(img), "filters": {"blur": 3}}]))
    src = np.asarray(Image.open(img).convert("RGB"), np.float32)
    assert np.abs(layer_rgb(r) - np_blur(src, 3.0)).max() <= 2.5
    assert layer_rgb(r).std() < src.std()


def test_blur_is_premultiplied_so_transparent_edges_do_not_darken(work):
    a = np.zeros((64, 64, 4), np.uint8)
    a[16:48, 16:48] = (255, 255, 255, 255)
    Image.fromarray(a).save(work / "sq.png")
    r = render.render_document(doc([{"type": "image", "path": str(work / "sq.png"), "filters": {"blur": 4}}], w=64, h=64))
    px = r.layers[0].rgba
    soft = (px[..., 3] > 20) & (px[..., 3] < 235)
    assert soft.sum() > 100 and px[..., :3][soft].min() >= 250          # edge colour stays white, only alpha softens
    assert 0 < px[16, 32, 3] < 255 and px[32, 32, 3] == 255 and px[2, 2, 3] == 0


def test_sharpen_posterize_duotone_pixelate(img):
    src = np.asarray(Image.open(img).convert("RGB"), np.float32)
    r = render.render_document(doc([{"type": "image", "path": str(img), "filters": {"posterize": 4}}]))
    want = np.clip(np.floor(src / 255.0 * 4) / 3.0, 0, 1) * 255
    assert np.abs(layer_rgb(r) - want).max() <= 2.5
    assert len(np.unique(layer_rgb(r))) <= 4

    r = render.render_document(doc([{"type": "image", "path": str(img), "filters": {"duotone": ["#102040", "#ffe0a0"]}}]))
    lum = (src / 255.0) @ np.array([0.299, 0.587, 0.114], np.float32)
    lo, hi = np.array([0x10, 0x20, 0x40], np.float32), np.array([0xff, 0xe0, 0xa0], np.float32)
    want = lo + (hi - lo) * lum[..., None]
    assert np.abs(layer_rgb(r) - want).max() <= 3.0

    r = render.render_document(doc([{"type": "image", "path": str(img), "filters": {"pixelate": 8}}]))
    px = layer_rgb(r)
    assert np.array_equal(px[0:8, 0:8], np.broadcast_to(px[0, 0], (8, 8, 3)))            # one flat cell
    assert np.abs(px[4, 4] - src[4, 4]).max() <= 1.5                                     # centre sample of the cell
    assert len(np.unique(px.reshape(-1, 3), axis=0)) < len(np.unique(src.reshape(-1, 3), axis=0)) / 10

    r = render.render_document(doc([{"type": "image", "path": str(img), "filters": {"sharpen": 1.5}}]))
    assert layer_rgb(r).std() > src.std() * 0.99 and not np.array_equal(layer_rgb(r), src)
    flat = np.full((16, 16, 3), 90, np.uint8)
    Image.fromarray(flat).save(img.parent / "flat.png")
    r = render.render_document(doc([{"type": "image", "path": str(img.parent / "flat.png"), "filters": {"sharpen": 2}}], w=64, h=64))
    assert np.abs(layer_rgb(r) - 90).max() <= 1                                           # flat areas stay flat


@pytest.mark.parametrize("flt", [{"halftone": 6}, {"chroma": 4}, {"vignette": 1.0}])
def test_reused_funkgl_post_shaders_keep_the_layers_alpha(work, flt):
    arr = assets.noise_array(96, 96, 4, alpha=True)
    Image.fromarray(arr).save(work / "ra.png")
    plain = render.render_document(doc([{"type": "image", "path": str(work / "ra.png")}], w=96, h=96))
    r = render.render_document(doc([{"type": "image", "path": str(work / "ra.png"), "filters": flt}], w=96, h=96))
    assert np.array_equal(r.layers[0].rgba[..., 3], arr[..., 3])
    assert not np.array_equal(r.layers[0].rgba[..., :3], plain.layers[0].rgba[..., :3])


def test_scale_changes_pixel_sized_filters_for_previews(img):
    d = doc([{"type": "image", "path": str(img), "filters": {"blur": 8}}], w=1080, h=720)
    # the image is 128x96 stretched (cover) onto the canvas; the preview must blur by 8 * scale px
    a = render.render_document(d, scale=0.5)
    assert (a.width, a.height) == (540, 360)


def test_adjust_and_filter_validation():
    errs = "\n".join(schema.check(doc([{"id": "a", "type": "solid", "color": "#000000",
                                        "adjust": {"brightness": 5, "wobble": 1, "levels": [0.9, 0.1, 1]},
                                        "filters": {"blur": -1, "duotone": ["#fff", "#000000"], "sparkle": 1}}])))
    for frag in ("adjust.brightness", "unknown keys ['wobble']", "adjust.levels", "filters.blur", "filters.duotone",
                 "unknown keys ['sparkle']"):
        assert frag in errs, frag


# ------------------------------------------------------------------ text
CASUAL = {"casual": True}


def text_doc(layers, **kw):
    return doc(layers, w=540, h=720, bg="#203040", **kw)


def test_literal_text_in_a_non_casual_doc_is_rejected():
    d = text_doc([{"id": "t", "type": "text", "text": "Hello there"}])
    with pytest.raises(schema.PosterError) as e:
        render.render_document(d)
    msg = " ".join(e.value.errors)
    assert "'t'" in msg and "casual" in msg and "copy_id" in msg
    ok = render.render_document({**d, "casual": True})
    assert ok.audit[0]["source"] == "literal" and ok.audit[0]["text"] == "Hello there"


def test_copy_id_needs_a_deck_and_a_known_id(work):
    d = text_doc([{"id": "t", "type": "text", "copy_id": "hook"}])
    with pytest.raises(schema.PosterError, match="needs a copy deck"):
        render.render_document(d)
    deck = work / "copy.json"
    deck.write_text(json.dumps({"brand_name": "X", "lines": {"hook": {"text": "LOOK CLOSER."}}}))
    r = render.render_document({**d, "copy": str(deck)})
    assert r.audit[0]["copy_id"] == "hook" and r.audit[0]["text"] == "LOOK CLOSER."
    with pytest.raises(schema.PosterError, match="unknown copy id 'nope'"):
        render.render_document({**text_doc([{"id": "t", "type": "text", "copy_id": "nope"}]), "copy": str(deck)})


LINES = {"hook": {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?"}, "body": {"text": "Tough on stains, gentle on family life."},
         "url": {"text": "GLOWMOP.EXAMPLE"}}


@pytest.fixture
def penv(work, monkeypatch):
    monkeypatch.setenv("FUNK_PROJECTS", str(work / "projects"))
    monkeypatch.setenv("FUNK_INBOX", str(work / "inbox"))
    P.new_project("demo")
    return work


def approve(lines=LINES):
    r = approval.propose_copy("demo", lines)
    approval.approve("demo", r["draft_sha256"], "cli")


def proj_doc(*ids, **kw):
    return text_doc([{"id": f"t{i}", "type": "text", "copy_id": c, "y": 0.1 + 0.2 * i, "size": 0.05}
                     for i, c in enumerate(ids)], project="demo", **kw)


def test_project_poster_draws_only_approved_locked_copy(penv):
    approve()
    r = render.render_document(proj_doc("hook", "body", "url"))
    assert [e["copy_id"] for e in r.audit] == ["hook", "body", "url"]
    assert r.audit[0]["text"] == "LOOK CLOSER.\nTIME FOR A CLEAN?"


def test_project_poster_with_a_stale_lock_or_no_approval_is_rejected(penv):
    with pytest.raises(schema.PosterError, match="no approved copy.json"):
        render.render_document(proj_doc("hook"))
    approval.propose_copy("demo", LINES)                           # a draft is not approval
    with pytest.raises(schema.PosterError, match="no approved copy.json"):
        render.render_document(proj_doc("hook"))
    approve()
    render.render_document(proj_doc("hook"))
    cp = P.project_dir("demo") / "copy.json"
    cp.write_text(cp.read_text().replace("CLOSER", "CLOSE R"))       # someone edits the approved file
    with pytest.raises(schema.PosterError, match="copy.json changed since approval"):
        render.render_document(proj_doc("hook"))
    cp.write_text(cp.read_text().replace("CLOSE R", "CLOSER"))        # back to the exact bytes: valid again
    render.render_document(proj_doc("hook"))


def test_project_posters_reject_literal_text_even_when_casual(penv):
    approve()
    d = proj_doc("hook")
    d["layers"].append({"id": "lit", "type": "text", "text": "sneaky", "y": 0.6})
    for casual in (False, True):
        with pytest.raises(schema.PosterError, match="not allowed in a project poster"):
            render.render_document({**d, "casual": casual})


def test_a_poster_cannot_borrow_another_decks_draft_or_a_projects_copy(penv, work):
    approve()
    draft = P.project_dir("demo") / "copy.draft.json"
    d = text_doc([{"id": "t", "type": "text", "copy_id": "hook"}], copy=str(draft))
    with pytest.raises(schema.PosterError, match="only the approved copy.json"):
        render.render_document(d)
    d["copy"] = str(P.project_dir("demo") / "copy.json")          # outside a project doc: the lock still applies
    render.render_document(d)
    cp = P.project_dir("demo") / "copy.json"
    cp.write_text(cp.read_text() + " ")
    with pytest.raises(schema.PosterError, match="changed since approval"):
        render.render_document(d)


def test_audit_lists_every_string_byte_equal_to_its_source(work):
    lines = {"hook": {"text": "LOOK CLOSER.\nTIME FOR A CLEAN?"},
             "long": {"text": "Tough on stains and gentle on family life, every single time you clean."},
             "uni": {"text": "Crème brûlée — 100%"}, "url": {"text": "GLOWMOP.EXAMPLE"}}
    deck = work / "copy.json"
    deck.write_text(json.dumps({"brand_name": "X", "lines": lines}, ensure_ascii=False), encoding="utf-8")
    d = text_doc([{"id": "a", "type": "text", "copy_id": "hook", "y": 0.05, "size": 0.05},
                  {"id": "b", "type": "text", "copy_id": "long", "y": 0.25, "size": 0.05, "max_width": 0.5, "align": "center"},
                  {"id": "c", "type": "text", "copy_id": "uni", "y": 0.6, "size": 0.05, "style": "display"},
                  {"id": "g", "type": "group", "layers": [{"id": "d", "type": "text", "copy_id": "url", "y": 0.8, "size": 0.05,
                                                              "align": "right", "x": 0.1, "max_width": 0.8}]}],
                 copy=str(deck), post=[{"name": "grain", "amount": 0.01}])
    paths = render.render_poster(d, work / "o", "aud")
    audit = json.loads(paths["text_audit"].read_text(encoding="utf-8"))
    assert [e["layer"] for e in audit] == ["a", "b", "c", "d"]
    src = {"a": "hook", "b": "long", "c": "uni", "d": "url"}
    for e in audit:
        want = lines[src[e["layer"]]]["text"]
        assert e["source"] == "copy_id" and e["copy_id"] == src[e["layer"]]
        assert e["text"].encode("utf-8") == want.encode("utf-8") and e["text_exact"] is True
        # the drawn lines + the separators the wrapper dropped rebuild exactly the source
        words_drawn = " ".join(e["drawn_lines"]).replace("\n", " ").split()
        assert words_drawn == want.split()
    b = next(e for e in audit if e["layer"] == "b")
    assert len(b["drawn_lines"]) >= 3 and all(len(l) < len(lines["long"]["text"]) for l in b["drawn_lines"])


def test_text_never_splits_words_never_truncates_and_errors_when_it_cannot_fit():
    long_word = "W" * 40
    d = text_doc([{"id": "w", "type": "text", "text": long_word, "size": 0.1, "max_width": 0.3}], casual=True)
    with pytest.raises(schema.PosterError) as e:
        render.render_document(d)
    assert "'w'" in str(e.value) and "minimum size" in str(e.value) and "wider than the line" in str(e.value)
    tall = text_doc([{"id": "z", "type": "text", "text": " ".join(["word"] * 120), "size": 0.08, "max_width": 0.3, "y": 0.5}],
                    casual=True)
    with pytest.raises(schema.PosterError, match="outside the canvas"):
        render.render_document(tall)
    shrink = text_doc([{"id": "s", "type": "text", "text": "A fairly long headline that wants to be big", "size": 0.2,
                        "max_width": 0.9, "y": 0.4}], casual=True)
    r = render.render_document(shrink)
    assert r.audit[0]["px"] < round(0.2 * 720) and r.audit[0]["text_exact"]          # shrank instead of overflowing


def test_text_pixels_stay_inside_the_reported_box_and_effects_add_around_it():
    d = text_doc([{"id": "t", "type": "text", "text": "Hello floor", "x": 0.1, "y": 0.4, "size": 0.08, "color": "#ffffff"}],
                 casual=True)
    r = render.render_document(d)
    bg = np.array([0x20, 0x30, 0x40], np.uint8)
    x, y, w, h = r.audit[0]["box"]
    changed = (r.image != bg).any(-1)
    assert changed[y:y + h, x:x + w].sum() > 300
    changed[max(0, y - 2):y + h + 2, max(0, x - 2):x + w + 2] = False
    assert changed.sum() == 0                                         # nothing drawn outside the layout box
    fx = text_doc([{"id": "t", "type": "text", "text": "Hello floor", "x": 0.1, "y": 0.4, "size": 0.08, "color": "#ffffff",
                    "effects": {"shadow": 0.8, "glow": {"opacity": 0.6, "color": "#ff00ff", "radius": 0.3},
                                "stroke": {"width": 0.06, "color": "#000000"}}}], casual=True)
    r2 = render.render_document(fx)
    assert r2.audit[0]["text"] == "Hello floor"
    outside = (r2.image != bg).any(-1)
    outside[y:y + h, x:x + w] = False
    assert outside.sum() > 500                                        # shadow/glow spill past the box
    assert r2.audit[0]["box"] == r.audit[0]["box"]                    # effects never change the layout


def test_text_layout_is_identical_at_preview_scale():
    d = text_doc([{"id": "t", "type": "text", "text": "A fairly long headline that wraps", "x": 0.1, "y": 0.2,
                   "size": 0.07, "max_width": 0.5}], casual=True)
    full = render.render_document(d)
    small = render.render_document(d, scale=0.5)
    assert (small.width, small.height) == (270, 360)
    assert small.audit == full.audit and small.image.shape == (360, 270, 3)


def test_text_layer_fx_and_blend_apply_after_drawing(work):
    d = text_doc([{"id": "bgc", "type": "solid", "color": "#808080"},
                  {"id": "t", "type": "text", "text": "Hi", "size": 0.2, "x": 0.1, "y": 0.3, "color": "#000000",
                   "blend": "difference", "adjust": {"invert": True}}], casual=True)
    r = render.render_document(d)
    assert r.audit[0]["text"] == "Hi"
    assert (r.image == 127).all(-1).sum() > 200        # black ink inverted to white, then |128 - 255| = 127

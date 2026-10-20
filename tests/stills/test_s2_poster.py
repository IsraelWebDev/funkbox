"""v3.2 S2: poster core (canvas, layer types, blend, opacity, masks, product, shader layers, groups)."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import assets
from reel import product as RP
from stills import raster, render, schema


def doc(layers, w=128, h=96, bg="#202020", **kw):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": bg}, "layers": layers, **kw}


def rgb01(a):
    return a.astype(np.float32) / 255.0


def ref_blend(mode, b, s):
    if mode == "normal":
        return s
    if mode == "multiply":
        return b * s
    if mode == "screen":
        return 1 - (1 - b) * (1 - s)
    if mode == "add":
        return np.minimum(b + s, 1)
    if mode == "difference":
        return np.abs(b - s)
    raise AssertionError(mode)


@pytest.fixture
def imgs(work):
    a = assets.save_noise(work / "a.png", 128, 96, 1)
    b = assets.save_noise(work / "b.png", 128, 96, 2, alpha=True)
    return work, a, b


# ------------------------------------------------------------------ schema
def test_presets_and_custom_canvas():
    assert schema.PRESETS == {"square": (1080, 1080), "insta-portrait": (1080, 1350), "story": (1080, 1920),
                              "landscape": (1920, 1080), "twitter": (1600, 900), "a4-150dpi": (1240, 1754)}
    for name, (w, h) in schema.PRESETS.items():
        d = schema.parse({"canvas": {"preset": name}, "layers": [{"type": "solid", "color": "#000000"}]})
        assert (d.width, d.height) == (w, h)
    d = schema.parse(doc([{"type": "solid", "color": "#fff"[:0] or "#ffffff"}], w=4096, h=4096))
    assert (d.width, d.height) == (4096, 4096)
    assert schema.parse(doc([{"type": "solid", "color": "#ffffff"}], w=101, h=99)).width == 100
    assert any("4096" in e for e in schema.check(doc([{"type": "solid", "color": "#ffffff"}], w=4097)))


def test_schema_reports_every_problem_with_the_layer_named():
    errs = schema.check({"canvas": {"preset": "nope"}, "colour": 1,
                         "layers": [{"id": "a", "type": "image", "blend": "dodge", "opacity": 2, "paht": "x"},
                                    {"id": "a", "type": "solid", "color": "red"},
                                    {"type": "wobble"},
                                    {"id": "t", "type": "text", "copy_id": "hook", "text": "both"}]})
    joined = "\n".join(errs)
    for frag in ("canvas.preset 'nope'", "unknown top-level keys ['colour']", "unknown blend 'dodge'",
                 "opacity must be 0..1", "unknown keys ['paht']", "image layer needs `path`", "duplicate layer id 'a'",
                 "solid layer needs `color`", "unknown layer type 'wobble'", "exactly one of `copy_id`"):
        assert frag in joined, frag
    with pytest.raises(schema.PosterError) as e:
        schema.parse({"layers": []})
    assert len(e.value.errors) >= 2


def test_ids_are_filled_and_unique():
    d = schema.parse(doc([{"type": "solid", "color": "#000000"}, {"type": "solid", "color": "#111111"},
                          {"type": "group", "layers": [{"type": "solid", "color": "#222222"}]}]))
    ids = [l["id"] for l in schema.all_layers(d.layers)]
    assert len(ids) == len(set(ids)) == 4


# ------------------------------------------------------------------ blend correctness (numpy references)
@pytest.mark.parametrize("mode", ["normal", "multiply", "screen", "add", "difference"])
@pytest.mark.parametrize("opacity", [1.0, 0.6])
def test_blend_modes_match_numpy_within_2_levels(imgs, mode, opacity):
    work, a, b = imgs
    r = render.render_document(doc([{"id": "base", "type": "image", "path": str(a)},
                                    {"id": "top", "type": "image", "path": str(b), "blend": mode, "opacity": opacity}]))
    A = np.asarray(Image.open(a).convert("RGB"))
    B = np.asarray(Image.open(b).convert("RGBA"))
    bb, ss, sa = rgb01(A), rgb01(B[..., :3]), rgb01(B[..., 3:4])
    expected = bb * (1 - opacity * sa) + ref_blend(mode, bb, ss) * (opacity * sa)
    diff = np.abs(r.image.astype(np.float32) - expected * 255.0)
    assert diff.max() <= 2.0, (mode, opacity, diff.max())
    assert (diff > 1.0).mean() < 0.01


def test_every_blend_mode_is_funkgls_own(imgs, gl):
    """Same pixels as funkgl's Engine blending two image layers: the compositor reuses blend.glsl."""
    from funkgl.engine import Engine
    from funkgl.recipe import BLEND_MODES, Recipe2
    work, a, b = imgs
    opaque = assets.save_noise(work / "c.png", 128, 96, 3)
    eng = Engine(128, 96)
    try:
        for mode in BLEND_MODES:
            rec = Recipe2.from_dict({"version": 2, "settings": {"width": 128, "height": 96, "seed": 1},
                                     "layers": [{"type": "image", "path": str(a), "fit": "stretch"},
                                                {"type": "image", "path": str(opaque), "fit": "stretch",
                                                 "blend": mode, "opacity": 0.7}]})
            want = eng.render(rec, 0)
            got = render.render_document(doc([{"type": "image", "path": str(a), "fit": "stretch"},
                                              {"type": "image", "path": str(opaque), "fit": "stretch",
                                               "blend": mode, "opacity": 0.7}])).image
            assert np.abs(got.astype(int) - want.astype(int)).max() <= 1, mode
    finally:
        eng.release()


def test_background_and_solid_and_hidden(imgs):
    work, a, b = imgs
    r = render.render_document(doc([{"id": "hide", "type": "solid", "color": "#ff0000", "visible": False}], bg="#336699"))
    assert (r.image == np.array([0x33, 0x66, 0x99], np.uint8)).all() and r.layers == []
    r = render.render_document(doc([{"type": "solid", "color": "#ff000080"}], bg="#000000"))
    assert abs(int(r.image[0, 0, 0]) - 128) <= 1 and r.image[0, 0, 1] == 0


# ------------------------------------------------------------------ masks
def test_radial_mask_centre_is_more_opaque_than_the_edge(imgs):
    work, a, b = imgs
    r = render.render_document(doc([{"id": "m", "type": "solid", "color": "#ffffff",
                                     "mask": {"type": "radial", "feather": 0.35}}], w=200, h=200))
    al = r.layers[0].rgba[..., 3]
    assert al[100, 100] == 255 and al[0, 0] == 0 and al[100, 0] <= 2 and al[100, 100] > al[100, 5]
    assert al[100, 100] > al[100, 195] and np.all(np.diff(al[100, 100:].astype(int)) <= 0)     # monotone fall-off
    inv = render.render_document(doc([{"id": "m", "type": "solid", "color": "#ffffff",
                                       "mask": {"type": "radial", "feather": 0.35, "invert": True}}], w=200, h=200))
    assert inv.layers[0].rgba[100, 100, 3] == 0 and inv.layers[0].rgba[0, 0, 3] == 255


def test_linear_and_image_masks(imgs):
    work, a, b = imgs
    r = render.render_document(doc([{"type": "solid", "color": "#ffffff",
                                     "mask": {"type": "linear", "angle": 90, "position": 0.5, "feather": 0.2}}]))
    al = r.layers[0].rgba[..., 3]
    assert al[0, 10] == 255 and al[-1, 10] == 0 and 100 < al[48, 10] < 160           # opaque top, clear bottom
    r = render.render_document(doc([{"type": "solid", "color": "#ffffff",
                                     "mask": {"type": "linear", "angle": 0, "position": 0.5, "feather": 0.0}}]))
    al = r.layers[0].rgba[..., 3]
    assert al[10, 5] == 255 and al[10, 120] == 0
    g = np.tile(np.linspace(0, 255, 128).astype(np.uint8), (96, 1))
    Image.fromarray(g).save(work / "m.png")
    r = render.render_document(doc([{"type": "solid", "color": "#ffffff", "mask": {"type": "image", "path": str(work / "m.png")}}]))
    al = r.layers[0].rgba[..., 3].astype(int)
    assert np.abs(al - g.astype(int)).max() <= 1


# ------------------------------------------------------------------ image / gradient / group
def test_image_fit_modes_and_placement(work):
    arr = np.zeros((40, 80, 3), np.uint8)
    arr[..., 0] = 255
    Image.fromarray(arr).save(work / "wide.png")
    p = str(work / "wide.png")
    cover = render.render_document(doc([{"type": "image", "path": p, "fit": "cover"}], w=100, h=100)).layers[0].rgba
    assert cover[..., 3].min() == 255
    contain = render.render_document(doc([{"type": "image", "path": p, "fit": "contain"}], w=100, h=100)).layers[0].rgba
    assert contain[0, 50, 3] == 0 and contain[50, 50, 3] == 255 and contain[99, 50, 3] == 0
    assert (contain[..., 3] == 255).sum() == 100 * 50
    small = render.render_document(doc([{"type": "image", "path": p, "fit": "contain", "scale": 0.5, "x": 0.25, "y": 0.25}],
                                       w=100, h=100)).layers[0].rgba
    ys, xs = np.nonzero(small[..., 3])
    assert xs.min() >= 0 and xs.max() < 50 and ys.max() < 50
    rot = render.render_document(doc([{"type": "image", "path": p, "fit": "contain", "rotate_deg": 90}], w=100, h=100)).layers[0].rgba
    ys, xs = np.nonzero(rot[..., 3] > 128)
    assert (ys.max() - ys.min()) > (xs.max() - xs.min())                                  # now taller than wide
    stretch = render.render_document(doc([{"type": "image", "path": p, "fit": "stretch"}], w=100, h=100)).layers[0].rgba
    assert stretch[..., 3].min() == 255


def test_image_at_canvas_size_is_not_resampled(imgs):
    work, a, b = imgs
    r = render.render_document(doc([{"type": "image", "path": str(a)}]))
    assert np.array_equal(r.image, np.asarray(Image.open(a).convert("RGB")))


def test_gradients(imgs):
    r = render.render_document(doc([{"id": "g", "type": "gradient", "stops": ["#00000000", "#000000cc"], "angle": 90}],
                                   w=64, h=200))
    al = r.layers[0].rgba[..., 3].astype(int)
    assert al[0].max() <= 2 and abs(al[-1].min() - 0xcc) <= 2 and np.all(np.diff(al.mean(axis=1)) >= -0.5)
    r = render.render_document(doc([{"type": "gradient", "stops": ["#ff0000", "#0000ff"], "angle": 0}], w=200, h=64))
    px = r.layers[0].rgba
    assert px[10, 0, 0] > 250 and px[10, 199, 2] > 250 and abs(int(px[10, 100, 0]) - 127) <= 3
    r = render.render_document(doc([{"type": "gradient", "kind": "radial", "stops": [{"pos": 0, "color": "#ffffff"},
                                                                                     {"pos": 1, "color": "#000000"}]}],
                                   w=100, h=100))
    px = r.layers[0].rgba
    assert px[50, 50, 0] > 240 and px[0, 0, 0] < 5
    # premultiplied interpolation: a transparent stop does not tint the middle
    r = render.render_document(doc([{"type": "gradient", "stops": ["#ff000000", "#0000ffff"], "angle": 0}], w=100, h=64))
    mid = r.layers[0].rgba[32, 50]
    assert mid[0] < 8 and mid[2] > 240


def test_groups_nest_blend_and_opacity(imgs):
    work, a, b = imgs
    child = {"type": "image", "path": str(b), "blend": "multiply"}
    flat = render.render_document(doc([{"type": "image", "path": str(a)}, {**child, "opacity": 0.5}])).image
    grp = render.render_document(doc([{"type": "image", "path": str(a)},
                                      {"type": "group", "opacity": 0.5, "blend": "normal", "layers": [child]}])).image
    # group = child multiplied over a TRANSPARENT backdrop (isolated), so it is plain `normal` inside
    iso = render.render_document(doc([{"type": "image", "path": str(a)}, {**{"type": "image", "path": str(b)}, "opacity": 0.5}])).image
    assert np.abs(grp.astype(int) - iso.astype(int)).max() <= 2
    assert not np.array_equal(flat, grp)
    grp2 = render.render_document(doc([{"type": "image", "path": str(a)},
                                       {"type": "group", "blend": "multiply", "opacity": 0.5,
                                        "layers": [{"type": "image", "path": str(b)}]}])).image
    assert np.abs(grp2.astype(int) - flat.astype(int)).max() <= 2           # group blend/opacity == the layer's own


# ------------------------------------------------------------------ product (reel R3 equivalence)
def prod_doc(work, **over):
    make = assets.make_bottle(work / "bottle.png")
    lay = {"id": "bottle", "type": "product", "image": str(make), "x": 0.5, "y": 0.9, "height": 700 / 960,
           "shadow": 0.0, "match_color": 0.0}
    lay.update(over)
    return doc([lay], w=540, h=960, bg="#303a48")


def test_product_pixels_are_untouched_without_colour_match(work, gl):
    r = render.render_document(prod_doc(work))
    src = np.asarray(Image.open(work / "bottle.png").convert("RGBA"))
    x0 = 270 - 150
    y0 = round(0.9 * 960 - 700)
    region = r.image[y0:y0 + 700, x0:x0 + 300]
    opaque = src[..., 3] == 255
    assert opaque.sum() > 50000
    assert np.array_equal(region[opaque], src[..., :3][opaque])                 # byte-identical label pixels
    layer = r.layers[0].rgba[y0:y0 + 700, x0:x0 + 300]
    assert np.array_equal(layer[..., 3][opaque], src[..., 3][opaque])


def test_product_colour_match_is_one_invertible_transform(work, gl):
    d = schema.parse(prod_doc(work, match_color=0.6))
    ctx = render.make_context(d, None, 540, 960, 1.0)
    backdrop = np.zeros((960, 540, 3), np.uint8)
    backdrop[:] = (48, 58, 72)
    backdrop[:, :200] = (120, 90, 60)
    rgba, info = raster.raster_product(d.layers[0], ctx, backdrop)
    tr = info["transform"]
    assert tr is not None and not tr.identity
    src = np.asarray(Image.open(work / "bottle.png").convert("RGBA"))
    x0, y0, w, h = info["placement"]
    region = rgba[y0:y0 + h, x0:x0 + w]
    opaque = src[..., 3] == 255
    raw_diff = np.abs(region[..., :3].astype(int) - src[..., :3].astype(int)).max(axis=-1)[opaque]
    assert raw_diff.mean() > 1.0                                                  # the match really changed pixels
    inv = RP.invert_transform(region[..., :3], tr)
    d3 = np.abs(inv - src[..., :3].astype(np.float32)).max(axis=-1)
    assert (d3[opaque] <= 3).mean() >= 0.99                                       # ...and it is undone by the inverse


def test_product_layer_equals_reels_compositor(work, gl):
    """Shadow + sprite as a normal-blended layer reproduces reel's frame compositing."""
    from reel.motion import Camera
    from reel.schema import ProductSpec
    dd = prod_doc(work, shadow=0.7, height=0.5)
    r = render.render_document(dd)
    spec = ProductSpec(image=str(work / "bottle.png"), x=0.5, y=0.9, height=0.5, depth=0.5, shadow=0.7, match_color=0.0)
    pc = RP.ProductCompositor(work / "bottle.png", spec, 540, 960)
    bg = np.empty((960, 540, 3), np.uint8)
    bg[:] = (0x30, 0x3a, 0x48)
    want = pc.draw(bg, Camera(mode="kenburns", zoom=1.0))
    assert np.abs(r.image.astype(int) - want.astype(int)).max() <= 2


def test_product_effects_only_add_pixels_behind(work, gl):
    plain = render.render_document(prod_doc(work, shadow=0.0))
    fx = render.render_document(prod_doc(work, shadow=0.0, effects={"glow": 0.8, "stroke": {"opacity": 1, "width": 0.01, "color": "#ff00ff"}}))
    src = np.asarray(Image.open(work / "bottle.png").convert("RGBA"))
    x0, y0 = 120, round(0.9 * 960 - 700)
    opaque = src[..., 3] == 255
    assert np.array_equal(fx.image[y0:y0 + 700, x0:x0 + 300][opaque], src[..., :3][opaque])
    assert np.abs(fx.image.astype(int) - plain.image.astype(int)).max() > 20      # but something was added around it


# ------------------------------------------------------------------ shader layers, determinism, sidecar
def test_shader_layer_is_deterministic_and_the_sidecar_reproduces_it(work, gl):
    d = doc([{"id": "bg", "type": "shader", "shader": "shaders/fbm_flow.frag", "t": 0.3, "palette": "vapor"},
             {"id": "g", "type": "gradient", "stops": ["#00000000", "#000000cc"]}], w=200, h=160, seed=5,
            post=[{"name": "grain", "amount": 0.02}])
    p1 = render.render_poster(copy.deepcopy(d), work, "one")
    p2 = render.render_poster(copy.deepcopy(d), work, "two")
    a, b = Image.open(p1["png"]), Image.open(p2["png"])
    assert np.array_equal(np.asarray(a), np.asarray(b)) and a.size == (200, 160)
    side = json.loads(p1["sidecar"].read_text())
    sl = side["layers"][0]
    assert "shader" not in sl and "effect" in sl["source"] and len(sl["source_sha256"]) == 64
    assert sl["seed"] == 5 and isinstance(sl["params"], dict) and sl["params"]
    assert side["layers"][1]["id"] == "g"
    again = render.render_poster(side, work, "three")
    assert np.array_equal(np.asarray(Image.open(again["png"])), np.asarray(a))
    # a different seed changes the sampled params and therefore the picture
    d2 = copy.deepcopy(d)
    d2["seed"] = 6
    p3 = render.render_poster(d2, work, "four")
    assert not np.array_equal(np.asarray(Image.open(p3["png"])), np.asarray(a))


def test_shader_layer_from_inline_source_and_funk2_recipe(work, gl):
    src = "vec4 effect(vec2 p) { return vec4(palette(0.5 + 0.5 * sin(4.0 * p.x + iPhase)), 1.0); }\n"
    r = render.render_document(doc([{"type": "shader", "source": src, "t": 0.25}]))
    assert r.image.std() > 1
    rec = {"version": 2, "settings": {"width": 540, "height": 540, "seed": 3}, "palette": {"name": "sunset"},
           "layers": [{"type": "shader", "shader": "shaders/plasma.frag"}]}
    r = render.render_document(doc([{"type": "shader", "recipe": rec, "t": 0.1}], w=96, h=64))
    assert r.image.shape == (64, 96, 3) and r.image.std() > 1
    bad = {**rec, "text": {"text": "HELLO"}}
    with pytest.raises(render.RenderError, match="draws text"):
        render.render_document(doc([{"id": "x", "type": "shader", "recipe": bad}]))


def test_post_passes_run_and_unknown_ones_are_refused(imgs):
    work, a, b = imgs
    plain = render.render_document(doc([{"type": "image", "path": str(a)}])).image
    v = render.render_document(doc([{"type": "image", "path": str(a)}], post=[{"name": "vignette", "amount": 1.2}])).image
    assert v[48, 64].astype(int).sum() >= v[0, 0].astype(int).sum() and not np.array_equal(plain, v)
    with pytest.raises(ValueError, match="unknown post pass"):
        render.render_document(doc([{"type": "image", "path": str(a)}], post=[{"name": "sparkle"}]))


# ------------------------------------------------------------------ files, fences, preview
def test_outputs_and_fences(imgs):
    work, a, b = imgs
    d = doc([{"type": "image", "path": str(a)}], export={"formats": ["png", "jpg", "webp"], "jpg_quality": 80})
    p = render.render_poster(d, work / "o", "p1")
    assert set(p) >= {"png", "jpg", "text_audit", "sidecar"}
    assert Image.open(p["jpg"]).format == "JPEG" and json.loads(p["text_audit"].read_text()) == []
    with pytest.raises(ValueError, match="outside the allowed"):
        render.render_poster(d, "/tmp/stills-elsewhere", "x")
    with pytest.raises(ValueError, match="bad output name"):
        render.render_poster(d, work, "../x")
    outside = Path("/etc/hostname")
    with pytest.raises(ValueError, match="outside the allowed"):
        render.render_document(doc([{"type": "image", "path": str(outside)}]))


def test_preview_scale_renders_a_smaller_picture(imgs):
    work, a, b = imgs
    d = doc([{"type": "image", "path": str(a)}, {"type": "solid", "color": "#ff000040"}], w=1080, h=1350)
    r = render.render_document(d, scale=0.25)
    assert (r.width, r.height) == (270, 338) and r.image.shape == (338, 270, 3)

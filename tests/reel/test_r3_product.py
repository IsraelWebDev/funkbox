import numpy as np
import pytest
from PIL import Image

from reel import product as P
from reel import render
from reel.validate import validate

W, H = 540, 960


def single_shot_reel(demo, product_over=None, motion=None, bg="inputs/shots/shot2.png"):
    prod = {"image": "inputs/brand/bottle.png", "x": 0.5, "y": 0.9, "height": 700 / H, "depth": 0.8,
            "shadow": 0.6, "match_color": 0.6}
    prod.update(product_over or {})
    return {"copy": "inputs/brand/copy.json", "settings": {"width": W, "height": H, "fps": 10},
            "shots": [{"id": "p", "source": {"image": bg}, "duration": 1.0, "product": prod,
                       "motion": motion or {"type": "kenburns", "zoom": [1.0, 1.0]}}]}


def renderer(demo, reel):
    rep = validate(reel, demo)
    assert rep.ok, rep.errors
    return render.Renderer(rep.reel, rep.deck, rep.spans, width=W, height=H, depth_mode="off")


def opaque_fraction_within(a, b, mask, tol=3):
    d = np.abs(a.astype(np.float32) - b.astype(np.float32)).max(axis=-1)
    return float((d[mask] <= tol).mean())


def test_label_pixels_survive_compositing_exactly(demo, gl):
    r = renderer(demo, single_shot_reel(demo))
    try:
        fr = r.still("p", 0.0)
        span = r.span_for_shot("p")
        pc = r.product(span)
        pl = pc.place(r.camera_for(span, 0))
        assert (pl.w, pl.h) == (300, 700) and pl.scale == pytest.approx(1.0)
        src = np.asarray(Image.open(demo / "inputs/brand/bottle.png").convert("RGBA"))
        x0, y0, x1, y1 = pl.box
        region = fr[y0:y1, x0:x1]
        tr = pc.transform
        assert tr is not None and not tr.identity                       # the colour match really ran
        opaque = src[..., 3] == 255
        assert opaque.sum() > 50000
        raw_diff = np.abs(region.astype(int) - src[..., :3].astype(int)).max(axis=-1)[opaque]
        assert raw_diff.mean() > 1.0                                    # ...and visibly changed the pixels
        inv = P.invert_transform(region, tr)
        assert opaque_fraction_within(inv, src[..., :3], opaque) >= 0.99
    finally:
        r.release()


def ncc(a, b):
    a, b = a - a.mean(), b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))


def best_ncc(region, tmpl, cx, cy, radius=6):
    th, tw = tmpl.shape
    best = -1.0
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            y, x = cy + dy, cx + dx
            if y < 0 or x < 0 or y + th > region.shape[0] or x + tw > region.shape[1]:
                continue
            best = max(best, ncc(region[y:y + th, x:x + tw], tmpl))
    return best


def gray(a):
    return a[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def test_demo123_text_is_read_back_by_template_matching(demo, gl):
    r = renderer(demo, single_shot_reel(demo))
    try:
        fr = r.still("p", 0.0)
        pl = r.product(r.span_for_shot("p")).place(r.camera_for(r.span_for_shot("p"), 0))
        src = np.asarray(Image.open(demo / "inputs/brand/bottle.png").convert("RGBA"))
        ty0, ty1, tx0, tx1 = 430, 500, 30, 270                           # the DEMO-123 line
        tmpl = gray(src[ty0:ty1, tx0:tx1])
        region = gray(fr[pl.y0:pl.y0 + pl.h, pl.x0:pl.x0 + pl.w])
        assert best_ncc(region, tmpl, tx0, ty0) > 0.95
    finally:
        r.release()


def test_scaled_product_keeps_label_and_text(demo, gl):
    # scale 0.5: the label must survive resampling plus the colour transform
    r = renderer(demo, single_shot_reel(demo, {"height": 350 / H, "y": 0.8}))
    try:
        fr = r.still("p", 0.0)
        span = r.span_for_shot("p")
        pc = r.product(span)
        pl = pc.place(r.camera_for(span, 0))
        assert (pl.w, pl.h) == (150, 350)
        src = Image.open(demo / "inputs/brand/bottle.png").convert("RGBA")
        exp = np.asarray(src.resize((150, 350), Image.LANCZOS))
        region = fr[pl.y0:pl.y0 + pl.h, pl.x0:pl.x0 + pl.w]
        inv = P.invert_transform(region, pc.transform)
        from scipy_free_erode import erode
        mask = erode(exp[..., 3] == 255, 4)
        assert mask.sum() > 10000
        assert opaque_fraction_within(inv, exp[..., :3], mask) >= 0.99
        tmpl = gray(exp[215:250, 15:135])
        assert best_ncc(gray(region), tmpl, 15, 215) > 0.95
    finally:
        r.release()


def test_match_color_zero_is_pixel_identical(demo, gl):
    r = renderer(demo, single_shot_reel(demo, {"match_color": 0.0, "shadow": 0.0}))
    try:
        fr = r.still("p", 0.0)
        pl = r.product(r.span_for_shot("p")).place(r.camera_for(r.span_for_shot("p"), 0))
        src = np.asarray(Image.open(demo / "inputs/brand/bottle.png").convert("RGBA"))
        region = fr[pl.y0:pl.y0 + pl.h, pl.x0:pl.x0 + pl.w]
        opaque = src[..., 3] == 255
        assert (region[opaque] == src[..., :3][opaque]).all()
    finally:
        r.release()


def test_contact_shadow_darkens_floor_under_the_product(demo, gl):
    with_sh = renderer(demo, single_shot_reel(demo, {"shadow": 1.0, "match_color": 0.0}))
    without = renderer(demo, single_shot_reel(demo, {"shadow": 0.0, "match_color": 0.0}))
    try:
        a, b = with_sh.still("p", 0.0), without.still("p", 0.0)
        pl = with_sh.product(with_sh.span_for_shot("p")).place(with_sh.camera_for(with_sh.span_for_shot("p"), 0))
        base_y = pl.y0 + pl.h
        band = (slice(base_y - 12, base_y + 12), slice(pl.x0 - 40, pl.x0 + pl.w + 40))
        assert a[band].mean() < b[band].mean() - 2
        far = (slice(0, 80), slice(None))
        assert np.array_equal(a[far], b[far])                            # shadow stays local
    finally:
        with_sh.release()
        without.release()


def test_product_is_depth_locked_to_the_camera(demo, gl):
    mo = {"type": "kenburns", "zoom": [1.0, 1.4], "pan": [[0, 0], [0.05, 0]]}
    r = renderer(demo, single_shot_reel(demo, {"match_color": 0.0}, motion=mo))
    try:
        span = r.span_for_shot("p")
        r.still("p", 0.0)
        pc = r.product(span)
        p0 = pc.place(r.camera_for(span, 0))
        p9 = pc.place(r.camera_for(span, span.end_frame - span.start_frame - 1))
        assert p9.h > p0.h                                              # grows with the zoom
        assert p9.x0 + p9.w // 2 != p0.x0 + p0.w // 2 or p9.w != p0.w   # and moves with the pan
    finally:
        r.release()


def test_cutout_makes_alpha_from_a_plain_background(work):
    im = Image.new("RGB", (200, 200), (250, 250, 250))
    from PIL import ImageDraw
    ImageDraw.Draw(im).rectangle((60, 40, 140, 170), fill=(30, 90, 160))
    p = work / "photo.png"
    im.save(p)
    out = P.cutout(p)
    assert out.mode == "RGBA" and out.size[0] < 100 and out.size[1] < 150
    a = np.asarray(out)[..., 3]
    assert a[a.shape[0] // 2, a.shape[1] // 2] == 255 and a[0, 0] < 50


def test_color_match_strength_is_a_cap_backed_off_for_gamut_edge_colours():
    rng = np.random.default_rng(0)
    mid = rng.integers(90, 160, (40, 40, 3), dtype=np.uint8)               # mid-tones: safe to shift
    alpha = np.full((40, 40), 255, np.uint8)
    bg = np.full((10, 10, 3), (200, 140, 90), np.uint8)
    tr = P.compute_transform(mid, alpha, bg, 0.5)
    assert tr.applied == pytest.approx(0.5)
    edge = np.zeros((40, 40, 3), np.uint8)
    edge[:] = (200, 16, 46)                                                 # brand red at the gamut edge
    edge[::2] = (20, 20, 20)
    tr2 = P.compute_transform(edge, alpha, np.full((10, 10, 3), (30, 160, 200), np.uint8), 1.0)
    assert tr2.applied < 0.5                                                # backed off, never clipped
    out = P.apply_transform(edge, tr2)
    assert np.abs(P.invert_transform(out, tr2) - edge).max() <= 3

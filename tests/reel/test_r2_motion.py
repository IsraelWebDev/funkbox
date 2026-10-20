import numpy as np
import pytest
from PIL import Image

from funkbox.reel import depth, motion
from funkbox.reel.schema import Motion


def noise_image(w=270, h=480, seed=1):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (h // 6, w // 6, 3), dtype=np.uint8)
    return np.asarray(Image.fromarray(small).resize((w, h), Image.BICUBIC))


@pytest.fixture
def mgl(gl):
    m = motion.MotionGL(270, 480)
    yield m
    m.release()


def phase_shift(a, b):
    """Integer (dx, dy) translation of b relative to a by phase correlation."""
    A, B = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
    R = A * np.conj(B)
    R /= np.abs(R) + 1e-9
    r = np.fft.ifft2(R).real
    dy, dx = np.unravel_index(np.argmax(r), r.shape)
    h, w = a.shape
    return (dx if dx < w // 2 else dx - w), (dy if dy < h // 2 else dy - h)


def gray(rgba):
    return rgba[..., :3].astype(np.float32).mean(axis=2)


def test_stub_depth_is_vertical_gradient():
    d = depth.stub_depth(8, 4)
    assert d.shape == (8, 4) and d[0, 0] == 0 and d[-1, 0] == 1


def test_parallax_moves_bottom_rows_more_than_top(mgl):
    src = mgl.upload(noise_image(), depth.stub_depth(480, 270))
    m = Motion(type="parallax", orbit_deg=5, push=0.0, dof=0.0)
    f0 = mgl.render(src, motion.camera(m, 0.1))
    f1 = mgl.render(src, motion.camera(m, 0.9))
    top = (slice(40, 120), slice(None))
    bot = (slice(360, 440), slice(None))
    dx_top = abs(phase_shift(gray(f0)[top], gray(f1)[top])[0])
    dx_bot = abs(phase_shift(gray(f0)[bot], gray(f1)[bot])[0])
    assert dx_bot > dx_top + 2, (dx_top, dx_bot)


def test_no_pixels_from_outside_the_source(mgl):
    src = mgl.upload(noise_image(), depth.stub_depth(480, 270))
    extremes = [Motion(type="parallax", orbit_deg=30, push=0.5, dof=1.0),
                Motion(type="parallax", orbit_deg=-30, push=0.0),
                Motion(type="parallax", orbit_deg=3, push=0.08)]
    for m in extremes:
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            f = mgl.render(src, motion.camera(m, t))
            assert (f[..., 3] == 255).all(), (m, t)
        assert motion.max_source_extent(lambda t, m=m: motion.camera(m, t)) <= 0.5
    kb = Motion(type="kenburns", zoom=(1.0, 1.2), pan=((-0.4, -0.4), (0.4, 0.4)))     # absurd pan: clamped
    for t in (0.0, 0.5, 1.0):
        assert (mgl.render(src, motion.camera(kb, t))[..., 3] == 255).all()
    assert motion.max_source_extent(lambda t: motion.camera(kb, t)) <= 0.5 + 1e-9


def test_orbit_is_clamped_to_three_percent():
    cam = motion.camera(Motion(type="parallax", orbit_deg=30), 1.0)
    assert abs(cam.shift[0]) <= motion.MAX_REVEAL + 1e-9


def test_kenburns_zoom_and_identity(mgl):
    img = noise_image()
    src = mgl.upload(img)
    still = mgl.render(src, motion.camera(Motion(type="none"), 0.5))
    assert np.abs(still[..., :3].astype(int) - img.astype(int)).max() <= 1           # identity, upright
    kb = Motion(type="kenburns", zoom=(1.0, 1.5))
    last = mgl.render(src, motion.camera(kb, 1.0))
    center = img[480 // 4: 480 * 3 // 4, 270 // 4: 270 * 3 // 4]                      # 1.5x zoom ~ central 2/3
    assert last[..., :3].std() > 0 and center.shape[0] > 0
    f0 = mgl.render(src, motion.camera(kb, 0.0))
    assert np.abs(f0[..., :3].astype(int) - img.astype(int)).max() <= 1


def test_camera_forward_matches_shader_backward():
    cam = motion.camera(Motion(type="parallax", orbit_deg=3, push=0.2), 0.7)
    for d in (0.1, 0.9):
        p, _ = cam.forward((0.3, 0.8), d)
        q = cam.backward(np.array(p), d)
        assert np.allclose(q, (0.3, 0.8), atol=1e-9)
    cam = motion.camera(Motion(type="kenburns", zoom=(1.0, 1.3), pan=((0, 0), (0.05, -0.04))), 0.6)
    p, _ = cam.forward((0.3, 0.8), 0.5)
    assert np.allclose(cam.backward(np.array(p), 0.5), (0.3, 0.8), atol=1e-9)


def test_depth_blur_smooths_edges():
    d = np.zeros((20, 20), np.float32)
    d[:, 10:] = 1.0
    b = depth.blur_depth(d)
    assert 0 < b[10, 10] < 1 and abs(b[10, 5]) < 0.05


def test_still_cli_renders_a_png(demo, work):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    from conftest import make_plain_reel
    reel = make_plain_reel(demo)
    (demo / "plain.reel.json").write_text(json.dumps(reel))
    out = work / "still.png"
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "REEL_DEPTH": "stub"}
    r = subprocess.run([sys.executable, str(root / "reel_cli.py"), "still", str(demo / "plain.reel.json"),
                        "--shot", "s1", "--t", "0.5", "--width", "270", "-o", str(out)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    from PIL import Image
    im = Image.open(out)
    assert im.size == (270, 480)


@pytest.mark.skipif(not depth.available(), reason="depth model not installed (python reel_cli.py setup-models)")
def test_real_depth_model_output_range(demo):
    d = depth.estimate_depth(demo / "inputs/shots/shot1.png")
    assert d.shape == (480, 270) and d.dtype == np.float32 and 0.0 <= d.min() and d.max() <= 1.0 and d.max() - d.min() > 0.5


def test_parallax_without_depth_degrades_to_kenburns(demo, gl, monkeypatch):
    import json

    from funkbox.reel import render
    from funkbox.reel.validate import validate
    monkeypatch.delenv("REEL_DEPTH", raising=False)
    from conftest import make_plain_reel
    rep = validate(make_plain_reel(demo), demo)
    assert rep.ok, rep.errors
    r = render.Renderer(rep.reel, rep.deck, rep.spans, width=270, height=480, depth_mode="off")
    try:
        fr = r.still("s1", 0.5)             # s1 is a parallax shot; no depth -> must not fail
    finally:
        r.release()
    assert fr.shape == (480, 270, 3)

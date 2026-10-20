"""v3.2 S1: FastSD extensions (request building, aspect presets, img2img clamp, path fence, stub, upscale)."""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import stills_fixtures  # noqa: F401  (env + sys.path for the stills tests)
import sdcore

ROOT = Path(__file__).resolve().parents[2]
mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


# ------------------------------------------------------------------ request building (the real code path)
def test_aspect_presets_are_multiples_of_64_and_match_the_plan():
    assert sdcore.ASPECTS == {"square": (512, 512), "portrait": (512, 768), "landscape": (768, 512),
                              "story": (512, 896), "wide": (896, 512)}
    for w, h in sdcore.ASPECTS.values():
        assert w % 64 == 0 and h % 64 == 0
    with pytest.raises(sdcore.SDError, match="unknown aspect"):
        sdcore.aspect_size("cinema")
    assert sdcore.nearest_aspect(1080, 1920) == "story"
    assert sdcore.nearest_aspect(1000, 1000) == "square"
    assert sdcore.nearest_aspect(1920, 1080) == "wide"


def test_txt2img_request_body():
    b = sdcore.build_request("a red kettle", "story", seed=7, name="k")
    assert b == {"prompt": "a red kettle", "negative_prompt": "", "width": 512, "height": 896, "seed": 7,
                 "name": "k", "mode": "txt2img", "steps": 1}


@pytest.mark.parametrize("strength,steps,expected", [(0.5, 1, 2), (0.3, 1, 4), (0.95, 1, 2), (0.05, 1, 20),
                                                     (0.5, 3, 3), (0.25, 1, 4), (1 / 3, 1, 3), (0.8, 5, 5)])
def test_img2img_steps_are_clamped_so_steps_times_strength_is_at_least_one(tmp_path, strength, steps, expected):
    init = tmp_path / "a.png"
    Image.new("RGB", (64, 64)).save(init)
    b = sdcore.build_request("x", "square", steps=steps, mode="img2img", init_image=init, strength=strength,
                             roots=[tmp_path])
    assert b["steps"] == expected and b["steps"] * strength >= 1 - 1e-9
    assert ("steps_note" in b) == (expected != steps)
    if "steps_note" in b:
        assert f"{steps} to {expected}" in b["steps_note"]
    assert b["strength"] == strength and b["init_image"] == str(init.resolve())


def test_strength_and_mode_validation(tmp_path):
    init = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(init)
    for bad in (0.0, 0.04, 0.96, 1.0):
        with pytest.raises(sdcore.SDError, match="strength"):
            sdcore.build_request("x", mode="img2img", init_image=init, strength=bad, roots=[tmp_path])
    with pytest.raises(sdcore.SDError, match="needs init_image"):
        sdcore.build_request("x", mode="img2img")
    with pytest.raises(sdcore.SDError, match="only for mode img2img"):
        sdcore.build_request("x", init_image=init, roots=[tmp_path])
    with pytest.raises(sdcore.SDError, match="mode must be"):
        sdcore.build_request("x", mode="inpaint")
    with pytest.raises(sdcore.SDError, match="non-empty"):
        sdcore.build_request("  ")


def test_init_image_path_fence(tmp_path):
    root, other = tmp_path / "root", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    ok, bad = root / "ok.png", other / "bad.png"
    for p in (ok, bad):
        Image.new("RGB", (8, 8)).save(p)
    assert sdcore.fence_path(ok, [root]) == ok.resolve()
    with pytest.raises(sdcore.SDError, match="outside the allowed"):
        sdcore.fence_path(bad, [root])
    with pytest.raises(sdcore.SDError, match="outside the allowed"):
        sdcore.fence_path(root / ".." / "other" / "bad.png", [root])
    with pytest.raises(sdcore.SDError, match="not found"):
        sdcore.fence_path(root / "missing.png", [root])
    link = root / "link.png"
    try:
        link.symlink_to(bad)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(sdcore.SDError, match="outside the allowed"):       # symlinks are followed
        sdcore.fence_path(link, [root])
    with pytest.raises(sdcore.SDError, match="outside the allowed"):
        sdcore.build_request("x", mode="img2img", init_image=bad, roots=[root])


def test_size_tracker_warns_once_per_new_size():
    t = sdcore.SizeTracker()
    assert t.warning(512, 512) is None                      # cold start at the warm size: model load, no recompile
    t.mark(512, 512)
    w = t.warning(512, 896)
    assert w and "first image at 512x896 will take ~2-3 min" in w
    t.mark(512, 896)
    assert t.warning(512, 896) is None and t.warning(512, 512) is None
    assert t.warning(896, 512)
    t.clear()
    assert t.warning(512, 896)                              # a restarted FastSD has compiled nothing


# ------------------------------------------------------------------ stub + upscale
def test_stub_is_deterministic_and_seed_sensitive():
    a = sdcore.fake_image("a red kettle", 128, 192, 3)
    b = sdcore.fake_image("a red kettle", 128, 192, 3)
    c = sdcore.fake_image("a red kettle", 128, 192, 4)
    d = sdcore.fake_image("a blue kettle", 128, 192, 3)
    assert a.size == (128, 192) and np.array_equal(np.asarray(a), np.asarray(b))
    assert not np.array_equal(np.asarray(a), np.asarray(c)) and not np.array_equal(np.asarray(a), np.asarray(d))


def test_stub_img2img_blends_toward_the_init_image_by_strength():
    init = Image.fromarray((np.random.default_rng(1).random((90, 120, 3)) * 255).astype(np.uint8))
    dist = {}
    for s in (0.1, 0.5, 0.9):
        out = sdcore.fake_image("p", 128, 128, 1, init, s)
        base = sdcore.cover_resize(init, 128, 128)
        dist[s] = float(np.abs(np.asarray(out, float) - np.asarray(base, float)).mean())
    assert dist[0.1] < dist[0.5] < dist[0.9]


def test_upscale_fallback_is_exactly_2x():
    for size in ((64, 48), (101, 37)):
        up = sdcore.upscale_lanczos(Image.new("RGB", size, (90, 120, 200)))
        assert up.size == (size[0] * 2, size[1] * 2)


# ------------------------------------------------------------------ the studio MCP tools on the stub
def _text(r):
    return "\n".join(c.text for c in r.content if c.type == "text")


def test_studio_tools_on_the_stub(tmp_path):
    home = tmp_path / "home"
    (home / "inputs").mkdir(parents=True)
    env = {**os.environ, "FUNK_HOME": str(home), "FUNK_OUT": str(home / "out"), "FUNK_INPUTS": str(home / "inputs"),
           "STUDIO_FAKE_SD": "1"}

    async def go():
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "studio" / "studio_mcp.py")], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                assert {"generate_image", "restyle_image", "upscale_image", "status", "start", "stop"} <= tools
                desc = {t.name: t.description for t in (await s.list_tools()).tools}
                assert "Stability AI Community License" in desc["generate_image"]

                async def call(name, args):
                    res = await s.call_tool(name, args)
                    assert not res.is_error, res.content
                    return res

                # n=3 gives 3 files and one sheet
                r1 = await call("generate_image", {"prompt": "mop on a kitchen floor", "n": 3, "seed": 10, "name": "mop"})
                files = sorted(p.name for p in (home / "inputs").glob("mop-*.png"))
                assert files == ["mop-1.png", "mop-2.png", "mop-3.png"]
                assert [c.type for c in r1.content] == ["text", "image"]
                assert (home / "out" / "sheet-mop.png").is_file() and "seed 10" in _text(r1) and "seed 12" in _text(r1)
                assert all(Image.open(home / "inputs" / f).size == (512, 512) for f in files)
                assert len({hashlib.sha256((home / "inputs" / f).read_bytes()).hexdigest() for f in files}) == 3

                # deterministic: the same call again writes identical bytes
                before = (home / "inputs" / "mop-2.png").read_bytes()
                await call("generate_image", {"prompt": "mop on a kitchen floor", "n": 3, "seed": 10, "name": "mop"})
                assert (home / "inputs" / "mop-2.png").read_bytes() == before

                # a new size warns BEFORE generating
                r2 = await call("generate_image", {"prompt": "tall", "aspect": "story", "name": "tall"})
                assert "NOT STARTED" in _text(r2) and "first image at 512x896 will take ~2-3 min" in _text(r2)
                assert not (home / "inputs" / "tall.png").exists()
                r3 = await call("generate_image", {"prompt": "tall", "aspect": "story", "name": "tall",
                                                   "confirm_slow": True})
                assert Image.open(home / "inputs" / "tall.png").size == (512, 896)
                r4 = await call("generate_image", {"prompt": "tall again", "aspect": "story", "name": "tall2"})
                assert "NOT STARTED" not in _text(r4)                    # that size is now compiled

                # img2img: strength clamp is reported, aspect follows the picture
                r5 = await call("restyle_image", {"path": "inputs/tall.png", "prompt": "oil painting",
                                                  "strength": 0.3, "name": "tall-oil"})
                assert "steps raised from 1 to 4" in _text(r5)
                assert Image.open(home / "inputs" / "tall-oil.png").size == (512, 896)
                r6 = await call("restyle_image", {"path": "/etc/hostname", "prompt": "x"})
                assert _text(r6).startswith("ERROR") and "outside the allowed" in _text(r6)

                # upscale: exactly 2x, method reported
                r7 = await call("upscale_image", {"path": "inputs/tall.png", "name": "tall-big"})
                assert Image.open(home / "inputs" / "tall-big.png").size == (1024, 1792)
                assert "lanczos" in _text(r7)
                r8 = await call("upscale_image", {"path": "../../etc/passwd"})
                assert _text(r8).startswith("ERROR")
                st = await call("status", {})
                assert "stub" in _text(st) and "512x896" in _text(st)

    asyncio.run(go())


def test_install_copies_the_launcher_with_its_helper_and_licence_is_documented(capsys, tmp_path):
    import install
    assert install.main(["--dry-run", "--with", "fastsd", "--register", "none", "--no-selftest"]) == 0
    assert "sdcore.py" in capsys.readouterr().out
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    doc = (ROOT / "docs" / "studio.md").read_text()
    for t in (notices, doc):
        assert "Stability AI Community License" in t and "US$1M" in t
    assert "register" in notices and "register" in doc

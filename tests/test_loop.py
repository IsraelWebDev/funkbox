from pathlib import Path

import pytest

from funkgl.loopcheck import validate_loop
from funkgl.recipe import Layer, Recipe2, Settings2
from funkgl.shader import ShaderSource

SHADERS = sorted((Path(__file__).resolve().parent.parent / "shaders").glob("*.frag"))


def rec(source: str, seconds=2.0, **kw) -> Recipe2:
    return Recipe2(settings=Settings2(width=96, height=96, fps=12, seconds=seconds, seed=4),
                   layers=[Layer(source=source)], **kw)


def test_have_shaders():
    assert len(SHADERS) >= 10


@pytest.mark.parametrize("path", SHADERS, ids=lambda p: p.stem)
def test_bundled_shader_loops(gl, path):
    src = path.read_text(encoding="utf-8")
    if ShaderSource(src).no_loopcheck:
        pytest.skip("@noloopcheck: loops only if the layers below do")
    r = validate_loop(Recipe2(settings=Settings2(width=128, height=128, fps=12, seconds=2.0, seed=5),
                              layers=[Layer(shader=path.relative_to(path.parent.parent).as_posix())]),
                      size=128)
    assert r.ok, r.reason
    assert "static" not in r.reason, "bundled shader must move"
    assert r.bad_pixel_frac <= 0.001


def test_itime_shader_fails_with_hint(gl):
    r = validate_loop(rec("vec4 effect(vec2 p){ return vec4(vec3(0.5 + 0.5*sin(5.0*p.x + iTime*1.3)), 1.0); }"),
                      size=96)
    assert not r.ok
    assert "iTime" in r.reason
    assert r.bad_pixel_frac > 0.001


def test_noninteger_phase_fails(gl):
    r = validate_loop(rec("vec4 effect(vec2 p){ return vec4(vec3(0.5 + 0.5*sin(5.0*p.x + iPhase*1.5)), 1.0); }"),
                      size=96)
    assert not r.ok


def test_constant_colour_is_static(gl):
    r = validate_loop(rec("vec4 effect(vec2 p){ return vec4(0.2, 0.4, 0.6, 1.0); }"), size=96)
    assert r.ok
    assert r.reason.startswith("static")
    assert r.typical_step < 0.05

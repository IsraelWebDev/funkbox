import pytest

from funkbox.gl.shader import (USER_LINE_OFFSET, ShaderError, ShaderSource, parse_params,
                           sample_params)

GOOD = """vec4 effect(vec2 p) {
  return vec4(palette(p.x * 0.5 + 0.5) * 0.5, 1.0);
}
"""


def test_native_compiles(gl):
    r = ShaderSource(GOOD).compile(gl)
    assert r.ok and r.program is not None and not r.errors


def test_error_line_is_in_user_coordinates(gl):
    bad = "// line 1\nvec4 effect(vec2 p) {\n  return vec4(nope, 0.0, 0.0, 1.0);\n}\n"
    r = ShaderSource(bad).compile(gl)
    assert not r.ok and r.program is None
    assert r.errors and all(e["line"] == 3 for e in r.errors if e["line"] is not None)
    assert any(e["line"] == 3 for e in r.errors)
    assert USER_LINE_OFFSET > 100          # the offset really is being subtracted


def test_error_line_other_position(gl):
    # Undeclared identifier, not a syntax error: drivers disagree on which line a missing-token
    # syntax error is reported (Intel Windows reports the next line), but all pin this one.
    bad = "\n\n\n\nvec4 effect(vec2 p) {\n  float x = nope2;\n  return vec4(x);\n}\n"
    r = ShaderSource(bad).compile(gl)
    assert not r.ok
    assert any(e["line"] == 6 for e in r.errors), r.errors


def test_shadertoy_detection_and_compile(gl):
    toy = "void mainImage(out vec4 c, in vec2 fc) { c = vec4(fc / iResolution.xy, 0.5, 1.0); }\n"
    s = ShaderSource(toy)
    assert s.style == "shadertoy"
    assert s.compile(gl).ok
    assert ShaderSource(GOOD).style == "native"


def test_no_entry_point_raises():
    with pytest.raises(ShaderError, match="effect"):
        ShaderSource("float f(float x) { return x; }\n")
    with pytest.raises(ShaderError):       # commented-out entry points do not count
        ShaderSource("// vec4 effect(vec2 p) { return vec4(1.0); }\n")


SRC_PARAMS = """uniform float u_freq;   // @param 2.0 9.0 4.0
uniform int   u_arms;   // @param 2 8 5
uniform vec3  u_tint;   // @param color #ff2bd6
uniform float u_plain;
vec4 effect(vec2 p) { return vec4(u_tint * u_freq * float(u_arms), 1.0); }
"""


def test_param_parsing_order_and_ranges():
    ps = parse_params(SRC_PARAMS)
    assert [p.name for p in ps] == ["u_freq", "u_arms", "u_tint"]
    assert [p.type for p in ps] == ["float", "int", "color"]
    assert (ps[0].min, ps[0].max, ps[0].default) == (2.0, 9.0, 4.0)
    assert (ps[1].min, ps[1].max, ps[1].default) == (2, 8, 5)
    assert ps[2].default == "#ff2bd6"


def test_seed_sampling_deterministic_and_varies(gl):
    ps = parse_params(SRC_PARAMS)
    a, b = sample_params(ps, 7), sample_params(ps, 7)
    assert a == b
    assert sample_params(ps, 8) != a
    assert 2.0 <= a["u_freq"] <= 9.0 and 2 <= a["u_arms"] <= 8
    assert a["u_tint"] == "#ff2bd6"
    assert sample_params(ps, 7, {"u_freq": 3.0})["u_freq"] == 3.0
    ShaderSource(SRC_PARAMS).compile(gl)

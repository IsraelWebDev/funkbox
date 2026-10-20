"""The six GLSL ports must reproduce the v1 numpy effects (same params, same frame), look controls included."""
import numpy as np
import pytest

import v1_oracle as v1
from funkbox.gl import Engine
from funkbox.gl.recipe import Layer, Recipe2, Settings2

SZ = 96


def map_params(effect: str, p: dict) -> dict:
    if effect == "plasma":
        return {f"u_k{i}": v for i, v in enumerate(p["k"])}
    if effect == "moire":
        return {"u_k": p["k"], "u_ph": p["ph"]}
    if effect == "warp":
        return {"u_amp": p["amp"], "u_freq": p["freq"], "u_stripes": p["stripes"], "u_iters": p["iters"]}
    if effect == "tunnel":
        return {"u_n": p["n"], "u_m": p["m"]}
    if effect == "rings":
        return {"u_k": p["k"], "u_lobes": p["lobes"], "u_wob": p["wob"]}
    d = {"u_balls": len(p["balls"])}
    for i, (ax, ay, fx, fy, ph) in enumerate(p["balls"]):
        d.update({f"u_ax{i}": ax, f"u_ay{i}": ay, f"k_fx{i}": fx, f"k_fy{i}": fy, f"u_ph{i}": ph})
    return d


LOOKS = [dict(), dict(kaleido=6, spin=1, zoom=0.15), dict(kaleido=0, spin=-1, zoom=0.0)]


@pytest.mark.parametrize("effect", ["plasma", "moire", "warp", "tunnel", "blobs", "rings"])
@pytest.mark.parametrize("look", LOOKS, ids=["plain", "kaleido-spin-zoom", "counter-spin"])
def test_port_matches_v1(gl, effect, look):
    p = v1.EFFECTS[effect].sample(np.random.default_rng(3))
    ctl = {"u_kaleido": look.get("kaleido", 0), "k_spin": look.get("spin", 0), "u_zoom": look.get("zoom", 0.0)}
    r = Recipe2(settings=Settings2(width=SZ, height=SZ, fps=12, seconds=2, seed=3), palette="vapor",
                cycles=1, layers=[Layer(shader=f"shaders/{effect}.frag", params={**map_params(effect, p), **ctl})])
    rc = v1.Look(effect=effect, palette="vapor", cycles=1, params=p, **look)
    eng = Engine(SZ, SZ, gl)
    try:
        for fr in (0, 5, 13):
            a = eng.render(r, fr).astype(int)
            b = v1.render_field(fr, 24, SZ, SZ, rc).astype(int)
            d = np.abs(a - b)
            assert d.mean() < 0.5, (effect, look, fr, d.mean())
            assert (d.max(axis=2) > 24).mean() < 0.005, (effect, look, fr)
    finally:
        eng.release()

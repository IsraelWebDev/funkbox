"""The six GLSL ports must reproduce the v1 numpy effects (same params, same frame)."""
import numpy as np
import pytest

import funk
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


@pytest.mark.parametrize("effect", ["plasma", "moire", "warp", "tunnel", "blobs", "rings"])
def test_port_matches_v1(gl, effect):
    p = funk.EFFECTS[effect].sample(np.random.default_rng(3))
    r = Recipe2(settings=Settings2(width=SZ, height=SZ, fps=12, seconds=2, seed=3), palette="vapor",
                cycles=1, layers=[Layer(shader=f"shaders/{effect}.frag", params=map_params(effect, p))])
    st = funk.Settings(width=SZ, height=SZ, fps=12, seconds=2, seed=3)
    rc = funk.Recipe(effect=effect, palette="vapor", cycles=1, vignette=0.0, params=p)
    eng = Engine(SZ, SZ, gl)
    try:
        for fr in (0, 5, 13):
            a = eng.render(r, fr).astype(int)
            b = funk.render_frame(fr, st, rc).astype(int)
            d = np.abs(a - b)
            assert d.mean() < 0.5, (effect, fr, d.mean())
            assert (d.max(axis=2) > 24).mean() < 0.005, (effect, fr)
    finally:
        eng.release()

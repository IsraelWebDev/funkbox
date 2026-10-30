"""SVG path shapes (curves, arcs, fill rules), importing SVG files, and clipping a layer to a shape."""
import math

import numpy as np
import pytest

from funkbox.stills import render, schema, vpath


def doc(layers, w=400, h=400):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": "#ffffff"}, "casual": True, "layers": layers}


def test_path_parser_handles_every_command():
    subs = vpath.parse("M10,10 h20 v20 H10 Z m40,0 l10,0 c5,0 5,10 0,10 s-5,-10 0,-10 q5,5 10,0 t10,0 "
                       "a5,5 0 0 1 10,0 Z")
    assert len(subs) == 2 and subs[0][1] and subs[1][1]
    assert subs[0][0][:4] == [(10, 10), (30, 10), (30, 30), (10, 30)]
    with pytest.raises(vpath.PathError, match="start with M"):
        vpath.parse("L1,1")
    with pytest.raises(vpath.PathError, match="missing numbers"):
        vpath.parse("M1,1 C2,2 3,3")


def test_an_arc_is_round():
    (pts, closed), = vpath.parse("M0,50 A50,50 0 1 0 100,50 A50,50 0 1 0 0,50 Z", tol=0.2)
    radii = [math.dist(p, (50, 50)) for p in pts]
    assert closed and max(radii) - min(radii) < 0.01 and len(pts) > 60


def test_fill_rules_make_holes():
    ring = ("M100,200 a100,100 0 1,0 200,0 a100,100 0 1,0 -200,0 Z "
            "M150,200 a50,50 0 1,0 100,0 a50,50 0 1,0 -100,0 Z")             # both circles the same direction
    lay = {"id": "r", "type": "shape", "kind": "path", "units": "px", "d": ring, "fill": "#000000"}
    nonzero = render.render_document(doc([lay])).image
    evenodd = render.render_document(doc([dict(lay, fill_rule="evenodd")])).image
    assert tuple(nonzero[200, 200]) == (0, 0, 0)                         # same direction: no hole under nonzero
    assert tuple(evenodd[200, 200]) == (255, 255, 255) and tuple(evenodd[200, 120]) == (0, 0, 0)
    reverse = ring.replace("a50,50 0 1,0 100,0 a50,50 0 1,0 -100,0", "a50,50 0 1,1 100,0 a50,50 0 1,1 -100,0")
    assert tuple(render.render_document(doc([dict(lay, d=reverse)])).image[200, 200]) == (255, 255, 255)


def test_svg_files_import_with_their_colours_and_transforms(tmp_path, monkeypatch):
    (tmp_path / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#ff8800"/></linearGradient></defs>'
        '<rect width="200" height="100" fill="url(#g)"/>'
        '<g transform="translate(100 50) scale(2)"><circle r="10" style="fill:#0000ff"/></g>'
        '<text x="1" y="9">x</text></svg>')
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    r = render.render_document(doc([{"id": "s", "type": "svg", "path": "logo.svg", "x": 0.0, "y": 0.25, "width": 1.0}]),
                               base_dir=tmp_path)
    im = r.image
    assert tuple(im[200, 200]) == (0, 0, 255)                            # the circle, scaled by its group transform
    assert tuple(im[200, 20]) == (255, 136, 0)                           # the gradient falls back to its first stop
    assert tuple(im[20, 200]) == (255, 255, 255)                         # height follows the viewBox (2:1)
    assert any("<text>" in w for w in r.warnings)


def test_clip_shows_a_layer_only_inside_a_shape():
    layers = [{"id": "win", "type": "shape", "kind": "ellipse", "points": [[0.25, 0.25], [0.75, 0.75]],
               "fill": "#000000", "visible": False},
              {"id": "red", "type": "solid", "color": "#ff0000", "clip": "win"}]
    im = render.render_document(doc(layers)).image
    assert tuple(im[200, 200]) == (255, 0, 0) and tuple(im[20, 20]) == (255, 255, 255)
    errs = " | ".join(schema.check(doc([{"id": "a", "type": "solid", "color": "#ff0000", "clip": "nope"},
                                        {"id": "b", "type": "solid", "color": "#ff0000", "clip": "a"}])))
    assert "is not a layer id" in errs and "must name a shape or svg layer" in errs

"""Vector export: SVG and PDF with text as outlines, matching the pixel render."""
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from funkbox.stills import render, schema, vector

ROOT = Path(__file__).resolve().parents[2]
NS = "{http://www.w3.org/2000/svg}"


def doc(layers, w=600, h=400, **kw):
    return {"version": 1, "canvas": {"width": w, "height": h, "background": "#ffffff"}, "casual": True,
            "brand": {"font_display": "fonts/Rubik-Bold.ttf", "font_caption": "fonts/Oswald-SemiBold.ttf"},
            "layers": layers, **kw}


FLAT = [
    {"id": "tri", "type": "shape", "kind": "polygon", "points": [[0.05, 0.9], [0.3, 0.1], [0.55, 0.9]], "fill": "#e0245e"},
    {"id": "dot", "type": "shape", "kind": "ellipse", "points": [[0.6, 0.1], [0.95, 0.6]], "fill": "#1b224b"},
    {"id": "bar", "type": "shape", "kind": "polyline", "points": [[0.6, 0.8], [0.8, 0.7], [0.95, 0.9]], "width": 0.04,
     "fill": "#2fd4ff"},
    {"id": "he", "type": "text", "text": "טקסט מדויק", "style": "display", "size": 0.12, "x": 0.05, "y": 0.05,
     "max_width": 0.9, "align": "center", "color": "#000000"},
    {"id": "en", "type": "text", "text": "EXACT TEXT. EVERY TIME.", "size": 0.08, "x": 0.1, "y": 0.55, "max_width": 0.8,
     "align": "center", "color": "#000000", "arc": 0.9},
]


def _diff(a, b):
    d = np.abs(np.asarray(a, int)[..., :3] - np.asarray(b, int)[..., :3]).sum(-1)
    return d.mean(), (d > 120).mean()


def test_svg_round_trips_through_funkbox_svg_import(tmp_path, monkeypatch):
    r = render.render_document(doc(FLAT), base_dir=ROOT)
    rep = vector.export(r, tmp_path / "p.svg")
    assert rep["vector"] == ["tri", "dot", "bar", "he", "en"] and not rep["pixels"]
    svg = ET.parse(tmp_path / "p.svg").getroot()
    assert svg.get("viewBox") == "0 0 600 400"
    assert not list(svg.iter(NS + "text")) and not list(svg.iter(NS + "image"))     # text is outlines
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    back = render.render_document(doc([{"id": "s", "type": "svg", "path": "p.svg", "x": 0, "y": 0, "width": 1.0}]),
                                  base_dir=tmp_path)
    mean, bad = _diff(r.image, back.image)
    assert mean < 5 and bad < 0.02                       # only anti-aliasing of small text differs


def test_pixel_layers_are_embedded_and_named(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_INPUTS", str(tmp_path))
    Image.new("RGB", (32, 32), (0, 200, 0)).save(tmp_path / "pic.png")
    layers = [{"id": "pic", "type": "image", "path": "pic.png", "fit": "cover"},
              {"id": "soft", "type": "shape", "kind": "rect", "points": [[0.1, 0.1], [0.4, 0.4]], "fill": "#ff0000",
               "filters": {"blur": 4}},
              {"id": "t", "type": "text", "text": "hi", "size": 0.1, "x": 0.5, "y": 0.5, "max_width": 0.4,
               "color": "#000000", "effects": {"shadow": 0.6}}]
    r = render.render_document(doc(layers), base_dir=tmp_path)
    rep = vector.export(r, tmp_path / "p.svg")
    assert set(rep["pixels"]) == {"pic", "soft", "t"} and "blur" not in rep["pixels"]["soft"]
    assert rep["pixels"]["soft"] == "adjust/filters" and "shadow" in rep["pixels"]["t"]
    assert len(list(ET.parse(tmp_path / "p.svg").getroot().iter(NS + "image"))) == 3
    assert any("kept as pictures" in w for w in rep["warnings"])


def test_gradient_border_clip_and_blend_are_vectors(tmp_path):
    layers = [{"id": "g", "type": "gradient", "stops": ["#ff0000", "#0000ff"], "angle": 0},
              {"id": "b", "type": "border", "px": 10, "color": "#000000"},
              {"id": "win", "type": "shape", "kind": "ellipse", "points": [[0.3, 0.3], [0.7, 0.7]], "fill": "#000000",
               "visible": False},
              {"id": "c", "type": "solid", "color": "#ffff00", "clip": "win", "blend": "multiply", "opacity": 0.5}]
    r = render.render_document(doc(layers), base_dir=ROOT)
    vector.export(r, tmp_path / "p.svg")
    s = (tmp_path / "p.svg").read_text()
    assert "<linearGradient" in s and 'x1="0" y1="200" x2="600" y2="200"' in s
    assert "<clipPath" in s and "mix-blend-mode:multiply" in s and 'opacity="0.5"' in s and 'fill-rule="evenodd"' in s


def test_pdf_has_the_poster_size_and_matches_the_render(tmp_path):
    r = render.render_document(doc(FLAT), base_dir=ROOT)
    vector.export(r, tmp_path / "p.pdf")
    from pypdf import PdfReader
    page = PdfReader(str(tmp_path / "p.pdf")).pages[0]
    assert [round(float(v)) for v in page.mediabox[2:]] == [450, 300]        # 600x400 px at 96 dpi
    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm not installed")
    subprocess.run(["pdftoppm", "-r", "96", "-png", "-singlefile", str(tmp_path / "p.pdf"), str(tmp_path / "pg")],
                   check=True)
    mean, bad = _diff(r.image, Image.open(tmp_path / "pg.png").convert("RGB"))
    assert mean < 5 and bad < 0.02


def test_formats_svg_and_pdf_through_the_export_list(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNK_OUT", str(tmp_path))
    d = doc(FLAT[:1], export={"formats": ["png", "svg", "pdf"]})
    assert not schema.check(d)
    paths = render.render_poster(d, tmp_path, "x", base_dir=ROOT)
    assert {"png", "svg", "pdf"} <= set(paths) and paths["svg"].read_text().startswith("<svg")


def test_the_arc_map_is_the_inverse_of_the_pixel_warp():
    from funkbox.stills.shapes import warp_text
    img = np.zeros((200, 300, 4), np.uint8)
    img[95:105, 140:160] = 255                                   # a small block left of centre-ish
    out = warp_text(img, (150, 100), arc=120.0, skew_x=10, rotate_deg=15)
    f = vector.text_geometry((150, 100), arc=120.0, skew_x=10, rotate_deg=15)
    cx, cy = f((150.0, 100.0))
    ys, xs = np.nonzero(out[..., 3] > 128)
    assert abs(xs.mean() - cx) < 1.5 and abs(ys.mean() - cy) < 1.5
    x2, y2 = f((250.0, 100.0))
    img2 = np.zeros_like(img)
    img2[95:105, 245:255] = 255
    ys, xs = np.nonzero(warp_text(img2, (150, 100), arc=120.0, skew_x=10, rotate_deg=15)[..., 3] > 128)
    assert abs(xs.mean() - x2) < 1.5 and abs(ys.mean() - y2) < 1.5

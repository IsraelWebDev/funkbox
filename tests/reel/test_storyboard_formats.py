"""`reel storyboard --format all`: one sheet with a row per declared format."""

import json
from types import SimpleNamespace

from PIL import Image

FORMATS = [{"id": "tall", "width": 270, "height": 480}, {"id": "wide", "width": 480, "height": 270}]


def _reel(demo, formats=FORMATS):
    d = json.loads((demo / "demo.reel.json").read_text())
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "none"}
    d["shots"] = d["shots"][:2]
    if formats:
        d["formats"] = formats
    else:
        d.pop("formats", None)
    return d


def test_one_row_per_format_at_one_tile_height(demo, gl, work):
    from funkbox.reel import render
    from funkbox.reel.storyboard import formats_storyboard

    d = _reel(demo)
    asked = []

    def plan_for(fmt):
        asked.append(fmt)
        return render.prepare(d, demo, fmt=fmt)

    img, fids = formats_storyboard(plan_for, work / "all.png", tile_h=120)
    assert fids == ["tall", "wide"]
    assert asked == [None, "tall", "wide"]
    assert (work / "all.png").is_file() and Image.open(work / "all.png").size == img.size
    n = len(render.prepare(d, demo).spans)  # the same shots in every row
    tall_w, wide_w = round(120 * 270 / 480) // 2 * 2, round(120 * 480 / 270) // 2 * 2
    assert img.width >= 10 + n * (wide_w + 10)  # the widest row sets the sheet's width
    # the wide row's first tile is a real frame (not background) and has the wide shape
    gray = img.convert("L")
    col = [gray.getpixel((10 + wide_w // 2, y)) for y in range(img.height)]
    assert max(col) - min(col) > 20
    assert tall_w < wide_w


def test_a_reel_without_formats_gives_one_row(demo, gl, work):
    from funkbox.reel import render
    from funkbox.reel.storyboard import formats_storyboard

    d = _reel(demo, formats=None)
    _, fids = formats_storyboard(lambda f: render.prepare(d, demo, fmt=f), None, tile_h=100)
    assert fids == ["270x480"]


def test_cli_format_all_writes_one_sheet(demo, gl, work, capsys):
    from funkbox.reel import cli

    reel = demo / "fmts.reel.json"
    reel.write_text(json.dumps(_reel(demo)))
    out = work / "sb-all.png"
    rc = cli.cmd_storyboard(SimpleNamespace(reel=str(reel), project=None, format="all", output=str(out), tile=120))
    assert rc == 0 and out.is_file()
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith(str(out)) and "2 formats: tall wide" in first


def test_mcp_tool_takes_format_all(demo, gl, monkeypatch):
    from funkbox.reel import tools

    monkeypatch.setattr(tools, "on_gl", lambda fn, *a, **k: fn(*a, **k))  # the test's GL context, as test_rv4 does
    reel = demo / "fmts-mcp.reel.json"
    reel.write_text(json.dumps(_reel(demo)))
    res = tools.reel_storyboard(reel=str(reel), format="all")
    assert isinstance(res, list) and len(res) == 2, res
    assert "2 formats (tall, wide), a row each; saved " in str(res[0]), res[0]

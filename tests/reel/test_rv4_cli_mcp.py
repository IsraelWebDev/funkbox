"""Reel variants, card 4: the CLI (`funkbox reel variants`) and the `reel_variants` MCP tool."""

import json
from types import SimpleNamespace

from funkbox.cli import main
from funkbox.config import funk_out
from funkbox.reel import render, tools

WARM = "#f6f7f4,#0f7c6e"
DARK = "#0f1216,#4fd1b9"


def plain_reel(demo):
    """The demo reel with voiceover and music off and 3 s shots (a caption needs room to finish typing)."""
    d = json.loads((demo / "demo.reel.json").read_text(encoding="utf-8"))
    d["audio"] = {"voiceover": False}
    for sh in d["shots"]:
        sh["duration"] = 3.0
        sh.pop("vo", None)
    return d


def write_reel(demo, name):
    path = demo / name
    path.write_text(json.dumps(plain_reel(demo)), encoding="utf-8")
    return path


def write_vary(work, vary, name="vary.json"):
    path = work / name
    path.write_text(json.dumps(vary), encoding="utf-8")
    return path


def test_count_must_equal_the_length_of_the_vary_lists(demo, work, capsys):
    reel = write_reel(demo, "rv4-count.reel.json")
    vary = write_vary(work, {"brand.palette": [WARM, DARK]})
    rc = main(["reel", "variants", str(reel), "--vary", str(vary), "--count", "3", "--out", str(work), "--name", "cnt"])
    assert rc == 1
    assert "--count 3 must equal the length" in capsys.readouterr().err
    assert not (work / "cnt-variants").exists()


def test_cli_refuses_a_vary_key_that_writes_literal_text(demo, work, capsys):
    reel = write_reel(demo, "rv4-text.reel.json")
    vary = write_vary(work, {"shots.s1.text": ["a", "b"]})
    rc = main(["reel", "variants", str(reel), "--vary", str(vary), "--out", str(work), "--name", "txt"])
    assert rc == 1
    assert capsys.readouterr().err.strip()
    assert not (work / "txt-variants").exists()


def test_cli_renders_each_variant_and_writes_the_manifest(demo, work, gl, capsys):
    reel = write_reel(demo, "rv4-ok.reel.json")
    vary = write_vary(work, {"brand.palette": [WARM, DARK]})
    rc = main(["reel", "variants", str(reel), "--vary", str(vary), "--no-audio", "--out", str(work), "--name", "cli2"])
    out = capsys.readouterr().out
    assert rc == 0, out
    manifest = json.loads((work / "cli2-variants" / "variants.json").read_text(encoding="utf-8"))
    assert manifest["count"] == 2
    assert "v01" in out and "v02" in out and "audit OK" in out
    assert "manifest:" in out
    assert all(e["audit_ok"] for e in manifest["variants"])


def test_cli_exits_non_zero_when_a_variant_audit_fails(demo, work, capsys, monkeypatch):
    def fake_render_formats(src, stem, *, out_dir, **kw):
        mp4 = out_dir / f"{stem}.mp4"
        audit = out_dir / f"{stem}.text_audit.json"
        mp4.write_bytes(b"")
        audit.write_text("{}", encoding="utf-8")
        return [SimpleNamespace(paths={"mp4": str(mp4), "text_audit": str(audit)}, audit_ok=(stem != "v02"))]

    monkeypatch.setattr(render, "render_formats", fake_render_formats)
    reel = write_reel(demo, "rv4-audit.reel.json")
    vary = write_vary(work, {"brand.palette": [WARM, DARK]})
    rc = main(["reel", "variants", str(reel), "--vary", str(vary), "--out", str(work), "--name", "aud"])
    assert rc == 1
    assert "variant 2: text audit failed" in capsys.readouterr().err
    manifest = json.loads((work / "aud-variants" / "variants.json").read_text(encoding="utf-8"))
    assert [e["audit_ok"] for e in manifest["variants"]] == [True, False]


def test_the_mcp_tool_is_registered():
    assert tools.reel_variants in tools.TOOLS


def test_the_mcp_tool_renders_and_returns_the_manifest(demo, work, gl, monkeypatch):
    # The server runs tools on its own GL thread; the test's context is already current here, so run inline.
    monkeypatch.setattr(tools, "on_gl", lambda fn, *a, **k: fn(*a, **k))
    reel = write_reel(demo, "rv4-mcp.reel.json")
    res = tools.reel_variants(reel=str(reel), vary=json.dumps({"brand.palette": [WARM, DARK]}), name="mcp4")
    assert isinstance(res, dict), res
    assert res["count"] == 2
    assert (funk_out() / "mcp4-variants" / "variants.json").is_file()


def test_the_mcp_tool_refuses_bad_input_with_a_message(demo, work):
    reel = write_reel(demo, "rv4-mcp-bad.reel.json")
    for bad_vary in ("not json", '{"shots.s1.text": ["a", "b"]}', "{}"):
        res = tools.reel_variants(reel=str(reel), vary=bad_vary, name="mcp5")
        assert isinstance(res, str) and res, bad_vary
    assert not (funk_out() / "mcp5-variants").exists()
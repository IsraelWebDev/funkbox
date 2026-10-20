"""The one command line: every old verb has a new equivalent, every sub-command answers --help, and the
four main paths run for real on demo assets."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples" / "reel_demo"))

#: old CLI verb -> the new command (also the table in docs/migration-v4.md)
OLD_TO_NEW = {
    "funk.py list": "loop list", "funk.py render": "loop render", "funk.py sheet": "loop sheet",
    "funk2.py info": "doctor", "funk2.py check": "loop check", "funk2.py still": "loop still",
    "funk2.py sheet": "loop sheet", "funk2.py render": "loop render", "funk2.py port-v1": "loop write-shader",
    "reel_cli.py validate": "reel validate", "reel_cli.py storyboard": "reel storyboard",
    "reel_cli.py still": "reel still", "reel_cli.py render": "reel render", "reel_cli.py beats": "reel beats",
    "reel_cli.py cutout": "reel cutout", "reel_cli.py setup-models": "reel setup-models",
    "reel_cli.py new-project": "project new", "reel_cli.py list-projects": "project list",
    "reel_cli.py ingest": "project ingest", "reel_cli.py set-product": "project set-product",
    "reel_cli.py brand": "project brand", "reel_cli.py set-brand": "project set-brand",
    "reel_cli.py propose-copy": "project propose-copy", "reel_cli.py approve-copy": "project approve-copy",
    "reel_cli.py copy-diff": "project copy-diff", "reel_cli.py auto-reel": "project auto",
    "reel_cli.py project-render": "project render", "reel_cli.py make-shortcuts": "project shortcuts",
    "stills_cli.py presets": "poster presets", "stills_cli.py preview": "poster preview",
    "stills_cli.py render": "poster render", "stills_cli.py vary": "poster vary",
    "stills_cli.py export-ora": "poster export-ora", "stills_cli.py ora-check": "poster ora-check",
    "stills_cli.py edit": "image edit", "stills_cli.py still": "loop still",
    "studio generate_image": "image generate", "studio restyle_image": "image restyle",
    "studio upscale_image": "image upscale", "studio status": "apps status", "studio start": "apps start",
    "studio stop": "apps stop",
}


def env(**extra):
    e = {**os.environ, "PYTHONPATH": str(ROOT), "STUDIO_FAKE_SD": "1", "REEL_DEPTH": "stub", "REEL_TTS": "stub", **extra}
    if sys.platform == "linux" and "DISPLAY" not in e:
        e.setdefault("FUNK_GL_BACKEND", "egl")
    return e


def fb(*args, cwd=ROOT, **kw):
    return subprocess.run([sys.executable, "-m", "funkbox", *map(str, args)], cwd=cwd, env=env(**kw.pop("env", {})),
                          capture_output=True, text=True, timeout=300, **kw)


@pytest.mark.parametrize("old,new", sorted(OLD_TO_NEW.items()))
def test_every_old_verb_has_a_new_command_that_answers_help(old, new):
    r = fb(*new.split(), "--help")
    assert r.returncode == 0 and r.stdout.startswith("usage: funkbox " + new.split()[0]), (old, r.stderr)


def test_migration_doc_lists_every_old_verb():
    doc = (ROOT / "docs" / "migration-v4.md").read_text()
    for old in OLD_TO_NEW:
        script, _, verb = old.partition(" ")
        if script == "studio":
            continue                                    # the studio tools are in the tool table
        assert verb.split()[0] in doc and script in doc, old
    for area in ("loop", "reel", "project", "poster", "image", "apps"):
        assert f"funkbox {area}" in doc


def test_top_level_help_and_usage_errors():
    r = fb("--help")
    assert r.returncode == 0 and all(a in r.stdout for a in ("loop", "reel", "project", "poster", "image", "apps", "doctor"))
    assert fb().returncode == 2 and fb("nonsense").returncode == 2 and fb("poster").returncode == 2


def test_the_python_launcher_is_the_same_command():
    r = subprocess.run([sys.executable, str(ROOT / "funkbox.py"), "loop", "list"], capture_output=True, text=True, env=env())
    assert r.returncode == 0 and "liquid-disco" in r.stdout
    assert len((ROOT / "funkbox.py").read_text().splitlines()) <= 8


def test_real_runs_on_demo_assets(gl, tmp_path):
    import make_assets

    from funkbox import config
    work = config.funk_out() / "test_cli_smoke"
    demo = work / "demo"
    make_assets.make(demo, 270, 480)
    # loop render: a named look, 64 px, 0.5 s
    r = fb("loop", "render", "--preset", "acid-plasma", "--size", "64", "--seconds", "0.5", "--out-dir", work / "loop")
    assert r.returncode == 0, r.stderr
    assert (work / "loop" / "acid-plasma-s1.mp4").stat().st_size > 0 and "15 frames 64x64" in r.stdout
    # reel storyboard
    reel = demo / "small.reel.json"
    d = json.loads((demo / "demo.reel.json").read_text())
    d["settings"] = {"width": 270, "height": 480, "fps": 10, "safe_area": "reels"}
    reel.write_text(json.dumps(d))
    r = fb("reel", "storyboard", reel, "-o", work / "sb.png", "--tile", 100)
    assert r.returncode == 0 and Image.open(work / "sb.png").width >= 100, r.stderr
    # poster render (the stills demo)
    ex = ROOT / "examples" / "stills"
    subprocess.run([sys.executable, str(ex / "make_assets.py"), "--dest", str(work / "inputs")], check=True,
                   capture_output=True)
    doc = json.loads((ex / "glowmop_poster.poster.json").read_text())
    for lay in doc["layers"]:
        for k in ("path", "image"):
            if str(lay.get(k, "")).startswith("inputs/"):
                lay[k] = str(work / lay[k])
    doc["copy"] = str(ex / "glowmop.copy.json")
    pj = work / "g.poster.json"
    pj.write_text(json.dumps(doc))
    r = fb("poster", "render", pj, "--out", work / "posters", "--name", "g")
    assert r.returncode == 0 and Image.open(work / "posters" / "g.png").size == (1080, 1350), r.stderr
    # image edit on a demo photo
    photo = demo / "inputs" / "shots" / "shot1.png"
    r = fb("image", "edit", photo, "--ops", '[{"op":"flip","axis":"h"}]', "--out", work / "edit", "--name", "f")
    assert r.returncode == 0 and any((work / "edit").glob("*.png")), r.stderr
    # image generate on the stub, through the same group
    r = fb("image", "generate", "a red kettle", "--name", "kettle", "--seed", "3",
           env={"FUNK_INPUTS": str(work / "in"), "FUNK_OUT": str(work / "o2")})
    assert r.returncode == 0 and (work / "in" / "kettle.png").is_file(), r.stderr
    r = fb("image", "generate", "tall", "--aspect", "story", env={"FUNK_INPUTS": str(work / "in")})
    assert r.returncode == 1 and "NOT STARTED" in r.stdout                    # the slow-size warning, exit 1
    r = fb("apps", "status", env={"FUNK_INPUTS": str(work / "in")})
    assert r.returncode == 0 and "stub" in r.stdout and re.search(r'"fastsd"', r.stdout)

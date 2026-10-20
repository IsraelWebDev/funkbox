#!/usr/bin/env python3
"""funk v3.2 "stills": layered posters and photo edits.

    python stills_cli.py presets
    python stills_cli.py preview X.poster.json -o out/pv.png [--max-side 720]
    python stills_cli.py render X.poster.json [--out DIR] [--name N] [--project P]      (+ .ora if the poster exports it)
    python stills_cli.py vary X.poster.json --vary '{"seed":[1,2,3]}' [--out DIR] [--name N]
    python stills_cli.py edit photo.jpg --ops '[{"op":"crop","aspect":"1:1"}]' [--name N] [--casual] [--project P]
    python stills_cli.py still shaders/warp.frag [--t 0.25] [--preset square] [--name N]
    python stills_cli.py export-ora X.poster.json [--out DIR] [--name N]
    python stills_cli.py ora-check FILE.ora

``--ops`` / ``--vary`` take JSON text or the path of a JSON file. On Linux with no display set FUNK_GL_BACKEND=egl.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if sys.platform == "linux" and "DISPLAY" not in os.environ:
    os.environ.setdefault("FUNK_GL_BACKEND", "egl")

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _abs(p: str) -> str:
    q = Path(p)
    return str(q.resolve()) if q.exists() else p


def _json_arg(s: str):
    s = s.strip()
    if s[:1] in "[{":
        return json.loads(s)
    return json.loads(Path(s).read_text(encoding="utf-8-sig"))


def _print_paths(paths: dict) -> None:
    for k, v in paths.items():
        print(f"{k}: {v}")


def cmd_presets(a) -> int:
    from stills import schema
    from funkgl.recipe import BLEND_MODES
    for k, (w, h) in schema.PRESETS.items():
        print(f"{k}: {w}x{h}")
    print("layer types: " + ", ".join(schema.LAYER_TYPES))
    print("blend modes: " + ", ".join(BLEND_MODES))
    print("filters: " + ", ".join(schema.FILTER_KEYS))
    print("exports: " + ", ".join(schema.EXPORTS))
    return 0


def cmd_preview(a) -> int:
    from stills import api
    r, im = api.preview(_abs(a.poster), a.project or "", a.max_side)
    out = Path(a.output or "out/preview.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    print(api.describe(r))
    print(f"preview: {out}")
    return 0


def _doc(a):
    from stills import api
    return api.load_poster(_abs(a.poster), getattr(a, "project", "") or "")


def _outdir(a, doc):
    from stills import api
    if a.out:
        return a.out
    if doc.get("project"):
        from reel import project
        return project.project_dir(doc["project"]) / "out"
    return api.posters_dir()


def cmd_render(a, formats=None) -> int:
    from stills import api, render
    doc, base = _doc(a)
    r = render.render_document(doc, base)
    paths = render.write_outputs(r, _outdir(a, doc), a.name or r.doc.name, formats)
    print(api.describe(r))
    _print_paths(paths)
    return 0


def cmd_export_ora(a) -> int:
    return cmd_render(a, ["png", "ora"])


def cmd_vary(a) -> int:
    from stills import variations
    doc, base = _doc(a)
    res = variations.poster_variations(doc, _json_arg(a.vary), _outdir(a, doc), a.name, base)
    for lab, f in zip(res.labels, res.files):
        print(f"{lab}: {f['png']}")
    print(f"sheet: {res.sheet}")
    return 0


def cmd_edit(a) -> int:
    from stills import api, edit
    res = edit.edit_image(_abs(a.image), _json_arg(a.ops), a.name or "", project=a.project or None,
                          copy=a.copy or None, casual=a.casual, out_dir=a.out)
    print(api.describe(res.rendered))
    _print_paths(res.paths)
    return 0


def cmd_still(a) -> int:
    from stills import api
    paths, r = api.funk_still(_abs(a.source), a.t, a.preset, a.name or "", a.out)
    print(api.describe(r))
    _print_paths(paths)
    return 0


def cmd_ora_check(a) -> int:
    from stills import api
    print(api.ora_report(a.file))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="stills_cli.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("presets").set_defaults(fn=cmd_presets)
    p = sub.add_parser("preview")
    p.add_argument("poster"); p.add_argument("-o", "--output"); p.add_argument("--max-side", type=int, default=720)
    p.add_argument("--project")
    p.set_defaults(fn=cmd_preview)
    for name, fn in (("render", cmd_render), ("export-ora", cmd_export_ora)):
        p = sub.add_parser(name)
        p.add_argument("poster"); p.add_argument("--out"); p.add_argument("--name"); p.add_argument("--project")
        p.set_defaults(fn=fn)
    p = sub.add_parser("vary")
    p.add_argument("poster"); p.add_argument("--vary", required=True); p.add_argument("--out")
    p.add_argument("--name", default="var"); p.add_argument("--project")
    p.set_defaults(fn=cmd_vary)
    p = sub.add_parser("edit")
    p.add_argument("image"); p.add_argument("--ops", required=True); p.add_argument("--name"); p.add_argument("--out")
    p.add_argument("--project"); p.add_argument("--copy"); p.add_argument("--casual", action="store_true")
    p.set_defaults(fn=cmd_edit)
    p = sub.add_parser("still")
    p.add_argument("source"); p.add_argument("--t", type=float, default=0.25)
    p.add_argument("--preset", default="square"); p.add_argument("--name"); p.add_argument("--out")
    p.set_defaults(fn=cmd_still)
    p = sub.add_parser("ora-check")
    p.add_argument("file")
    p.set_defaults(fn=cmd_ora_check)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except Exception as e:  # noqa: BLE001
        from stills.schema import PosterError
        msg = "\n".join(e.errors) if isinstance(e, PosterError) else f"{type(e).__name__}: {e}"
        print(f"error: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

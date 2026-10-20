"""``funkbox poster`` and ``funkbox image edit``: layered posters and photo edits (no AI).

    funkbox poster presets
    funkbox poster preview X.poster.json -o out/pv.png [--max-side 720]
    funkbox poster render X.poster.json [--out DIR] [--name N] [--project P]      (+ .ora if the poster exports it)
    funkbox poster vary X.poster.json --vary '{"seed":[1,2,3]}' [--out DIR] [--name N]
    funkbox poster export-ora X.poster.json [--out DIR] [--name N]
    funkbox poster ora-check FILE.ora
    funkbox image edit photo.jpg --ops '[{"op":"crop","aspect":"1:1"}]' [--name N] [--casual] [--project P]

``--ops`` / ``--vary`` take JSON text or the path of a JSON file.
"""
from __future__ import annotations

import json
from pathlib import Path


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


def cmd_poster_presets(a) -> int:
    from funkbox.stills import schema
    from funkbox.gl.recipe import BLEND_MODES
    for k, (w, h) in schema.PRESETS.items():
        print(f"{k}: {w}x{h}")
    print("layer types: " + ", ".join(schema.LAYER_TYPES))
    print("blend modes: " + ", ".join(BLEND_MODES))
    print("filters: " + ", ".join(schema.FILTER_KEYS))
    print("exports: " + ", ".join(schema.EXPORTS))
    return 0


def cmd_poster_preview(a) -> int:
    from funkbox.stills import api
    r, im = api.preview(_abs(a.poster), a.project or "", a.max_side)
    out = Path(a.output or "out/preview.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    print(api.describe(r))
    print(f"preview: {out}")
    return 0


def _doc(a):
    from funkbox.stills import api
    return api.load_poster(_abs(a.poster), getattr(a, "project", "") or "")


def _outdir(a, doc):
    from funkbox.stills import api
    if a.out:
        return a.out
    if doc.get("project"):
        from funkbox.reel import project
        return project.project_dir(doc["project"]) / "out"
    return api.posters_dir()


def cmd_poster_render(a, formats=None) -> int:
    from funkbox.stills import api, render
    doc, base = _doc(a)
    r = render.render_document(doc, base)
    paths = render.write_outputs(r, _outdir(a, doc), a.name or r.doc.name, formats)
    print(api.describe(r))
    _print_paths(paths)
    return 0


def cmd_poster_export_ora(a) -> int:
    return cmd_poster_render(a, ["png", "ora"])


def cmd_poster_vary(a) -> int:
    from funkbox.stills import variations
    doc, base = _doc(a)
    res = variations.poster_variations(doc, _json_arg(a.vary), _outdir(a, doc), a.name, base)
    for lab, f in zip(res.labels, res.files):
        print(f"{lab}: {f['png']}")
    print(f"sheet: {res.sheet}")
    return 0


def cmd_image_edit(a) -> int:
    from funkbox.stills import api, edit
    res = edit.edit_image(_abs(a.image), _json_arg(a.ops), a.name or "", project=a.project or None,
                          copy=a.copy or None, casual=a.casual, out_dir=a.out)
    print(api.describe(res.rendered))
    _print_paths(res.paths)
    return 0


def cmd_poster_ora_check(a) -> int:
    from funkbox.stills import api
    print(api.ora_report(a.file))
    return 0


def add_cli(sub) -> None:
    from funkbox.studio.images import image_group
    ap = sub.add_parser("poster", help="layered posters: presets, preview, render, vary, export-ora")
    psub = ap.add_subparsers(dest="verb", required=True, metavar="{presets,preview,render,vary,export-ora,ora-check}")
    psub.add_parser("presets", help="canvas sizes, layer types, blend modes, filters").set_defaults(fn=cmd_poster_presets)
    p = psub.add_parser("preview", help="a small PNG of the poster")
    p.add_argument("poster"); p.add_argument("-o", "--output"); p.add_argument("--max-side", type=int, default=720)
    p.add_argument("--project")
    p.set_defaults(fn=cmd_poster_preview)
    for name, fn in (("render", cmd_poster_render), ("export-ora", cmd_poster_export_ora)):
        p = psub.add_parser(name, help="full-size PNG" + (" and layered .ora" if name == "export-ora" else ""))
        p.add_argument("poster"); p.add_argument("--out"); p.add_argument("--name"); p.add_argument("--project")
        p.set_defaults(fn=fn)
    p = psub.add_parser("vary", help="variations of a poster (a sheet plus one file each)")
    p.add_argument("poster"); p.add_argument("--vary", required=True); p.add_argument("--out")
    p.add_argument("--name", default="var"); p.add_argument("--project")
    p.set_defaults(fn=cmd_poster_vary)
    p = psub.add_parser("ora-check", help="validate an OpenRaster file")
    p.add_argument("file")
    p.set_defaults(fn=cmd_poster_ora_check)

    p = image_group(sub).add_parser("edit", help="non-AI photo edits: crop, flip, adjust, text, filters ...")
    p.add_argument("image"); p.add_argument("--ops", required=True); p.add_argument("--name"); p.add_argument("--out")
    p.add_argument("--project"); p.add_argument("--copy"); p.add_argument("--casual", action="store_true")
    p.set_defaults(fn=cmd_image_edit)

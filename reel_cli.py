#!/usr/bin/env python3
"""funk v3 "reel": shot list -> vertical video ad.

    python reel_cli.py validate X.reel.json
    python reel_cli.py storyboard X.reel.json -o out/sb.png
    python reel_cli.py still X.reel.json --shot s3 --t 0.5
    python reel_cli.py render X.reel.json [--draft] [--audit]
    python reel_cli.py setup-models
    python reel_cli.py cutout photo.jpg -o inputs/brand/bottle.png
    python reel_cli.py beats music.mp3

v3.1 projects (drop files in inbox/, get an ad):
    python reel_cli.py new-project "Glowmop"          then put files in inbox/ (see: make-shortcuts)
    python reel_cli.py ingest glowmop [--source FOLDER]
    python reel_cli.py set-product glowmop FILE       (only when ingest reports an ambiguous product)
    python reel_cli.py brand glowmop                  (+ set-brand glowmop --primary #c8102e ...)
    python reel_cli.py propose-copy glowmop --file lines.json   then: approve-copy glowmop --from-draft
    python reel_cli.py auto-reel glowmop [--length 20 --pace fast]
    python reel_cli.py project-render glowmop [--full]
    python reel_cli.py validate|storyboard|render --project glowmop
    python reel_cli.py make-shortcuts                 (desktop links "funk inbox" and "funk projects")

On Linux with no display set FUNK_GL_BACKEND=egl.
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
    """CLI paths are relative to the current folder (the fence is applied by the engine)."""
    q = Path(p)
    return str(q.resolve()) if q.exists() else p


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _reel_arg(a) -> str:
    """The reel path, or a project's <slug>.reel.json for --project."""
    if getattr(a, "project", None):
        from reel import project
        p = project.project_dir(a.project) / f"{a.project}.reel.json"
        if not p.is_file():
            raise ValueError(f"project {a.project!r} has no reel yet; run: reel_cli.py auto-reel {a.project}")
        return str(p)
    if not a.reel:
        raise ValueError("give a reel file or --project NAME")
    return _abs(a.reel)


def cmd_validate(a) -> int:
    from reel.validate import validate
    rep = validate(_reel_arg(a))
    print(rep.text())
    return 0 if rep.ok else 1


def cmd_still(a) -> int:
    from PIL import Image

    from funkgl.recipe import funk_out
    from reel import render
    plan = render.prepare(_reel_arg(a))
    st = plan.reel.settings
    w = a.width or st.width
    h = round(w * st.height / st.width)
    r = render.Renderer(plan.reel, plan.deck, plan.spans, width=w, height=h,
                        depth_mode="off" if a.no_depth else "auto")
    try:
        fr = r.still(a.shot, a.t)
    finally:
        r.release()
    out = Path(a.output) if a.output else funk_out() / f"still-{a.shot}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(fr).save(out)
    print(out)
    return 0


def cmd_storyboard(a) -> int:
    import time

    from funkgl.recipe import funk_out
    from reel import render
    from reel.storyboard import storyboard
    plan = render.prepare(_reel_arg(a))
    t0 = time.perf_counter()
    out = Path(a.output) if a.output else funk_out() / "storyboard.png"
    storyboard(plan, out, tile_w=a.tile)
    print(f"{out}  ({len(plan.spans)} tiles in {time.perf_counter() - t0:.1f}s)")
    return 0


def cmd_render(a) -> int:
    from reel import render
    src = _reel_arg(a)
    name = a.name or Path(src).name.replace(".reel.json", "").replace(".json", "")
    out_dir = a.out or (str(Path(src).parent / "out") if getattr(a, "project", None) else None)
    out = render.render_reel(src, name, out_dir=out_dir, draft=a.draft, audit=a.audit,
                             depth_mode=None, with_audio=not a.no_audio, gif=a.gif, progress=True)
    for k, v in out.paths.items():
        print(f"{k}: {v}")
    print(out.report_line())
    for w in out.warnings:
        print(f"warning: {w}")
    if a.audit:
        print(f"text audit: {'OK' if out.audit_ok else 'PROBLEMS (see the JSON)'}")
    return 0 if out.audit_ok is not False else 2


def cmd_setup_models(a) -> int:
    from reel import models
    models.setup_models(only=a.only, force=a.force)
    for k, v in models.status().items():
        print(f"{k}: {'ready' if v['ready'] else 'MISSING'}  licence: {v['license']}")
    return 0


def cmd_cutout(a) -> int:
    from reel.product import cutout
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cutout(a.photo, a.tolerance, a.feather).save(out)
    print(out)
    return 0


def cmd_beats(a) -> int:
    from reel import audio
    info = audio.detect_beats(a.music)
    print(f"tempo: {info.bpm:.1f} BPM, {len(info.beats)} beats in {info.duration:.1f}s")
    print("first beats (s): " + ", ".join(f"{b:.3f}" for b in info.beats[:12]))
    return 0


def cmd_approve_copy(a) -> int:
    """A PERSON at a terminal re-approves copy.json after a hand edit (or approves the pending draft with
    --from-draft). The agent must not run this: it is the user's own approval path."""
    from reel import approval, project
    if not sys.stdin.isatty() and not a.stdin:
        return _err("approve-copy must be typed by a person at a terminal. (If YOU are piping your own answer in, "
                    "add --stdin.)")
    slug = a.project
    project.project_dir(slug)
    if a.from_draft:
        data, deck = approval.read_draft(slug)
        sha = project.sha256_bytes(data)
        print(f"DRAFT copy for {slug!r}:\n\n{approval.listing(deck)}\n")
    else:
        state = project.copy_lock_error(slug)
        print(f"copy.json of {slug!r} as it stands now ({'LOCKED and intact' if state is None else state}):\n")
        print(approval.approved_listing(slug) + "\n")
    sys.stdout.write("Approve and lock exactly these lines? [y/N] ")
    sys.stdout.flush()
    ans = sys.stdin.readline().strip().lower()
    if ans not in ("y", "yes"):
        print("not approved; nothing changed")
        return 1
    out = approval.approve(slug, sha, "cli") if a.from_draft else approval.relock(slug, "cli")
    print(f"approved and locked ({out['lines']} lines, sha256 {out['copy_lock'][:12]}...)")
    return 0


def cmd_new_project(a) -> int:
    from reel import project
    meta = project.new_project(a.name)
    print(f"project {meta['slug']!r} created at {project.project_dir(meta['slug'])}")
    print(f"inbox (drop files here): {project.inbox_dir()}")
    print(f"next: reel_cli.py ingest {meta['slug']}")
    return 0


def cmd_list_projects(a) -> int:
    from reel import project
    ps = project.list_projects()
    for p in ps:
        print(f"{p['slug']}: {p['status']}; product {'yes' if p['has_product'] else 'no'}, copy "
              f"{'approved' if p['copy_approved'] else 'not approved'}, {p['shots']} shots, {p['clips']} clips, {p['music']} music")
    if not ps:
        print("no projects yet; run: reel_cli.py new-project NAME")
    print(f"inbox: {project.inbox_dir()}")
    return 0


def cmd_ingest(a) -> int:
    from reel import ingest
    res = ingest.ingest(a.project, a.source, move=not a.copy)
    print(res.text())
    return 0


def cmd_set_product(a) -> int:
    from reel import ingest
    pr = ingest.set_product(a.project, a.file)
    q = pr["cutout"]
    print(f"product set to {pr['file']}; cut-out score {q['score']} ({'OK' if q['ok'] else 'POOR'})")
    for p in q["problems"]:
        print(f"  problem: {p}")
    if q.get("advice"):
        print("  " + q["advice"])
    return 0


def cmd_brand(a) -> int:
    from reel import brand
    print(brand.brand_text(brand.brand_from_assets(a.project)))
    return 0


def cmd_set_brand(a) -> int:
    from reel import brand
    print(brand.brand_text(brand.set_brand(a.project, a.primary, a.secondary, a.text, a.font_caption, a.font_display)))
    return 0


def cmd_propose_copy(a) -> int:
    """Draft copy from a JSON file {id: {text, say?}} (or the full copy-deck shape). Draft only: nothing is
    approved until you run approve-copy."""
    from reel import approval
    raw = json.loads(Path(a.file).read_text(encoding="utf-8-sig"))
    lines, pron, bn = raw, None, None
    if isinstance(raw, dict) and "lines" in raw and isinstance(raw["lines"], dict):
        lines, pron, bn = raw["lines"], raw.get("pronounce"), raw.get("brand_name")
    res = approval.propose_copy(a.project, lines, pron, bn)
    print(approval.propose_text(res, a.project))
    print(f"\nto approve (you, at a terminal): reel_cli.py approve-copy {a.project} --from-draft")
    return 0


def cmd_copy_diff(a) -> int:
    from reel import approval
    print(approval.copy_diff(a.project))
    return 0


def cmd_auto_reel(a) -> int:
    from reel import autoreel, render
    from reel.storyboard import storyboard
    res = autoreel.auto_reel(a.project, a.length, a.style, a.pace, a.cta or None, a.music)
    print(res.text())
    plan = render.prepare(str(res.path))
    sb = res.path.parent / "out" / f"{a.project}-storyboard.png"
    storyboard(plan, sb, tile_w=270)
    print(f"storyboard: {sb}")
    return 0


def cmd_project_render(a) -> int:
    from reel import project, render
    d = project.project_dir(a.project)
    reel = d / f"{a.project}.reel.json"
    if not reel.is_file():
        return _err(f"project {a.project!r} has no reel yet; run: reel_cli.py auto-reel {a.project}")
    name = a.project if a.full else f"{a.project}-draft"
    out = render.render_reel(str(reel), name, out_dir=d / "out", draft=not a.full, audit=True, progress=True)
    for k, v in out.paths.items():
        print(f"{k}: {v}")
    print(out.report_line())
    print(f"text audit: {'OK' if out.audit_ok else 'PROBLEMS (see the JSON)'}")
    project.advance_status(a.project, "rendered")
    return 0 if out.audit_ok else 2


def cmd_make_shortcuts(a) -> int:
    from reel import shortcuts
    for line in shortcuts.make_shortcuts(dry_run=a.dry_run):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="reel_cli.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate", help="check a reel (copy ids, files, timing, caption fit)")
    p.add_argument("reel", nargs="?")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("still", help="render one frame of a shot")
    p.add_argument("reel", nargs="?")
    p.add_argument("--project")
    p.add_argument("--shot", required=True)
    p.add_argument("--t", type=float, default=0.5, help="seconds into the shot")
    p.add_argument("-o", "--output")
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--no-depth", action="store_true")
    p.set_defaults(fn=cmd_still)
    p = sub.add_parser("storyboard", help="contact sheet: one frame per shot + the end card")
    p.add_argument("reel", nargs="?")
    p.add_argument("--project")
    p.add_argument("-o", "--output")
    p.add_argument("--tile", type=int, default=270, help="tile width in px")
    p.set_defaults(fn=cmd_storyboard)
    p = sub.add_parser("render", help="render the reel to mp4")
    p.add_argument("reel", nargs="?")
    p.add_argument("--project")
    p.add_argument("--draft", action="store_true", help="540x960, 24 fps, no depth model")
    p.add_argument("--audit", action="store_true", help="also write <name>.text_audit.json")
    p.add_argument("--name")
    p.add_argument("--out", help="output folder (default FUNK_OUT / ./out)")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--gif", action="store_true")
    p.set_defaults(fn=cmd_render)
    p = sub.add_parser("setup-models", help="download + verify the depth and voice models (once)")
    p.add_argument("--only", nargs="*", choices=["depth", "voice"])
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_setup_models)
    p = sub.add_parser("cutout", help="alpha cut-out of a product photo on a plain background")
    p.add_argument("photo")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--tolerance", type=float, default=28.0)
    p.add_argument("--feather", type=float, default=22.0)
    p.set_defaults(fn=cmd_cutout)
    p = sub.add_parser("new-project", help="start a project (folder under projects/)")
    p.add_argument("name")
    p.set_defaults(fn=cmd_new_project)
    p = sub.add_parser("list-projects", help="list projects and their status")
    p.set_defaults(fn=cmd_list_projects)
    p = sub.add_parser("ingest", help="classify and file what is in the inbox (or --source FOLDER)")
    p.add_argument("project")
    p.add_argument("--source", help="a folder or file to copy from instead of the inbox")
    p.add_argument("--copy", action="store_true", help="copy instead of moving inbox files")
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("set-product", help="choose the product image")
    p.add_argument("project")
    p.add_argument("file")
    p.set_defaults(fn=cmd_set_product)
    p = sub.add_parser("brand", help="derive palette, text colour and fonts from the product/logo")
    p.add_argument("project")
    p.set_defaults(fn=cmd_brand)
    p = sub.add_parser("set-brand", help="override brand values")
    p.add_argument("project")
    for k in ("primary", "secondary", "text", "font-caption", "font-display"):
        p.add_argument("--" + k)
    p.set_defaults(fn=cmd_set_brand)
    p = sub.add_parser("propose-copy", help="write a DRAFT of the copy from a JSON file (not approved)")
    p.add_argument("project")
    p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_propose_copy)
    p = sub.add_parser("copy-diff", help="draft vs approved copy, line by line")
    p.add_argument("project")
    p.set_defaults(fn=cmd_copy_diff)
    p = sub.add_parser("auto-reel", help="build <project>.reel.json from the assets and the approved copy")
    p.add_argument("project")
    p.add_argument("--length", type=float, default=20.0)
    p.add_argument("--style", default="red-box")
    p.add_argument("--pace", choices=["fast", "calm"], default="fast")
    p.add_argument("--cta", nargs="*", help="copy ids for the end card (headline, sub, url)")
    p.add_argument("--music")
    p.set_defaults(fn=cmd_auto_reel)
    p = sub.add_parser("project-render", help="render a project's reel (draft unless --full)")
    p.add_argument("project")
    p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_project_render)
    p = sub.add_parser("make-shortcuts", help='desktop links "funk inbox" and "funk projects"')
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_make_shortcuts)
    p = sub.add_parser("approve-copy", help="(you, at a terminal) re-approve a hand-edited copy.json, or the draft")
    p.add_argument("project")
    p.add_argument("--from-draft", action="store_true", help="approve the pending copy.draft.json instead")
    p.add_argument("--stdin", action="store_true", help="read the y/N answer from a pipe (for scripts YOU run)")
    p.set_defaults(fn=cmd_approve_copy)
    p = sub.add_parser("beats", help="tempo and beat grid of a music file")
    p.add_argument("music")
    p.set_defaults(fn=cmd_beats)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except Exception as e:  # noqa: BLE001
        from reel.schema import ReelError
        if isinstance(e, ReelError):
            for m in e.errors:
                print(f"error: {m}", file=sys.stderr)
            return 1
        return _err(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())

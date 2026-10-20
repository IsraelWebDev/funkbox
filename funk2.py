#!/usr/bin/env python3
"""
funk2 - GPU shader engine CLI (v2). v1 (funk.py) is untouched and still works.

    python funk2.py info
    python funk2.py check  shaders/plasma.frag
    python funk2.py still  --recipe r.json --t 0.25 -o out/x.png
    python funk2.py sheet  --shader shaders/voronoi_pop.frag --seeds 1-9
    python funk2.py render --shader shaders/plasma.frag --palette vapor --post chroma=4 grain=0.03
    python funk2.py port-v1

Exit codes: 0 ok, 1 validation/compile failure (message on stderr), 2 bad usage.
Set FUNK_GL_BACKEND=egl on a headless Linux box.
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _expand(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        hits = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        out.extend(Path(h) for h in hits)
    return out


def cmd_info(args) -> int:
    from funkgl.context import gl_info
    from funkgl.shader import ShaderSource
    info = gl_info()
    print(f"GL_RENDERER: {info['GL_RENDERER']}\nGL_VERSION:  {info['GL_VERSION']}")
    print("shaders:")
    for f in sorted((HERE / "shaders").glob("*.frag")):
        try:
            ps = ShaderSource.from_file(f).params
            print(f"  {f.stem:<16} {len(ps)} params")
        except Exception as e:  # noqa: BLE001
            print(f"  {f.stem:<16} (unreadable: {e})")
    return 0


def cmd_check(args) -> int:
    from funkgl.engine import Engine  # noqa: F401  (forces GL import errors early)
    from funkgl.loopcheck import validate_loop
    from funkgl.recipe import Layer, Recipe2, Settings2
    from funkgl.shader import ShaderError, ShaderSource
    from funkgl.context import create_context
    files = _expand(args.files)
    if not files:
        _err("check: no files given")
        return 2
    failed = 0
    ctx = create_context()
    for f in files:
        try:
            src = ShaderSource.from_file(f)
        except (OSError, ShaderError) as e:
            _err(f"{f}: {e}")
            failed += 1
            continue
        res = src.compile(ctx)
        if not res.ok:
            failed += 1
            for e in res.errors:
                _err(f"{f}:{e['line'] or '?'}: {e['message']}")
            continue
        if src.no_loopcheck:
            print(f"{f}: compiles; loop check skipped (@noloopcheck)")
            continue
        rec = Recipe2(settings=Settings2(width=args.size, height=args.size, fps=args.fps,
                                         seconds=args.seconds, seed=args.seed),
                      layers=[Layer(source=src.source)])
        rep = validate_loop(rec, size=args.size)
        flag = "OK  " if rep.ok else "FAIL"
        print(f"{flag} {f}: {rep.reason} (bad_px={rep.bad_pixel_frac:.4f} seam={rep.seam_step:.2f} "
              f"typical={rep.typical_step:.2f})")
        if not rep.ok:
            failed += 1
            _err(f"{f}: {rep.reason}")
    return 1 if failed else 0


def cmd_port_v1(args) -> int:
    from funkgl.ports import PORTS
    d = HERE / "shaders"
    d.mkdir(exist_ok=True)
    for name, src in PORTS.items():
        (d / f"{name}.frag").write_text(src, encoding="utf-8", newline="\n")
        print(f"wrote shaders/{name}.frag")
    return 0


class UsageError(Exception):
    """Bad command-line usage (exit code 2)."""


def _parse_post(items: list[str] | None):
    from funkgl.recipe import PostPass
    from funkgl.shader import ShaderSource
    out = []
    for it in items or []:
        name, eq, val = it.partition("=")
        f = HERE / "funkgl" / "post" / f"{name}.frag"
        if not f.exists():
            names = sorted(x.stem for x in (HERE / "funkgl" / "post").glob("*.frag"))
            raise UsageError(f"unknown post pass {name!r}; have {names}")
        params: dict = {}
        if eq:
            declared = ShaderSource.from_file(f).params
            if not declared:
                raise UsageError(f"post pass {name!r} takes no value")
            try:
                v = float(val)
            except ValueError:
                raise UsageError(f"--post {it!r}: value must be a number") from None
            params[declared[0].name[2:]] = int(v) if v == int(v) and declared[0].type == "int" else v
        out.append(PostPass(name, params))
    return out


def _scale(w: int, h: int, size: int) -> tuple[int, int]:
    """Longest side = size, aspect preserved."""
    if w >= h:
        return size, max(2, round(size * h / w))
    return max(2, round(size * w / h)), size


def _build_recipe(args):
    from funkgl.recipe import Layer, Recipe2, Settings2, load_recipe
    if args.recipe and args.shader:
        raise UsageError("use --recipe or --shader, not both")
    if args.recipe:
        rec = load_recipe(args.recipe)
    elif args.shader:
        # The CLI user owns their files: read the .frag here (no folder fence) and inline it.
        sp = Path(args.shader)
        if not sp.is_file():
            raise UsageError(f"shader file not found: {args.shader}")
        rec = Recipe2(settings=Settings2(),
                      layers=[Layer(type="shader", shader=sp.as_posix(),
                                    source=sp.read_text(encoding="utf-8-sig"))])
        rec.base_dir = str(Path.cwd())
    else:
        raise UsageError("give --recipe r.json or --shader shaders/foo.frag")
    st = rec.settings
    if args.seed is not None:
        st.seed = args.seed
    if args.size:
        st.width, st.height = _scale(st.width, st.height, args.size)
    if getattr(args, "width", None):
        st.width = args.width
    if getattr(args, "height", None):
        st.height = args.height
    if getattr(args, "fps", None):
        st.fps = args.fps
    if getattr(args, "seconds", None):
        st.seconds = args.seconds
    if getattr(args, "audio", None):
        from funkgl.audio import wav_duration
        ap_ = Path(args.audio)
        if not ap_.is_file():
            raise UsageError(f"audio file not found: {args.audio}")
        rec.settings.audio = ap_.resolve().as_posix()
        if not getattr(args, "seconds", None):
            rec.settings.seconds = round(wav_duration(ap_), 3)
    if getattr(args, "palette", None):
        rec.palette = args.palette
    if getattr(args, "cycles", None) is not None:
        rec.cycles = args.cycles
    if getattr(args, "post", None):
        rec.post = _parse_post(args.post)
    if getattr(args, "text", None):
        from funkgl.recipe import TextSpec
        rec.text = TextSpec(text=args.text, wobble=6.0)
    if rec.palette not in __import__("funk").PALETTES:
        raise UsageError(f"unknown palette {rec.palette!r}; have {sorted(__import__('funk').PALETTES)}")
    return rec


def _default_out() -> Path:
    from funkgl.recipe import funk_out
    return funk_out()


def cmd_still(args) -> int:
    from PIL import Image
    from funkgl.engine import Engine
    rec = _build_recipe(args)
    out = Path(args.out) if args.out else _default_out() / "still.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    eng = Engine(rec.settings.width, rec.settings.height)
    try:
        arr = eng.render_at_t(rec, args.t)
    finally:
        eng.release()
    Image.fromarray(arr).save(out)
    print(out)
    return 0


def cmd_sheet(args) -> int:
    import copy
    import math

    import funk
    from PIL import Image, ImageDraw
    from funkgl.engine import Engine
    rec = _build_recipe(args)
    seeds = funk.parse_seeds(args.seeds)
    st = rec.settings
    tw, th = _scale(st.width, st.height, min(max(st.width, st.height), args.tile))
    out = Path(args.out) if args.out else _default_out() / "sheet.png"
    eng = Engine(tw, th)
    tiles = []
    try:
        for s in seeds:
            r = copy.deepcopy(rec)
            r.settings.seed = s
            if not args.keep_params:                 # resample params per seed (like v1 sheets)
                for lay in r.layers:
                    if lay.type in ("shader", "v1"):
                        lay.params = None
            im = Image.fromarray(eng.render_at_t(r, args.t))
            ImageDraw.Draw(im).text((8, 6), f"seed {s}", font=funk._font(max(12, eng.h // 18)),
                                    fill="#ffffff", stroke_width=2, stroke_fill="#000000")
            tiles.append(im)
    finally:
        eng.release()
    cols = max(1, min(args.cols, len(tiles)))
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (cols * eng.w, rows * eng.h), "#000")
    for k, im in enumerate(tiles):
        sheet.paste(im, ((k % cols) * eng.w, (k // cols) * eng.h))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(out)
    return 0


def cmd_render(args) -> int:
    from funkgl.encode import render_recipe
    from funkgl.loopcheck import validate_loop
    rec = _build_recipe(args)
    label = Path(args.shader).stem if args.shader else Path(args.recipe).stem
    name = args.name or f"{label}-s{rec.settings.seed}"
    if rec.settings.audio:
        _err("warning: audio set; the loop check is skipped. Audio loops are not seamless "
             "unless the music is.")
    if not args.no_check and not rec.settings.audio:
        rep = validate_loop(rec)
        if not rep.ok:
            _err(f"loop check failed: {rep.reason}")
            return 1
        if rep.reason.startswith("static"):
            _err(f"warning: {rep.reason}")
    res = render_recipe(rec, Path(args.out_dir) if args.out_dir else _default_out(), name,
                        mp4=not args.no_mp4, gif=args.gif and not args.no_gif, png=not args.no_png,
                        gif_width=args.gif_width)
    print(res.timing_line())
    for k, p in res.paths.items():
        print(f"  {k:<4} {p}")
    return 0


def _add_recipe_args(p: argparse.ArgumentParser, *, render: bool = False) -> None:
    p.add_argument("--recipe", help="v2 recipe .json (or a v1 sidecar .json)")
    p.add_argument("--shader", help="build a one-layer recipe from this .frag")
    p.add_argument("--seed", type=int)
    p.add_argument("--size", type=int, help="longest side in px (aspect preserved)")
    p.add_argument("--palette")
    p.add_argument("--cycles", type=int)
    p.add_argument("--post", nargs="*", metavar="NAME[=VAL]",
                   help="post passes, e.g. chroma=4 grain=0.03 scanlines")
    p.add_argument("--text")
    if render:
        p.add_argument("--fps", type=int)
        p.add_argument("--seconds", type=float)
        p.add_argument("--audio", help="WAV file: drives iAudio/iChannel3 and is muxed into the mp4")
        p.add_argument("--width", type=int)
        p.add_argument("--height", type=int)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="funk2", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="GL renderer/version and shader list").set_defaults(fn=cmd_info)
    c = sub.add_parser("check", help="compile + loop-check shaders")
    c.add_argument("files", nargs="+")
    c.add_argument("--size", type=int, default=192)
    c.add_argument("--fps", type=int, default=30)
    c.add_argument("--seconds", type=float, default=4.0)
    c.add_argument("--seed", type=int, default=1)
    c.set_defaults(fn=cmd_check)

    s = sub.add_parser("still", help="one frame to a PNG")
    _add_recipe_args(s, render=True)
    s.add_argument("--t", type=float, default=0.25, help="loop position 0..1")
    s.add_argument("-o", "--out")
    s.set_defaults(fn=cmd_still)

    h = sub.add_parser("sheet", help="contact sheet across seeds")
    _add_recipe_args(h)
    h.add_argument("--seeds", default="1-9")
    h.add_argument("--cols", type=int, default=3)
    h.add_argument("--t", type=float, default=0.25)
    h.add_argument("--tile", type=int, default=360, help="max tile side in px")
    h.add_argument("--keep-params", action="store_true",
                   help="keep the recipe's explicit params instead of resampling per seed")
    h.add_argument("-o", "--out")
    h.set_defaults(fn=cmd_sheet)

    r = sub.add_parser("render", help="render a seamless loop (mp4 + png + json; --gif for a GIF)")
    _add_recipe_args(r, render=True)
    r.add_argument("--name")
    r.add_argument("--out-dir", help="default: $FUNK_OUT or <funkbox>/out")
    r.add_argument("--no-mp4", action="store_true")
    r.add_argument("--gif", action="store_true",
                   help="also write a GIF (off by default: on busy/grainy loops it is ~10x slower than the mp4 and ~10 MB)")
    r.add_argument("--no-gif", action="store_true", help=argparse.SUPPRESS)  # v1 compat
    r.add_argument("--no-png", action="store_true")
    r.add_argument("--no-check", action="store_true", help="skip the automatic loop check")
    r.add_argument("--gif-width", type=int, default=400)
    r.set_defaults(fn=cmd_render)

    sub.add_parser("port-v1", help="write shaders/*.frag ports of the v1 effects").set_defaults(fn=cmd_port_v1)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130
    except UsageError as e:
        _err(f"usage error: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        _err(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

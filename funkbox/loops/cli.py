"""``funkbox loop``: seamless shader loops. list | render | still | sheet | check | write-shader.

    funkbox loop list
    funkbox loop render --preset liquid-disco --text FUNK --set kaleido=8 --seconds 4
    funkbox loop render --shader shaders/plasma.frag --palette vapor --post chroma=4 grain=0.03
    funkbox loop still  --recipe r.json --t 0.25 -o out/x.png
    funkbox loop sheet  --shader shaders/voronoi_pop.frag --seeds 1-9
    funkbox loop check  shaders/plasma.frag
    funkbox loop write-shader --ports                       (rewrite shaders/{plasma,moire,warp,tunnel,blobs,rings}.frag)

A loop comes from one of: ``--recipe r.json`` (v2 recipe, or a funk v1 sidecar), ``--shader x.frag``, or a
named look (``--preset NAME``, default liquid-disco, or ``--random`` for a whole random look from ``--seed``).
``--set key=value`` overrides a look (kaleido=8 palette=toxic chroma=6 ...).
Exit codes: 0 ok, 1 validation/compile failure (message on stderr), 2 bad usage.
"""
from __future__ import annotations

import argparse
import copy
import glob
import math
import sys
from pathlib import Path

from funkbox import config


class UsageError(Exception):
    """Bad command-line usage (exit code 2)."""


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _expand(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        hits = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        out.extend(Path(h) for h in hits)
    return out


def _shaders_dir() -> Path:
    return config.get().shaders_dir


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #
def cmd_list(args) -> int:
    from funkbox.gl.recipe import BLEND_MODES, LAYER_TYPES, PALETTES
    from funkbox.gl.shader import ShaderSource
    from funkbox.loops import presets
    print("PRESETS (loop render --preset NAME; --set key=value overrides)")
    for k in presets.LOOKS:
        print("  " + presets.describe(k))
    print("\nSHADERS (shaders/<name>.frag; params go in a recipe layer's `params`)")
    for f in sorted(_shaders_dir().glob("*.frag")):
        try:
            ps = ShaderSource.from_file(f).params
            print(f"  {f.stem:<16} {len(ps)} params")
        except Exception as e:  # noqa: BLE001
            print(f"  {f.stem:<16} (unreadable: {e})")
    print("\nPALETTES\n  " + "  ".join(PALETTES))
    print("\nBLEND MODES\n  " + "  ".join(BLEND_MODES))
    print("\nLAYER TYPES\n  " + "  ".join(LAYER_TYPES))
    print("\nLOOK KEYS (use with --set)\n  " + "  ".join(presets.LOOK_DEFAULTS))
    return 0


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def cmd_check(args) -> int:
    from funkbox.gl.context import create_context
    from funkbox.gl.loopcheck import validate_loop
    from funkbox.gl.recipe import Layer, Recipe2, Settings2
    from funkbox.gl.shader import ShaderError, ShaderSource
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


# --------------------------------------------------------------------------- #
# write-shader
# --------------------------------------------------------------------------- #
def cmd_write_shader(args) -> int:
    if args.ports:
        from funkbox.gl.ports import PORTS
        d = _shaders_dir()
        d.mkdir(parents=True, exist_ok=True)
        for name, src in PORTS.items():
            (d / f"{name}.frag").write_text(src, encoding="utf-8", newline="\n")
            print(f"wrote shaders/{name}.frag")
        return 0
    if not args.name or not args.file:
        raise UsageError("give NAME and --file SOURCE.frag (or --ports)")
    import re
    if not re.match(r"^[a-z0-9_\-]{1,40}$", args.name):
        raise UsageError(f"bad shader name {args.name!r}: use 1-40 chars of a-z 0-9 _ - (no paths, no dots)")
    source = Path(args.file).read_text(encoding="utf-8-sig")
    from funkbox.gl.context import create_context
    from funkbox.gl.loopcheck import validate_loop
    from funkbox.gl.recipe import Layer, Recipe2, Settings2
    from funkbox.gl.shader import ShaderSource
    src = ShaderSource(source, name=args.name)
    res = src.compile(create_context())
    if not res.ok:
        for e in res.errors:
            _err(f"{args.file}:{e['line'] or '?'}: {e['message']}")
        _err("not saved")
        return 1
    if not args.no_check and not src.no_loopcheck:
        rep = validate_loop(Recipe2(settings=Settings2(width=160, height=160, seed=1), palette="vapor",
                                    layers=[Layer(source=source)]), size=160)
        print(f"loop check: {'OK' if rep.ok else 'FAIL'} - {rep.reason}")
        if not rep.ok:
            _err("not saved: it does not loop (animate with iPhase * integer, or loopNoise); --no-check saves anyway")
            return 1
    dest = _shaders_dir() / f"{args.name}.frag"
    dest.parent.mkdir(parents=True, exist_ok=True)
    existed = dest.exists()
    dest.write_text(source, encoding="utf-8", newline="\n")
    print(f"{'overwrote' if existed else 'saved'} {dest}")
    return 0


# --------------------------------------------------------------------------- #
# building a recipe from the command line
# --------------------------------------------------------------------------- #
def _parse_post(items: list[str] | None):
    from funkbox.gl.recipe import PostPass
    from funkbox.gl.shader import ShaderSource
    post_dir = Path(__file__).resolve().parents[1] / "gl" / "post"
    out = []
    for it in items or []:
        name, eq, val = it.partition("=")
        f = post_dir / f"{name}.frag"
        if not f.exists():
            raise UsageError(f"unknown post pass {name!r}; have {sorted(x.stem for x in post_dir.glob('*.frag'))}")
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
    from funkbox.gl.recipe import PALETTES, Layer, Recipe2, Settings2, TextSpec, load_recipe
    from funkbox.loops import presets
    if args.recipe and args.shader:
        raise UsageError("use --recipe or --shader, not both")
    if args.set and (args.recipe or args.shader):
        raise UsageError("--set overrides a named look; with --recipe/--shader edit the recipe instead")
    seed = args.seed if args.seed is not None else 1
    if args.recipe:
        rec = load_recipe(args.recipe)
        label = Path(args.recipe).stem
    elif args.shader:
        # The CLI user owns their files: read the .frag here (no folder fence) and inline it.
        sp = Path(args.shader)
        if not sp.is_file():
            raise UsageError(f"shader file not found: {args.shader}")
        rec = Recipe2(settings=Settings2(),
                      layers=[Layer(type="shader", shader=sp.as_posix(),
                                    source=sp.read_text(encoding="utf-8-sig"))])
        rec.base_dir = str(Path.cwd())
        label = sp.stem
    else:
        d = presets.look(args.preset, seed, args.text or "", args.set, random=args.random)
        rec = presets.look_recipe(d)
        rec.settings = Settings2(seed=seed)
        label = "random" if args.random else args.preset
    st = rec.settings
    if args.seed is not None:
        st.seed = args.seed
    if args.size:
        st.width, st.height = _scale(st.width, st.height, args.size)
    for k in ("width", "height", "fps", "seconds"):
        if getattr(args, k, None):
            setattr(st, k, getattr(args, k))
    if getattr(args, "audio", None):
        from funkbox.gl.audio import wav_duration
        ap_ = Path(args.audio)
        if not ap_.is_file():
            raise UsageError(f"audio file not found: {args.audio}")
        st.audio = ap_.resolve().as_posix()
        if not getattr(args, "seconds", None):
            st.seconds = round(wav_duration(ap_), 3)
    if args.palette:
        rec.palette = args.palette
    if args.cycles is not None:
        rec.cycles = args.cycles
    if args.post:
        rec.post = _parse_post(args.post)
    if args.text and (args.recipe or args.shader):
        rec.text = TextSpec(text=args.text, wobble=6.0)
    if rec.palette not in PALETTES:
        raise UsageError(f"unknown palette {rec.palette!r}; have {sorted(PALETTES)}")
    return rec, label


def _default_out() -> Path:
    return config.get().out


def cmd_still(args) -> int:
    from PIL import Image

    from funkbox.gl.engine import Engine
    if args.canvas:                      # a poster-sized still of a shader (was stills_cli.py still)
        if not args.shader:
            raise UsageError("--canvas needs --shader SOURCE.frag")
        from funkbox.stills import api
        paths, r = api.funk_still(str(Path(args.shader).resolve()), args.t, args.canvas, args.name or "", args.out)
        print(api.describe(r))
        for k, v in paths.items():
            print(f"{k}: {v}")
        return 0
    rec, _ = _build_recipe(args)
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
    from PIL import Image, ImageDraw

    from funkbox.gl.engine import Engine
    from funkbox.gl.recipe import resample_params
    from funkbox.loops.presets import parse_seeds
    from funkbox.media import font
    rec, label = _build_recipe(args)
    seeds = parse_seeds(args.seeds)
    st = rec.settings
    tw, th = _scale(st.width, st.height, min(max(st.width, st.height), args.tile))
    out = Path(args.out) if args.out else _default_out() / f"{label}-sheet.png"
    eng = Engine(tw, th)
    tiles = []
    try:
        for s in seeds:
            r = copy.deepcopy(rec)
            if args.random and not (args.recipe or args.shader):   # a whole new look per seed
                from funkbox.loops import presets
                r = presets.preset_recipe(seed=s, text=args.text or "", random=True, width=tw, height=th)
            r.settings.seed = s
            if not args.keep_params:                 # resample params per seed
                resample_params(r)
            im = Image.fromarray(eng.render_at_t(r, args.t))
            ImageDraw.Draw(im).text((8, 6), f"seed {s}", font=font(max(12, eng.h // 18)),
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
    from funkbox.gl.encode import render_recipe
    from funkbox.gl.loopcheck import validate_loop
    rec, label = _build_recipe(args)
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
                        mp4=not args.no_mp4, gif=args.gif, png=not args.no_png, gif_width=args.gif_width)
    print(res.timing_line())
    for k, p in res.paths.items():
        print(f"  {k:<4} {p}")
    return 0


def _add_source_args(p: argparse.ArgumentParser, *, render: bool = False) -> None:
    p.add_argument("--preset", default="liquid-disco", help="a named look (see: loop list)")
    p.add_argument("--random", action="store_true", help="a whole random look from --seed")
    p.add_argument("--recipe", help="v2 recipe .json (or a funk v1 sidecar .json)")
    p.add_argument("--shader", help="build a one-layer recipe from this .frag")
    p.add_argument("--set", nargs="*", metavar="KEY=VAL",
                   help="override a look: kaleido=8 palette=toxic chroma=6 (presets only)")
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


def add_cli(sub) -> None:
    ap = sub.add_parser("loop", help="seamless shader loops: list, render, still, sheet, check, write-shader",
                        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lsub = ap.add_subparsers(dest="verb", required=True,
                             metavar="{list,render,still,sheet,check,write-shader}")
    lsub.add_parser("list", help="presets, shaders, palettes, blend modes").set_defaults(fn=cmd_list)

    c = lsub.add_parser("check", help="compile + loop-check shaders")
    c.add_argument("files", nargs="+")
    c.add_argument("--size", type=int, default=192)
    c.add_argument("--fps", type=int, default=30)
    c.add_argument("--seconds", type=float, default=4.0)
    c.add_argument("--seed", type=int, default=1)
    c.set_defaults(fn=cmd_check)

    s = lsub.add_parser("still", help="one frame to a PNG")
    _add_source_args(s, render=True)
    s.add_argument("--t", type=float, default=0.25, help="loop position 0..1")
    s.add_argument("-o", "--out", help="output file (with --canvas: output folder)")
    s.add_argument("--canvas", help="poster size preset (square, story, landscape ...): render --shader as a poster still")
    s.add_argument("--name", help="with --canvas: output name")
    s.set_defaults(fn=cmd_still)

    h = lsub.add_parser("sheet", help="contact sheet across seeds")
    _add_source_args(h)
    h.add_argument("--seeds", default="1-9")
    h.add_argument("--cols", type=int, default=3)
    h.add_argument("--t", type=float, default=0.25)
    h.add_argument("--tile", type=int, default=360, help="max tile side in px")
    h.add_argument("--keep-params", action="store_true",
                   help="keep the recipe's explicit params instead of resampling per seed")
    h.add_argument("-o", "--out")
    h.set_defaults(fn=cmd_sheet)

    r = lsub.add_parser("render", help="render a seamless loop (mp4 + png + json; --gif for a GIF)")
    _add_source_args(r, render=True)
    r.add_argument("--name")
    r.add_argument("--out-dir", help="default: $FUNK_OUT or <funkbox>/out")
    r.add_argument("--no-mp4", action="store_true")
    r.add_argument("--gif", action="store_true",
                   help="also write a GIF (off by default: on busy/grainy loops it is ~10x slower than the mp4 and ~10 MB)")
    r.add_argument("--no-gif", action="store_true", help=argparse.SUPPRESS)   # v1 name; GIF is off by default now
    r.add_argument("--no-png", action="store_true")
    r.add_argument("--no-check", action="store_true", help="skip the automatic loop check")
    r.add_argument("--gif-width", type=int, default=400)
    r.set_defaults(fn=cmd_render)

    w = lsub.add_parser("write-shader", help="compile, loop-check and save shaders/NAME.frag (or --ports)")
    w.add_argument("name", nargs="?")
    w.add_argument("--file", help="the GLSL source to save")
    w.add_argument("--no-check", action="store_true")
    w.add_argument("--ports", action="store_true", help="rewrite shaders/{plasma,moire,warp,tunnel,blobs,rings}.frag")
    w.set_defaults(fn=cmd_write_shader)

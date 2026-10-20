"""The one funkbox command line: ``python -m funkbox <area> <verb>`` (or ``python funkbox.py ...``).

    funkbox loop    list | render | still | sheet | check | write-shader
    funkbox reel    validate | storyboard | still | render | beats | cutout | setup-models
    funkbox project new | list | ingest | set-product | brand | set-brand | propose-copy | approve-copy
                    | copy-diff | auto | render | shortcuts
    funkbox poster  presets | preview | render | vary | export-ora | ora-check
    funkbox image   generate | restyle | upscale | edit
    funkbox apps    status | start | stop
    funkbox doctor  GL, ffmpeg, models, FastSD/GIMP paths and the resolved config
    funkbox serve   the MCP server (stdio)

Each area module exposes ``add_cli(subparsers)``; this file only wires them together. On Linux with no display
FUNK_GL_BACKEND defaults to egl. Exit codes: 0 ok, 1 failure (message on stderr), 2 bad usage.
"""
from __future__ import annotations

import argparse
import sys


def cmd_doctor(args) -> int:
    from funkbox import config
    cfg = config.get()
    print("CONFIG (env var, then funkbox.toml, then default)")
    for row in cfg.describe():
        print("  " + row)
    problems = 0

    def line(ok: bool | None, what: str, detail: str) -> None:
        nonlocal problems
        mark = "ok  " if ok else ("--  " if ok is None else "FAIL")
        problems += ok is False
        print(f"  {mark} {what:<12} {detail}")

    print("\nCHECKS")
    try:
        from funkbox.gl.context import gl_info
        info = gl_info()
        line(True, "OpenGL", f"{info['GL_RENDERER']} ({info['GL_VERSION']})")
    except Exception as e:  # noqa: BLE001
        line(False, "OpenGL", f"{type(e).__name__}: {e}  (headless Linux: FUNK_GL_BACKEND=egl)")
    try:
        from funkbox.media import ffmpeg_exe
        line(True, "ffmpeg", str(ffmpeg_exe()))
    except Exception as e:  # noqa: BLE001
        line(False, "ffmpeg", str(e))
    try:
        from funkbox.reel import models
        for key, v in models.status().items():
            line(True if v["ready"] else None, f"model {key}",
                 ("ready" if v["ready"] else "not installed (funkbox reel setup-models)") + f"; licence {v['license']}")
    except Exception as e:  # noqa: BLE001
        line(None, "models", f"{type(e).__name__}: {e}")
    line(True if cfg.fastsd_dir.exists() else None, "FastSD", f"{cfg.fastsd_dir}" + ("" if cfg.fastsd_dir.exists() else " (not installed)"))
    line(True if cfg.gimp_mcp_dir.exists() else None, "gimp-mcp", f"{cfg.gimp_mcp_dir}" + ("" if cfg.gimp_mcp_dir.exists() else " (not installed)"))
    line(True if cfg.gimp_exe else None, "GIMP", cfg.gimp_exe or "not found")
    line(None, "image tools", "stub (STUDIO_FAKE_SD)" if cfg.fake_sd else "FastSD")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="funkbox", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="area", required=True, metavar="AREA")
    from funkbox.loops import cli as loops_cli
    from funkbox.reel import cli as reel_cli
    from funkbox.stills import cli as stills_cli
    from funkbox.studio import apps, images
    loops_cli.add_cli(sub)
    reel_cli.add_cli(sub)
    stills_cli.add_cli(sub)          # poster + image edit
    images.add_cli(sub)              # image generate/restyle/upscale (same `image` group)
    apps.add_cli(sub)
    sub.add_parser("doctor", help="GL info, ffmpeg, models, FastSD/GIMP paths, config").set_defaults(fn=cmd_doctor)
    return ap


def _fail(e: Exception) -> int:
    from funkbox.gl.shader import ShaderError
    from funkbox.loops.cli import UsageError
    from funkbox.reel.schema import ReelError
    from funkbox.stills.schema import PosterError
    if isinstance(e, UsageError):
        print(f"usage error: {e}", file=sys.stderr)
        return 2
    if isinstance(e, (ReelError, PosterError)):
        for m in e.errors:
            print(f"error: {m}", file=sys.stderr)
        return 1
    if isinstance(e, ShaderError):
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:                       # argparse: --help is 0, bad usage is 2
        return int(e.code) if isinstance(e.code, int) else 2
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        return _fail(e)


if __name__ == "__main__":
    sys.exit(main())

"""MCP tools and CLI of the ``video`` area: look inside a video, find its cuts, trim it, burn in subtitles."""

from __future__ import annotations

from pathlib import Path


from funkbox.mcp_util import png as _png, safe
from funkbox.tool_options import CaptionStyle, SubtitleAnchor


@safe
def video_info(path: str) -> str:
    """Duration, size, frame rate, codec and whether a video file has audio."""
    from funkbox.video import clip

    return clip.info(path).text()


@safe
def video_scenes(path: str, threshold: float = 0.3, min_shot: float = 0.25, name: str = "") -> list:
    """Find the cuts in a video (trailers, gameplay, screen recordings) and show one frame per shot with its times.
    ``threshold`` 0.05..0.95: lower finds more cuts. Writes <out>/video/<name>.scenes.json, which lists every shot
    as a ready reel source {"video": ..., "trim": [start, end]}, and <name>.scenes.jpg. Pick shots from the sheet,
    then use their trims in a reel."""
    from funkbox.video import clip

    res = clip.scenes(path, threshold, min_shot)
    files = clip.write_scenes(res, None, name)
    out = [res.text() + "\n" + "\n".join(f"{k}: {v}" for k, v in files.items())]
    if res.sheet is not None:
        out.append(_png(res.sheet, 1600))
    return out


@safe
def video_trim(
    path: str,
    start: float | str,
    end: float | str | None = None,
    duration: float | str | None = None,
    name: str = "",
    accurate: bool = True,
) -> str:
    """Cut a piece of a video into a new file in <out>/video/ (the original is untouched, nothing is overwritten).
    Times are seconds or [hh:]mm:ss. ``accurate`` (default) re-encodes for exact frames; false copies the
    streams instantly from the nearest earlier keyframe."""
    from funkbox.video import clip

    p = clip.trim(path, start, end, None, name, accurate, duration)
    return clip.info(p).text() + f"\nfile: {p}"


@safe
def video_subtitles(
    path: str,
    cues: list | None = None,
    srt: str = "",
    style: CaptionStyle = "lower-third",
    anchor: SubtitleAnchor = "bottom",
    name: str = "",
    brand: dict | None = None,
) -> list:
    """Burn subtitles into a copy of a video. ``cues`` = [{"start": 1.2, "end": 3.0, "text": "..."}] or ``srt`` =
    a .srt/.vtt file. Drawn by funkbox's caption engine in a caption style (lower-third, red-box, bold-pop) with the
    brand's fonts and colours (brand = {primary, secondary, text, font_caption, font_display}); Hebrew reads right
    to left; text is never rewritten and a .text_audit.json proves each cue was drawn exactly."""
    from funkbox.video import clip

    res = clip.subtitles(path, cues, srt or None, style, anchor, None, name, brand)
    inf = clip.info(res.path)
    frame = clip.frame_at(res.path, min(inf.duration - 0.05, _first_cue_mid(cues, srt)), 720)
    return [f"{res.cues} cues drawn exactly\nfile: {res.path}\naudit: {res.audit}", _png(frame, 720)]


def _first_cue_mid(cues, srt) -> float:
    from funkbox.video import clip

    c = clip._cues(cues, srt or None)[0]
    return (c.start + c.end) / 2


TOOLS = [video_info, video_scenes, video_trim, video_subtitles]


def register(server) -> None:
    for fn in TOOLS:
        server.tool(name=fn.__name__)(fn)


# --------------------------------------------------------------------------- CLI
def _abs(p: str) -> str:
    q = Path(p)
    return str(q.resolve()) if q.exists() else p


def cmd_info(a) -> int:
    from funkbox.video import clip

    print(clip.info(_abs(a.video)).text())
    return 0


def cmd_scenes(a) -> int:
    from funkbox.video import clip

    res = clip.scenes(_abs(a.video), a.threshold, a.min_shot)
    print(res.text(limit=200))
    for k, v in clip.write_scenes(res, a.out, a.name or "").items():
        print(f"{k}: {v}")
    return 0


def cmd_trim(a) -> int:
    from funkbox.video import clip

    p = clip.trim(_abs(a.video), a.start, a.end, a.out, a.name or "", not a.fast, a.duration)
    print(clip.info(p).text())
    print(f"file: {p}")
    return 0


def cmd_subtitles(a) -> int:
    import json
    from funkbox.video import clip

    cues = json.loads(Path(a.cues).read_text(encoding="utf-8-sig")) if a.cues else None
    res = clip.subtitles(_abs(a.video), cues, _abs(a.srt) if a.srt else None, a.style, a.anchor, a.out, a.name or "")
    print(f"{res.cues} cues drawn exactly\nfile: {res.path}\naudit: {res.audit}")
    return 0


def add_cli(sub) -> None:
    ap = sub.add_parser("video", help="video files: info, scene cuts, trim, burned-in subtitles")
    vs = ap.add_subparsers(dest="verb", required=True, metavar="{info,scenes,trim,subtitles}")
    p = vs.add_parser("info", help="duration, size, fps, codec, audio")
    p.add_argument("video")
    p.set_defaults(fn=cmd_info)
    p = vs.add_parser("scenes", help="find the cuts; a sheet and reel-ready trims")
    p.add_argument("video")
    p.add_argument("--threshold", type=float, default=0.3)
    p.add_argument("--min-shot", type=float, default=0.25)
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_scenes)
    p = vs.add_parser("trim", help="cut a piece into a new file")
    p.add_argument("video")
    p.add_argument("start")
    p.add_argument("end", nargs="?")
    p.add_argument("--duration")
    p.add_argument("--fast", action="store_true", help="stream copy (keyframe start)")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_trim)
    p = vs.add_parser("subtitles", help="burn in subtitles from an .srt/.vtt or a cues JSON")
    p.add_argument("video")
    p.add_argument("--srt")
    p.add_argument("--cues", help="JSON file: [{start, end, text}]")
    p.add_argument("--style", default="lower-third")
    p.add_argument("--anchor", default="bottom")
    p.add_argument("--out")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_subtitles)

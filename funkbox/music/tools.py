"""MCP tool and CLI of the ``music`` area: scores rendered with General MIDI SoundFont instruments."""
from __future__ import annotations

from pathlib import Path

from funkbox.mcp_util import safe


def _out(name: str) -> Path:
    from funkbox.config import funk_out
    from funkbox.stills.render import NAME_RE, fence_out
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad output name {name!r}: use 1-60 of letters, digits, _ and -")
    d = fence_out(funk_out() / "music")
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.wav"


def _describe(m) -> str:
    return (f"{m.seconds:.1f}s at {m.bpm:g} BPM, {m.notes} notes, tracks: {', '.join(m.tracks)}; "
            f"instruments: {m.soundfont}" + "".join(f"\nwarning: {w}" for w in m.warnings))


@safe
def music_render(score: dict | str, name: str = "music") -> str:
    """Render a music score (JSON object or a .music.json path) to <out>/music/<name>.wav (48 kHz stereo) with real
    sampled General MIDI instruments (GeneralUser GS, downloaded once; "soundfont": "builtin" needs nothing).
    score = {bpm, meter, key, scale (major, minor, harmonic-minor, freygish, misheberach, dorian, ...), bars, swing,
    reverb, tracks: [{name, program (GM 0..127), octave, velocity, pan, gain_db, notes: "^1/8 ^2 ^3 | [D3 F#3 A3]/2 r/4"},
    {drums: true, pattern: {kick: "x...x...", snare: "....x...", hat: "x.x.x.x."}}]}. Notes are pitches (D4, Bb2),
    scale degrees (^1..^7, ' up and , down an octave), chords [..], r rests, ~ holds; /4 quarter, /8 eighth, . dots,
    ! accent, ? soft. A reel can use the .music.json directly as audio.music (its beat grid follows the bpm)."""
    from funkbox.music.score import render
    m = render(score, _out(name))
    return _describe(m) + f"\nwav: {m.path}"


TOOLS = [music_render]


def register(server) -> None:
    for fn in TOOLS:
        server.tool(name=fn.__name__)(fn)


# --------------------------------------------------------------------------- CLI
def cmd_render(a) -> int:
    from funkbox.music.score import render
    m = render(str(Path(a.score).resolve()) if Path(a.score).exists() else a.score,
               Path(a.output) if a.output else _out(a.name or Path(a.score).name.split(".")[0]))
    print(_describe(m))
    print(f"wav: {m.path}")
    return 0


def cmd_instruments(a) -> int:
    from funkbox.music.score import resolve_soundfont
    from funkbox.music.sf2 import load
    path, label, warns = resolve_soundfont(a.soundfont)
    for w in warns:
        print(f"warning: {w}")
    for bank, prog, nm in load(str(path)).presets():
        if bank in (0, 128) or a.all:
            print(f"{'drums ' if bank == 128 else ''}{bank:>3}:{prog:<3} {nm}")
    return 0


def add_cli(sub) -> None:
    ap = sub.add_parser("music", help="music from a short score with real General MIDI instruments")
    ms = ap.add_subparsers(dest="verb", required=True, metavar="{render,instruments}")
    p = ms.add_parser("render", help="render a .music.json score to a WAV")
    p.add_argument("score"); p.add_argument("-o", "--output"); p.add_argument("--name")
    p.set_defaults(fn=cmd_render)
    p = ms.add_parser("instruments", help="list the SoundFont's instruments (GM programs)")
    p.add_argument("--soundfont"); p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_instruments)

"""SS-1: voice-to-music balance of the voiceover demo, measured on the stems before loudness normalisation.

Run from the repo root with the reel models installed:
    python examples/voiceover_demo/measure_ratio.py <work-dir>

For each music gain it renders three stems with the reel's own mix graph (loudness normalisation off):
voice only, music only (no duck), and the ducked mix. It prints the integrated loudness (ebur128) of each.
Integrated loudness is gated, so silence between lines is ignored for the voice; the music stem has no gaps.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from funkbox.media import ffmpeg_exe
from funkbox.reel import audio as A
from funkbox.reel import render

DEMO = Path(__file__).resolve().parent
GAINS = [-14.0, -18.0, -22.0]  # -22 is the demo's current setting; -14 and -18 are the candidates


def integrated_lufs(path: Path) -> float:
    r = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostats", "-i", str(path), "-filter_complex", "ebur128", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    m = re.search(r"Integrated loudness:.*?I:\s+(-?[\d.]+) LUFS", r.stderr, re.S)
    if not m:
        raise RuntimeError(f"no loudness for {path}: {r.stderr[-400:]}")
    return float(m.group(1))


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    base = json.loads((DEMO / "demo.reel.json").read_text(encoding="utf-8"))
    print("gain_db  voice_LUFS  music_LUFS  music_minus_voice_LU  ducked_mix_LUFS  mix_minus_voice_LU")
    for gain in GAINS:
        reel = json.loads(json.dumps(base))
        reel["audio"]["music_gain_db"] = gain
        plan = render.prepare(reel, DEMO)
        a = plan.reel.audio
        music = A.music_file(plan.reel)
        clips = [
            (A.synthesize(plan.deck, sp.vo_id, a.voice, a.voice_speed), sp.vo_start)
            for sp in plan.spans
            if sp.kind == "shot" and sp.vo_id and sp.vo_start is not None
        ]
        total = plan.spans[-1].end
        tag = f"g{int(-gain)}"

        def stem(name, m, c):
            args, graph = A.mix_graph(plan, m, c, total, None)
            p = work / f"{tag}-{name}.wav"
            A._run_graph(args, graph, ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(p)], work)
            return p

        voice = integrated_lufs(stem("voice", None, clips))
        only = integrated_lufs(stem("music", music, []))
        mix = integrated_lufs(stem("mix", music, clips))
        print(
            f"{gain:7.1f}  {voice:10.1f}  {only:10.1f}  {only - voice:20.1f}  {mix:15.1f}  {mix - voice:17.1f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
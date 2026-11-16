#!/usr/bin/env bash
# Prepare a fresh Linux runner (the cloud promo run) for the day's promo. Idempotent: a ready runner passes through
# in seconds, so the run calls it every time instead of finding a missing library halfway through a render.
#
#   bash promo/setup_runner.sh            # Python requirements, and Mesa's EGL/GL if OpenGL cannot start
#   bash promo/setup_runner.sh --voice    # also the voiceover stack: espeak-ng, requirements-reel.txt,
#                                         # faster-whisper, the Kokoro voice and promo/hear.py's model
#
# Exits non-zero, naming what failed, if OpenGL or ffmpeg is still missing afterwards (or with --voice, the voice).
set -euo pipefail
cd "$(dirname "$0")/.."

voice=0
[ "${1:-}" = "--voice" ] && voice=1
export FUNK_GL_BACKEND="${FUNK_GL_BACKEND:-egl}"

pkgs=()
python - <<'EOF' || pkgs+=(libegl1 libegl-mesa0 libgl1-mesa-dri)
import ctypes.util, sys
sys.exit(0 if ctypes.util.find_library("EGL") and ctypes.util.find_library("GL") else 1)
EOF
if [ "$voice" = 1 ] && ! command -v espeak-ng >/dev/null; then pkgs+=(espeak-ng); fi
if [ "${#pkgs[@]}" -gt 0 ]; then
  echo "setup: installing ${pkgs[*]}"
  sudo=""
  [ "$(id -u)" != 0 ] && sudo="sudo"
  $sudo apt-get install -y -q "${pkgs[@]}" >/dev/null ||
    { $sudo apt-get update -q >/dev/null && $sudo apt-get install -y -q "${pkgs[@]}" >/dev/null; }
fi

# --ignore-installed steps over Debian's own Pillow and PyJWT; python -m pip puts the packages where scripts run.
python -m pip install -q --disable-pip-version-check --break-system-packages --ignore-installed -r requirements.txt

if [ "$voice" = 1 ]; then
  python -m pip install -q --disable-pip-version-check --break-system-packages -r requirements-reel.txt faster-whisper
  python -m funkbox reel setup-models --only kokoro
  python - <<'EOF'
import sys
sys.path.insert(0, "promo")
from hear import MODEL
from faster_whisper import WhisperModel

WhisperModel(MODEL, device="cpu", compute_type="int8")  # fetched once and cached, so hear.py starts at once
print(f"setup: speech-to-text model {MODEL} ready")
EOF
fi

report="$(python -m funkbox doctor 2>&1 || true)"
problems=0
for check in OpenGL ffmpeg; do
  line="$(grep -E "^\s+\S+\s+$check\s" <<<"$report" || true)"
  echo "setup: ${line:-$check: not reported}" | sed 's/  */ /g'
  grep -qE "^\s+ok\s" <<<"$line" || problems=1
done
if [ "$voice" = 1 ]; then
  mode="$(python -c 'from funkbox.reel import audio; print(audio.tts_mode("kokoro:af_heart"))')"
  echo "setup: voice $mode"
  [ "$mode" = "kokoro" ] || problems=1
fi
[ "$problems" = 0 ] && echo "setup: OK" || { echo "setup: NOT READY (see above)"; exit 1; }

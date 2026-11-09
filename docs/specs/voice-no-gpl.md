# Spec: voice without a GPL dependency on the hosted path (E3)

Status: findings and a decision request, merged as PR #4 (merge 3e985d6) from feat/e3-voice-licence (base 8ba9550). Not a feature. Done-when is NOT met: decision D7 is still open. Source: owner list item E3, decision D7. This note states no legal conclusion; the licence question goes to legal.

## Why this matters

Ads and voiceover need a voice on the hosted path. The default voice (Kokoro-82M, Apache-2.0) reaches a GPL-3.0 component through its English text-to-phoneme step. Until that changes, the lock for the voice stack cannot be described as free of GPL packages.

## Where the GPL packages come in

| Package (version in uv.lock) | Licence as declared | Where it enters | Role on the voice path |
|---|---|---|---|
| phonemizer 3.4.0 | GPLv3+ (installed METADATA: "GNU GENERAL PUBLIC LICENSE") | uv.lock line 1332; pulled in by kokoro-onnx (uv.lock line 886) | English text-to-phoneme, calls espeak-ng |
| espeakng-loader 0.2.4 | no licence in its METADATA; the wheel contains espeak-ng.dll and espeak-ng-data | uv.lock line 423; pulled in by kokoro-onnx (uv.lock line 880) | loads the espeak-ng library |
| espeak-ng (inside espeakng-loader) | GPL-3.0 or later (upstream repo) | via espeakng-loader | the phonemizer backend |
| kokoro-onnx 0.6.1 | MIT (upstream README) | pyproject.toml line 22 (reel extra); requirements-reel.txt line 3; uv.lock line 876 | Kokoro runtime; depends on espeakng-loader and phonemizer |
| piper-tts 1.8.0 | GPL-3.0-or-later (installed METADATA) | pyproject.toml line 23 (reel extra); requirements-reel.txt line 4; uv.lock line 1539 | fallback voice; its wheel contains espeakbridge.pyd and espeak-ng-data |

Code path: funkbox/reel/audio.py line 126 builds `Kokoro(str(model), str(voices))`, and line 167 calls `k.create(sent, voice=voice_id, speed=speed, lang=lang)` without `is_phonemes`, so the espeak phonemizer runs for every sentence. Piper is loaded at line 116 only when chosen as the fallback.

Notice text: THIRD_PARTY_NOTICES.md lines 14 to 17 list Piper, the Kokoro model, kokoro-onnx ("MIT (it uses phonemizer and espeak-ng, GPL-3.0)") and piper-tts, all as optional and not bundled. Lines 30 to 31 say the GPL components are "neither bundled nor linked into funkbox". The notice does not name phonemizer or espeakng-loader, and it does not say that installing the reel extra places the espeak-ng library in the environment. requirements-reel.txt line 3 and funkbox/reel/models.json (kokoro.license_note) say the same as the notice.

Hosted worker: this repository contains no hosted voice worker. A search of funkbox/, docs/, install.py, README.md and AGENTS.md for "hosted" and "worker" found only AGENTS.md line 7, which lists cloud-hosted rendering as out of scope. Whatever runs the hosted path lives elsewhere (the funkbox-online repo is the likely place) and was not opened for this note.

## What was checked

1. Read in this worktree at 8ba9550: pyproject.toml, requirements-reel.txt, THIRD_PARTY_NOTICES.md, uv.lock (the entries above), funkbox/reel/audio.py, funkbox/reel/models.json. All four text files use LF line endings.
2. Read-only pip list in C:\dev\funkbox\.venv: kokoro-onnx 0.6.1, phonemizer 3.4.0, espeakng-loader 0.2.4, piper-tts 1.8.0, onnxruntime 1.31.0, dlinfo 2.0.0, joblib 1.6.0, attrs 26.1.0, pathvalidate 3.3.1. No misaki and no spacy in site-packages. Nothing was installed.
3. Installed kokoro_onnx source (0.6.1). tokenizer.py imports espeakng_loader and phonemizer at module level. Tokenizer.__init__ loads the espeak-ng library with ctypes and sets EspeakWrapper's library. Tokenizer.phonemize calls phonemizer.phonemize. Kokoro._setup always builds a Tokenizer. Kokoro.create takes an is_phonemes flag (default False) that skips phonemize and accepts a phoneme string. The package does not import misaki.
4. Experiment, construction without espeak: with espeakng_loader.get_library_path pointed at a missing file, Kokoro(model, voices) raised RuntimeError ("Failed to load espeak-ng from fallback. Please install espeak-ng system wide."). So with 0.6.1 the espeak-ng library is required even when is_phonemes=True.
5. Experiment, current default path: one sentence ("Hello from the voice test.", voice af_heart, speed 1.0, en-us) rendered to a WAV: 35,328 samples at 24 kHz, 1.47 s, peak 0.373, RMS 0.080. This shows the current GPL path produces non-silent audio. The WAV is at %TEMP%\e3_probe_kokoro.wav, outside the repository and not committed. Model files were read from C:\dev\funkbox\models; their hashes were not re-checked.
6. Upstream pages (read through the fetch tool, which returns summaries; the LICENSE files themselves were not read):
   - kokoro-onnx README, https://github.com/thewh1teagle/kokoro-onnx: its License section lists "kokoro-onnx: MIT" and "kokoro model: Apache 2.0", and it recommends the misaki g2p package from v1.0. The page does not mention espeak.
   - phonemizer on PyPI, https://pypi.org/project/phonemizer/: licence "GNU General Public License v3 or later (GPLv3+)". It describes espeak as one of four backends and leaves the backend choice to the user.
   - espeakng-loader on PyPI, https://pypi.org/project/espeakng-loader/: "This package loads the espeak-ng shared library so it will be available for other libraries." No licence is shown.
   - espeak-ng, https://github.com/espeak-ng/espeak-ng: GPL version 3 or later.
   - misaki, https://github.com/hexgrad/misaki: Apache-2.0. English G2P runs with fallback=None (no espeak); espeak is an optional fallback for out-of-dictionary words.
   - misaki on PyPI, https://pypi.org/project/misaki/: licence "Apache Software License"; extras en, he, ja, ko, vi, zh. The dependency list was not shown in the fetched page.
   - Kokoro-82M model card, https://huggingface.co/hexgrad/Kokoro-82M: Apache-licensed; "Under the hood, kokoro uses misaki, a G2P library".
   - hexgrad/kokoro, https://github.com/hexgrad/kokoro: "Install espeak, used for English OOD fallback and some non-English languages" (from a code comment).
7. piper-tts installed METADATA: License GPL-3.0-or-later. Its installed package contains espeakbridge.pyd, phonemize_espeak.py and espeak-ng-data.

## What could not be checked, and why

- misaki's full dependency tree (including whether misaki[en] pulls in phonemizer-fork or another GPL package). The PyPI JSON endpoint refused the fetch, and misaki is not installed, so this was not checked.
- Whether misaki's output maps onto kokoro-onnx's vocabulary and produces intelligible speech. That needs misaki installed; not done.
- Construction without espeak-ng on the Kokoro side. Source reading and the failed construction show it is not possible in 0.6.1 unmodified. Whether a patched or thin runner works is untested.
- The espeakng-loader wheel's licence. Its METADATA declares none; the bundled espeak-ng is GPL upstream. Legal to confirm.
- The hosted worker: its code, lock, image and distribution model are not in this worktree.
- Any legal conclusion on hosting GPL code without distributing it, or on whether a worker image counts as distribution. Out of scope for this note; for legal.

## Options

A. Thin runner plus a non-espeak G2P (recommended engineering path). Write a small runner on onnxruntime (MIT) that feeds tokens, the voice style and speed to the Kokoro model, reading the vocabulary from the model metadata (kokoro-onnx's session.py already does this). Produce phonemes with misaki (Apache-2.0) using fallback=None. kokoro-onnx, phonemizer and espeakng-loader then drop out of the lock. Cost: a new Python dependency (misaki and its tree, still to be checked), a runner of roughly 50 lines, and a render test. Risk: output quality and vocabulary mapping are untested.

B. Another non-GPL English phonemizer. Not researched in this note. It would still need the same runner, since kokoro-onnx 0.6.1 cannot be used unmodified.

C. Keep the current stack and take a documented decision that the hosted worker is outside the GPL distribution question, after legal review. Needs the facts: what the worker ships (wheel, container image, or only served audio), who receives copies, and whether espeak-ng is present in any artefact that leaves the company.

D. Remove piper-tts from the hosted lock. piper-tts is GPL-3.0-or-later and is only a fallback. This does not fix kokoro-onnx, so it helps only with the piper part of the problem.

## Recommendation

Do C now, in parallel with A. Send legal the specific facts for the hosted worker (option C), and time-box a spike of A on a separate branch: a thin onnxruntime runner, misaki with fallback=None, and a test that renders one non-silent sentence. Do not describe the lock as GPL-free until the spike's lock check passes. Option B is not worth pursuing until misaki's tree is checked.

## Decisions for the owner

1. Approve the spike for option A (adds misaki, Apache-2.0 upstream, as a new dependency once its tree is checked), or choose another route.
2. Send the hosted-worker question to legal now, with the facts listed under option C. Who answers it: legal.
3. Decide whether piper-tts stays in the hosted lock.
4. Decide whether THIRD_PARTY_NOTICES.md should name phonemizer and espeakng-loader and say that installing the reel extra brings espeak-ng into the environment. This is a factual correction; it is not a licence conclusion.

## Release note (E3 not done)

"E3: the default voice (Kokoro-82M) still reaches espeak-ng (GPL-3.0) through phonemizer and espeakng-loader. The voice path is not GPL-free. The hosted-worker licence question is with legal."
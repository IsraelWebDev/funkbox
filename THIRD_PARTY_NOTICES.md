# Third-party notices

funkbox itself is MIT licensed. It bundles, downloads or invites you to install the following. "Bundled" means the
file is in this repository; "downloaded" means fetched on request; "optional" means not needed for the core.

| component | licence | how it is used |
|---|---|---|
| ashima/webgl-noise (simplex noise GLSL) | MIT | bundled, vendored in `funkbox/gl/noise.glsl` |
| Oswald (font) | SIL OFL 1.1 | bundled in `fonts/`, licence file `fonts/OFL-oswald.txt` |
| DM Serif Display (font) | SIL OFL 1.1 | bundled in `fonts/`, licence file `fonts/OFL-dmserifdisplay.txt` |
| Rubik Bold (font; Latin, Hebrew, Cyrillic; the default fallback) | SIL OFL 1.1 | bundled in `fonts/` (the 700 weight of the variable font), licence file `fonts/OFL-rubik.txt` |
| Fonts fetched by name (`"Rubik:800"`) | each font's own: SIL OFL 1.1, Apache 2.0 or Ubuntu Font Licence (only those Google Fonts folders are used) | downloaded on first use into `FUNK_HOME/fonts-cache/`, with the licence file next to each font |
| Depth-Anything-V2-Small (ONNX) | Apache-2.0 | downloaded by `funkbox reel setup-models` into `models/` |
| Piper voice `en_US-ljspeech-medium` | public-domain training data (LJ Speech); model released under MIT | downloaded by `funkbox reel setup-models` into `models/` |
| Kokoro-82M v1.0 (ONNX) and its voice pack | Apache-2.0 | the default voice, downloaded by `funkbox reel setup-models` into `models/` (hash-checked) |
| kokoro-onnx | MIT (it uses phonemizer and espeak-ng, GPL-3.0) | optional Python package (`requirements-reel.txt`), installed on request, not bundled |
| piper-tts | GPL-3.0 | optional Python package (`requirements-reel.txt`), installed on request, not bundled |
| FastSD CPU | MIT | optional, cloned by `install.py --with fastsd` into `vendor/` |
| SD-Turbo (`rupeshs/sd-turbo-openvino`) | Stability AI Community License | the default FastSD model, downloaded by FastSD on first use; see the note below |
| gimp-mcp | GPL-3.0 | optional, cloned by `install.py --with gimp` into `vendor/` |
| GIMP | GPL-3.0 | installed by you; funkbox never installs it |
| GeneralUser GS (General MIDI SoundFont) | GeneralUser GS License v2.0: free use in music, private or commercial, and in software | downloaded on first `music_render` from its GitHub repository into `FUNK_HOME/soundfonts/`, with its licence file; not bundled |
| HarfBuzz via uharfbuzz | MIT (HarfBuzz) / Apache-2.0 (uharfbuzz) | Python package from PyPI: text shaping for outlines (SVG/PDF export, typography) |
| fontTools | MIT | Python package from PyPI: font names, glyph checks, outlines |
| ReportLab | BSD | Python package from PyPI: PDF export |

Python dependencies (numpy, Pillow, imageio-ffmpeg, mcp, moderngl, fonttools, uharfbuzz, reportlab, onnxruntime) are installed from PyPI under their
own licences; imageio-ffmpeg downloads an ffmpeg binary (LGPL/GPL builds, see imageio-ffmpeg).
Because the GPL components are neither bundled nor linked into funkbox, funkbox stays MIT; if you redistribute a
build that includes them, their licences apply to that distribution.

**SD-Turbo / Stability AI Community License.** Images made with FastSD's default model (SD-Turbo) fall under the
Stability AI Community License: commercial use is free for individuals and organisations under US$1M in annual
revenue, and Stability AI asks commercial users to register with them (https://stability.ai/community-license-agreement).
Above that revenue you need a separate licence from Stability AI. funkbox does not bundle the model.

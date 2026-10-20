# AGENTS.md: installing and using funkbox

**Use funkbox when** a user wants, on their own machine and without cloud accounts: a seamless looping
video or GIF (shaders, a photo made psychedelic, a logo loop), a poster or social image with exact text, a
generated or restyled photo (FastSD), or a short 9:16 product video ad with captions, voiceover and music
whose wording must match what they approved. **Don't** use it for long videos, real video of people moving
(it animates stills; clips from a video generator can be dropped in), or cloud-hosted rendering.

For AI agents (and humans) setting up this repository. Everything runs locally; no accounts, no cloud,
no NVIDIA GPU needed (OpenGL 3.3 is enough; an Intel iGPU is fine).

Rules for agents:
- Do not install GIMP silently. `install.py` prints where to get it; tell the user.
- Do not edit any Claude config by hand: use `python install.py --register ...` (it backs up first and
  touches only the keys `funkbox` and `gimp`, and removes the v3 keys `funk`, `funk2`, `reel`, `studio`, `stills`).
- Never write `projects/<slug>/copy.json` yourself. Copy is proposed with `project_propose_copy` and approved by the
  user (`project_approve_copy`). See `skills/funk-studio/SKILL.md`.

## Windows (PowerShell)

```powershell
git clone https://github.com/IsraelWebDev/funkbox funkbox; cd funkbox
py -3 install.py --with reel --register desktop --shortcuts --yes
# optional: image generation and GIMP
py -3 install.py --with reel,fastsd,gimp --register desktop --shortcuts --yes
```
Quit Claude Desktop before `--register desktop`, start it again afterwards.

## macOS

```bash
git clone https://github.com/IsraelWebDev/funkbox funkbox && cd funkbox
python3 install.py --with reel --register desktop --shortcuts --yes
```

## Linux (headless or desktop)

```bash
sudo apt-get install -y libegl1 libgl1-mesa-dri ffmpeg      # software OpenGL, only if there is no GPU driver
git clone https://github.com/IsraelWebDev/funkbox funkbox && cd funkbox
FUNK_GL_BACKEND=egl python3 install.py --with reel --register code --yes
```

`--register code` runs `claude mcp add --scope user ...` for you (or prints the lines when the CLI is absent).
As a Claude Code plugin instead (needs `uv`): `/plugin marketplace add IsraelWebDev/funkbox`, then
`/plugin install funkbox@funkbox`. Plugin data lives in `~/funkbox-data/`. The heavy extras (`reel` models,
FastSD, GIMP) still need `install.py --with ...` from a clone.

The `funkbox` server includes posters, photo edits and OpenRaster export (see `docs/stills.md`) and needs no extra
install step. Upgrading from v3: run `python install.py --register desktop` again; it replaces the five old entries
with one (see `docs/migration-v4.md`). Check any time with `python -m funkbox doctor`.

Preview any plan first: `python install.py --dry-run --with reel,fastsd,gimp --register desktop --shortcuts`.

## Verify

```bash
.venv/bin/python -m pip install pytest        # Windows: .venv\Scripts\python.exe
FUNK_GL_BACKEND=egl .venv/bin/python -m pytest -q    # drop FUNK_GL_BACKEND where a real GPU exists
```
The installer ends with a one-second loop render as a self-test (`out/install-selftest/`).

## Uninstall

```bash
python install.py --unregister                 # removes only funkbox's entries from the Claude Desktop config
python install.py --unregister --register code # also: claude mcp remove ... for each of them
```
Then delete the folder (the venv, `vendor/`, `models/`, `out/`, `inbox/` and `projects/` all live inside it).
Config backups stay next to the config as `*.bak-<timestamp>`.

## Optional parts (`--with`)

| part | what it does |
|---|---|
| `reel` | installs onnxruntime and piper-tts, downloads the depth and voice models (`funkbox reel setup-models`) |
| `fastsd` | clones FastSD CPU into `vendor/fastsdcpu`, Python 3.11 venv via `uv`, CPU torch, copies the localhost-only launcher |
| `gimp` | clones `gimp-mcp` into `vendor/gimp-mcp` and runs `uv sync`; looks for GIMP 3 and prints the download if missing |

Paths of the optional apps are resolved from the environment (`FASTSD_DIR`, `GIMP_MCP_DIR`, `GIMP_EXE`), then
`funkbox.toml` (written by the installer, git-ignored), then `vendor/`.

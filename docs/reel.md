## v3 "reel": shot list to vertical ad

`reel_cli.py` / `reel_mcp.py` turn a JSON shot list (`*.reel.json`) plus a copy deck (`copy.json`) into a
9:16 ad: stills with Ken Burns or 2.5D parallax, your real product photo composited in (shadow + a
capped global Lab colour match, label pixels stay exact), boxed animated captions, a branded end card,
local voice-over (Piper), a music bed with ducking and loudness normalisation.

**Text is exact by construction.** Shots reference copy by ID; the validator rejects any literal-text key;
`render --audit` writes `<name>.text_audit.json` proving every drawn string equals its copy entry byte for
byte. The MCP server has no tool that writes the copy deck.

```
pip install -r requirements-reel.txt          # optional: onnxruntime + piper-tts
python reel_cli.py setup-models               # once: depth (Apache-2.0) + voice (public-domain data), hashes verified
python examples/reel_demo/make_assets.py      # synthetic demo assets
python reel_cli.py validate   examples/reel_demo/demo.reel.json
python reel_cli.py storyboard examples/reel_demo/demo.reel.json -o out/sb.png
python reel_cli.py still      examples/reel_demo/demo.reel.json --shot s3 --t 0.8
python reel_cli.py render     examples/reel_demo/demo.reel.json --draft --audit
python reel_cli.py cutout photo.jpg -o inputs/brand/bottle.png    # product on a plain background -> alpha PNG
python reel_cli.py beats music.mp3
```
Register the MCP server: `claude mcp add reel -e FUNK_OUT=C:\dev\funkbox\out -- <venv python> C:\dev\funkbox\reel_mcp.py`
(tools: `copy_lines`, `list_templates`, `validate`, `storyboard`, `still`, `render`). On Linux without a
display set `FUNK_GL_BACKEND=egl`. Without the depth model `parallax` degrades to Ken Burns; without a Piper
voice `voiceover: true` is an error (the sine-burst stub exists for tests only: `REEL_TTS=stub`).
Fonts in `fonts/` are SIL OFL (Oswald, DM Serif Display); licence texts alongside.

## v3.1 "projects": drop files in, get an ad

### Make an ad in 5 steps

1. **Drop your files** into `inbox/` (run `python reel_cli.py make-shortcuts` once for desktop links "funk inbox" and
   "funk projects"): a product photo (a plain background is fine), a logo, music, video clips, scene images. Or give
   Claude any folder path (external folders are copied, never moved).
2. **Start and ingest.** Tell Claude "make an ad for GLOWMOP". It calls `new_project`, then `ingest`, which files
   everything by type, cuts the product out (with a quality score; it says when to use GIMP instead) and shows a
   contact sheet. If two images could be the product it asks you, then calls `set_product`.
3. **Brand.** `brand_from_assets` picks the primary colour from the product label, the darkest colour as secondary,
   and white or near-black text (WCAG contrast >= 4.5), with your fonts if you dropped any. Override with `set_brand`.
4. **Approve the copy.** Claude drafts lines with `propose_copy` and shows you the numbered, exact listing. You approve
   it in a prompt in your client (MCP elicitation), or in chat if your client has no prompts. Only approved lines can
   reach the screen or the voice-over, and `copy.json` is then hash-locked: if it changes afterwards, `validate` and
   `render` refuse until you re-approve (`python reel_cli.py approve-copy NAME`, which asks you y/N at the terminal).
5. **Build and render.** `auto_reel` writes `projects/<name>/<name>.reel.json` (hook, body lines, branded end card,
   the product in every other shot, captions that dodge the product) and a storyboard to look at; then
   `project_render` (draft first). The text audit proves every drawn string equals your approved copy.

Name the copy lines `hook`, then any body lines, plus `end_head`, `end_sub` and `url` for the end card. Everything is
also available from the CLI (`python reel_cli.py new-project|ingest|brand|propose-copy|approve-copy|auto-reel|project-render`)
and every earlier tool (`validate`, `storyboard`, `still`, `render`) accepts `project=<name>` instead of a reel.
The reel file is plain JSON you can edit by hand; copy still comes only from `copy.json` by id.


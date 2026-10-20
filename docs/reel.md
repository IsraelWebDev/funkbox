## Reels and projects: shot list to vertical ad

`funkbox reel ...` / `funkbox project ...` and the `reel_*` / `project_*` MCP tools turn a JSON shot list (`*.reel.json`) plus a copy deck (`copy.json`) into a
9:16 ad: stills with Ken Burns or 2.5D parallax, your real product photo composited in (shadow + a
capped global Lab colour match, label pixels stay exact), boxed animated captions, a branded end card,
local voice-over (Piper), a music bed with ducking and loudness normalisation.

**Text is exact by construction.** Shots reference copy by ID; the validator rejects any literal-text key;
`reel render --audit` writes `<name>.text_audit.json` proving every drawn string equals its copy entry byte for
byte. The only tool that writes the copy deck is `project_approve_copy`, after your approval.

```
pip install -r requirements-reel.txt          # optional: onnxruntime + piper-tts
python -m funkbox reel setup-models           # once: depth (Apache-2.0) + voice (public-domain data), hashes verified
python examples/reel_demo/make_assets.py      # synthetic demo assets
python -m funkbox reel validate   examples/reel_demo/demo.reel.json
python -m funkbox reel storyboard examples/reel_demo/demo.reel.json -o out/sb.png
python -m funkbox reel still      examples/reel_demo/demo.reel.json --shot s3 --t 0.8
python -m funkbox reel render     examples/reel_demo/demo.reel.json --draft --audit
python -m funkbox reel cutout photo.jpg -o inputs/brand/bottle.png    # product on a plain background -> alpha PNG
python -m funkbox reel beats music.mp3
```
The MCP tools are `reel_copy_lines`, `reel_templates`, `reel_validate`, `reel_storyboard`, `reel_still`, `reel_render`
(registered with everything else by `python install.py --register ...`). On Linux without a
display set `FUNK_GL_BACKEND=egl`. Without the depth model `parallax` degrades to Ken Burns; without a Piper
voice `voiceover: true` is an error (the sine-burst stub exists for tests only: `REEL_TTS=stub`).
Fonts in `fonts/` are SIL OFL (Oswald, DM Serif Display); licence texts alongside.

## Projects: drop files in, get an ad

### Make an ad in 5 steps

1. **Drop your files** into `inbox/` (run `python -m funkbox project shortcuts` once for desktop links "funk inbox" and
   "funk projects"): a product photo (a plain background is fine), a logo, music, video clips, scene images. Or give
   Claude any folder path (external folders are copied, never moved).
2. **Start and ingest.** Tell Claude "make an ad for GLOWMOP". It calls `project_new`, then `project_ingest`, which files
   everything by type, cuts the product out (with a quality score; it says when to use GIMP instead) and shows a
   contact sheet. If two images could be the product it asks you, then calls `project_set_product`.
3. **Brand.** `project_brand` picks the primary colour from the product label, the darkest colour as secondary,
   and white or near-black text (WCAG contrast >= 4.5), with your fonts if you dropped any. Override with `project_set_brand`.
4. **Approve the copy.** Claude drafts lines with `project_propose_copy` and shows you the numbered, exact listing. You approve
   it in a prompt in your client (MCP elicitation), or in chat if your client has no prompts. Only approved lines can
   reach the screen or the voice-over, and `copy.json` is then hash-locked: if it changes afterwards, `validate` and
   `render` refuse until you re-approve (`python -m funkbox project approve-copy NAME`, which asks you y/N at the terminal).
5. **Build and render.** `project_auto_reel` writes `projects/<name>/<name>.reel.json` (hook, body lines, branded end card,
   the product in every other shot, captions that dodge the product) and a storyboard to look at; then
   `project_render` (draft first). The text audit proves every drawn string equals your approved copy.

Name the copy lines `hook`, then any body lines, plus `end_head`, `end_sub` and `url` for the end card. Everything is
also available from the CLI (`python -m funkbox project new|ingest|brand|propose-copy|approve-copy|auto|render`)
and the reel tools (`reel_validate`, `reel_storyboard`, `reel_still`, `reel_render`) accept `project=<name>` instead of a reel.
The reel file is plain JSON you can edit by hand; copy still comes only from `copy.json` by id.


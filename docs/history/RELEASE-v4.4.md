# funkbox 4.4.0

Everything since 4.2.0. The 4.3.0 number went into the captions commit without a tag or notes of its own, so its
changes are folded in here. The full test suite has 694 tests.

## Reels
- **Grid shot source**: `source.grid` lays out clips side by side (`cols` x `rows`, with a gap and a contain or cover fit
  per cell). `reveal: "sequence"` brings the cells in one after another, each dropping and fading in (stagger in
  seconds or `beats:N`). A cell can carry a label, a copy id drawn with the caption path and recorded in the text
  audit. Shot motion moves the whole grid; per-cell push-ins are not part of it.
- **Text audit `opaque_frame`**: the first frame a grid label is at full opacity, so it can be read. It sits next to
  `complete_frame`, which is the first frame the exact text is drawn and can be at low opacity during a fade.
- **Captions**: `auto` anchor, size and speed, and auto transitions. The `auto` box takes its colour from the shot.
- **`brand.from`** takes the palette from an image or an mp4 frame.

## Posters
- **`poster_fit_inside`** sizes and places words inside a bar or plate of the artwork; `poster_fit` gets `keep_inside`.
- **Lettering**: drawn letters stay exact text, with tuning to a reference.
- **`image_trace`** lifts lettering out of the artwork, returns its lines, and can write a starter poster with live text.
- **Shapes** fill only inside each shape's bounding box, about 6x faster for traced SVGs.

## Install and diagnostics
- `funkbox doctor` probes every OpenGL backend and falls back to software OpenGL, with a distro install hint.

## Promo
- A seeded tempo and a key image for each day's asset pack; owner seeds and genre trends, so the daily run can ride a
  style people are making; measured facts with F24 tracing and live text; `state.commit` pulls with `--autostash`.

## Development
- **Ruff 0.17.3** (pinned) is the formatter and linter. CI has a `lint` job that runs `ruff check` and
  `ruff format --check`. The lint covers syntax errors and pyflakes findings; style rules are not enforced yet.
- The repo was formatted in a single commit, so later diffs stay readable.
- `uv run` in the MCP test uses `--frozen`, so running the tests no longer rewrites `uv.lock`.
- Text layers on Pillow builds without libraqm (the Windows wheels) get the same "size raised to the minimum" warning
  as the others. The warning had been missing on that path.

## Known issues
- Captions fade in with the `pop` animation, and `opaque_frame` does not track that yet.
# Reel CPU benchmark (E4)

Status: spec. Written before the first version of the script, and revised after two independent reviews (see
"Revisions"). The table it produces lives in `docs/benchmarks/reel-cpu.md`. The owner decides D6 (hosted server
profile) from that table. This item changes no hosting.

## Goal

Measure how much CPU time a reel render costs per second of finished video, on a CPU-only host, at two portrait
sizes, with and without the depth model. The question is whether a 2 vCPU host with no GPU can render an ad reel
in a reasonable time.

Reference point from the owner: a 23-second reel took about 3.5 minutes on a laptop iGPU. That reel is not in the
repository (see Open questions).

## Method

- Input: `examples/reel_demo/demo.reel.json`, committed, with its synthetic assets under `examples/reel_demo/inputs/`
  (git-ignored; made by `examples/reel_demo/make_assets.py`). It has a music bed, voice-over on, and five parallax
  shots, so both the depth path and the Ken Burns fallback run.
- Render path: `funkbox.reel.render.render_reel` with `draft=False`. This runs the full path: frame drawing, voice
  lines (Kokoro or Piper), music mix, H.264 encode through ffmpeg, audio mux. The text audit is off. The reel's own
  fps (30) is kept.
- Size: the reel is changed in memory to the case size (`settings.width`, `settings.height`). Nothing on disk changes.
- Depth on: `depth_mode="model"` (Depth-Anything-V2-Small, ONNX, onnxruntime CPUExecutionProvider).
- Depth off: `depth_mode="off"`. Parallax shots fall back to Ken Burns, as the draft render does.
- The script sets the depth mode itself. If `REEL_DEPTH` is set, or `reel_depth` is set in `funkbox.toml`, the
  script refuses to start: exit code 2, and the message names `REEL_DEPTH`. Otherwise a config override could make a
  depth-off row read depth-on output, or measure the wrong thing.
- Depth cache: before each depth-on render the script deletes every `*.npy` file in `FUNK_OUT/cache/depth`. That is
  the depth cache funkbox uses for all reels, not a benchmark-only folder, so the deletion touches it too. The header
  names the folder. Depth-off renders do not read the cache. The clearing makes the ONNX inference part of the timing.
- Warm-up: one untimed render at the first timed size, at full settings. Its depth mode is `model` only when the
  depth model exists and a depth-on row will run (`--depth on` or `both`). A depth-off-only run warms with depth off,
  so it writes no depth cache at all. The warm-up warms imports, fonts, the GL context, the ONNX session and the
  voice-line cache (`FUNK_OUT/cache/tts`), so voice synthesis is not in the timed numbers. The one-time costs of the
  first size are therefore excluded. The second size gets no warm-up, so its one-time costs are in its timed numbers.
  The first render of a new voice line pays synthesis once.
- Warm-up failure: if the warm-up render raises, the script keeps going. The header's warm-up line reads
  `error <message>; the timed rows are not warmed`, the timed rows still run and are reported as usual, and the exit
  code is 1 even if every timed row is `ok`.
- Repeats: `--repeat N`, default 1. The row is the run with the median CPU total. With an even N it is the mean of
  the two middle runs. Every column comes from that row, so `cpu_python_s + cpu_children_s = cpu_total_s` holds.
- Models: read from `FUNK_MODELS` (or the default models folder), and the folder is printed in the header. The script
  never downloads anything.

## Sizes

540x960 and 720x1280 (9:16). Any other size is refused with exit code 2 before any setup or render.

## What is timed

- CPU: `time.process_time()` for this process (all its threads: Python, numpy, onnxruntime, moderngl), plus the CPU
  time of child processes (ffmpeg, used for video encode, audio mix and clip decode). On Windows the child time comes
  from `GetProcessTimes` on each child's handle. On Linux it is the change in `getrusage(RUSAGE_CHILDREN)`.
- Wall: `time.perf_counter()` around the same call.
- Output seconds: frames divided by fps, as `render_reel` reports it.
- Metric: `cpu_total_s / output_s` ("CPU seconds per second of output"). The wall ratio is printed beside it.
- Not timed: interpreter start, imports, model download, `setup-models`, the warm-up.

## Output format

stdout, CSV. Lines starting with `#` are header comments, in this order: the timer; the child-CPU method; host and
Python version; the onnxruntime version (`unknown` when the import fails); the GL renderer and whether it is software;
the models folder; the depth model path, or why it is unavailable; the depth cache folder that is cleared before each
depth-on render; the reel's fps; the warm-up (or its error); the MP4 folder. Then one header row and one row per
case. Columns, in this order:

`size, depth, output_s, cpu_python_s, cpu_children_s, cpu_total_s, cpu_per_output_s, wall_s, wall_per_output_s, status, note`

`cpu_per_output_s` is `cpu_total_s / output_s`, and `wall_per_output_s` is `wall_s / output_s`.

Status values:

- `ok`: every run read its child-process CPU.
- `partial` (Windows only): in at least one run, a child process could not be opened for CPU reading, because
  `OpenProcess` returned no handle. That child is missing from `cpu_children_s` and `cpu_total_s`, so both are lower
  than the true values. The note says that some runs had a child handle that could not be opened.
- `not run`: the case cannot run on this host. The note gives the reason. Numeric cells are empty.
- `error`: a timed render, or the depth-cache clear before it (for example a locked `.npy` file), raised an exception.
  The note gives it. Numeric cells are empty.

Exit codes: 0 when the warm-up worked and no row is `error` (`ok`, `partial` and `not run` all give 0); 1 when any row
is `error` or the warm-up failed; 2 for bad arguments, an unknown size, or a `REEL_DEPTH` / `reel_depth` override.

Rendered MP4s are written under `FUNK_OUT/bench-reel-cpu/`.

## Host caveat

- The laptop (zvislaptop, Windows, Intel iGPU present) is not the hosted box. The hosted box is 2 vCPU with no GPU.
  Its row stays "not measured" until someone runs the script there.
- Laptop numbers are a reference point. They are not scaled to the host, because core count, clock and BLAS
  threading all differ.
- Frame drawing uses OpenGL through moderngl. On a host with no GPU that is Mesa llvmpipe, which runs on the CPU, so
  it adds CPU time that the laptop's iGPU would not show in the same way. The GL renderer string is printed in the
  header of every run.
- The Linux child-CPU path (`RUSAGE_CHILDREN`) is written for the host but is not exercised by the laptop run.

## Test coverage

- Tests use stub renders only. They cover: the columns and CSV shape; the per-second columns; the median rule (odd
  and even counts); the `partial` status, including a failed child handle followed by a clean window; the error row
  and exit code 1; a depth-cache clear that raises (error row, CSV still printed, exit 1); the warm-up failure
  (CSV still printed, exit 1), including a multi-line error message kept on one header line;
  depth-off passed to the render with the cache cleared only before depth-on; the refusals (unknown size, `REEL_DEPTH`
  in the environment, `reel_depth` in a temporary `funkbox.toml`); the onnxruntime line in the header.
- Not tested in the repository: the `OpenProcess` call itself and the Windows `GetProcessTimes` read. The tests drive
  the same handler with a failed handle (0), so the `partial` logic is tested, but the Windows calls are verified
  only by reading the code and by the laptop run. The laptop run had no `partial` rows.

## Not in scope

No change to hosting, CI, `copy.json`, `install.py`, `projects/`, or the render code. The script calls the public
render path only.

## Revisions after review

- First review: REEL_DEPTH guard (refuse, exit 2) instead of clearing the override; onnxruntime line in the header;
  `partial` status for a failed child handle; the row is the median run by CPU total, not per-field medians; the depth
  cache folder is named in the header and here; the warm-up uses full settings at the first size; the models folder
  is in the header; the laptop run used `FUNK_MODELS=C:\dev\funkbox\models` (read only).
- Second review: a warm-up failure no longer stops the script. It is recorded in the header, the timed rows still run,
  and the exit code is 1. The warm-up depth mode follows `--depth`, so a depth-off-only run writes no depth cache. The
  `partial` note no longer counts processes, because runs can differ. The tests now cover the per-second columns, the
  `partial` path, the warm-up failure, the warm-up depth mode, and the `funkbox.toml` override. The `Setup` class is
  now a plain class, not a dataclass, so the tests need no global module registration.

- Third review: a depth-cache clear failure becomes an error row (it was outside the error handling). The funkbox.toml
  refusal is tested through `main()`, with `CONFIG_ROOT` pointed at a temporary folder. Header text is collapsed to
  one line. The onnxruntime header test reads the expected version from package metadata.

## Open questions

- The 23-second reel is not in the repository. The benchmark uses the demo reel, whose measured output length is
  reported in the table. A matching reel would need to be supplied by the owner.
- The depth-on and depth-off difference includes parallax drawing as well as the ONNX inference. Splitting them would
  need one more measurement.
- The host run should use `--repeat 3` on an idle machine, and record its own Python and onnxruntime versions.
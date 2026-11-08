# Reel CPU benchmark (E4)

Status: spec, written before the benchmark script. The table it produces lives in `docs/benchmarks/reel-cpu.md`.
The owner decides D6 (hosted server profile) from that table. This item changes no hosting.

## Goal

Measure how much CPU time a reel render costs per second of finished video, on a CPU-only host, at two portrait
sizes, with and without the depth model. The question is whether a 2 vCPU host with no GPU can render an ad reel
in a reasonable time.

Reference point from the owner: a 23-second reel took about 3.5 minutes on a laptop iGPU. That reel is not in the
repository (see Open questions).

## Method

- Input: `examples/reel_demo/demo.reel.json`, committed, with its synthetic assets under `examples/reel_demo/inputs/`.
  It has a music bed, voice-over on, and parallax shots, so both the depth path and the Ken Burns fallback run.
- Render path: `funkbox.reel.render.render_reel` with `draft=False`. This runs the full path: frame drawing, voice
  lines (Kokoro or Piper), music mix, H.264 encode through ffmpeg, audio mux. The text audit is off. The reel's own
  fps (30) is kept.
- Size: the reel is changed in memory to the case size (`settings.width`, `settings.height`). Nothing on disk changes.
- Depth on: `depth_mode="model"` (Depth-Anything-V2-Small, ONNX, onnxruntime CPUExecutionProvider).
- Depth off: `depth_mode="off"`. Parallax shots fall back to Ken Burns, as the draft render does.
- Depth-on runs start with the depth cache emptied (`FUNK_OUT/cache/depth`), so ONNX inference is inside the timing.
- Warm-up: one untimed draft render before the timed rows (depth on when the model is available), so imports, fonts,
  the GL context, the ONNX session and the voice-line cache (`FUNK_OUT/cache/tts`) are warm. Voice synthesis is
  therefore not in the timed numbers. The first render of a new voice line pays it once.
- Repeats: `--repeat N`, default 1. With N above 1 the row reports the median, and the note lists the runs.
- Model files come from `FUNK_MODELS`. The script never downloads anything.

## Sizes

540x960 and 720x1280 (9:16). Any other size is refused with exit code 2 before anything is rendered.

## What is timed

- CPU: `time.process_time()` for this process (all its threads: Python, numpy, onnxruntime, moderngl), plus the CPU
  time of child processes (ffmpeg, used for video encode, audio mix and clip decode). On Windows the child time comes
  from `GetProcessTimes` on each child's handle. On Linux it is the change in `getrusage(RUSAGE_CHILDREN)`.
- Wall: `time.perf_counter()` around the same call.
- Output seconds: frames divided by fps, as `render_reel` reports it.
- Metric: `cpu_total_s / output_s` ("CPU seconds per second of output"). The wall ratio is printed beside it.
- Not timed: interpreter start, imports, model download, `setup-models`.

## Output format

stdout, CSV. Lines starting with `#` are header comments: the timer, the GL renderer, host and Python versions.
Then one header row and one row per case. Columns, in this order:

`size, depth, output_s, cpu_python_s, cpu_children_s, cpu_total_s, cpu_per_output_s, wall_s, wall_per_output_s, status, note`

A case that cannot run keeps its row, with `status` = `not run` and the reason in `note`. Its numeric cells are empty.
The rendered MP4s are written under `FUNK_OUT/bench-reel-cpu/`.

## Host caveat

- The laptop (zvislaptop, Windows, Intel iGPU present) is not the hosted box. The hosted box is 2 vCPU with no GPU.
  Its row stays "not measured" until someone runs the script there.
- Laptop numbers are a reference point. They are not scaled to the host, because core count, clock and BLAS
  threading all differ.
- Frame drawing uses OpenGL through moderngl. On a host with no GPU that is Mesa llvmpipe, which runs on the CPU, so
  it adds CPU time that the laptop's iGPU would not show in the same way. The GL renderer string is printed in the
  header of every run.
- The Linux child-CPU path (`RUSAGE_CHILDREN`) is written for the host but is not exercised by the laptop run.

## Not in scope

No change to hosting, CI, `copy.json`, `install.py`, `projects/`, or the render code. The script calls the public
render path only.

## Open questions

- The 23-second reel is not in the repository. The benchmark uses the demo reel, whose measured output length is
  reported in the table. A matching reel would need to be supplied by the owner.
- The host's Python and onnxruntime versions are not known here. They should be recorded on the host run.
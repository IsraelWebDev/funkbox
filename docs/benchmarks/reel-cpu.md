# Reel CPU benchmark: results

Measured by `tools/bench_reel_cpu.py`. Method, sizes and columns: `docs/specs/reel-cpu-bench.md`.
The owner decides D6 (hosted server profile) from this table. This file changes no hosting.

Reel: `examples/reel_demo/demo.reel.json`. Output is 24.50 s at 30 fps, with voice-over, a music bed and five
parallax shots. It is not the 23-second reel from the owner's note.

## Table

Each laptop row is the median of 2 timed renders. CPU seconds are the process's own time plus its child processes
(ffmpeg). Run on 2026-11-08, local time 03:07 to 03:18.

| Host | Size | Depth | Output (s) | CPU, this process (s) | CPU, child processes / ffmpeg (s) | CPU total (s) | CPU s per output s | Wall (s) | Wall s per output s |
|---|---|---|---|---|---|---|---|---|---|
| laptop (zvislaptop, iGPU present) | 540x960 | on | 24.50 | 89.37 | 35.04 | 124.41 | 5.08 | 54.31 | 2.22 |
| laptop (zvislaptop, iGPU present) | 540x960 | off | 24.50 | 34.10 | 32.06 | 66.16 | 2.70 | 42.69 | 1.74 |
| laptop (zvislaptop, iGPU present) | 720x1280 | on | 24.50 | 117.84 | 49.10 | 166.94 | 6.81 | 84.72 | 3.46 |
| laptop (zvislaptop, iGPU present) | 720x1280 | off | 24.50 | 70.73 | 56.60 | 127.33 | 5.20 | 82.75 | 3.38 |
| 2 vCPU hosted box: not measured | 540x960 | on | not measured | not measured | not measured | not measured | not measured | not measured | not measured |
| 2 vCPU hosted box: not measured | 540x960 | off | not measured | not measured | not measured | not measured | not measured | not measured | not measured |
| 2 vCPU hosted box: not measured | 720x1280 | on | not measured | not measured | not measured | not measured | not measured | not measured | not measured |
| 2 vCPU hosted box: not measured | 720x1280 | off | not measured | not measured | not measured | not measured | not measured | not measured | not measured |

No laptop case was "not run": the depth model was present, so both depth settings ran.

## Notes

- Per-run CPU totals, for spread: 540x960 on 131.1 and 117.7 s; 540x960 off 71.7 and 60.7 s; 720x1280 on 170.1 and
  163.8 s; 720x1280 off 130.7 and 123.9 s.
- "Depth on" is more than the model. With depth on, the five parallax shots use depth-based 2.5D motion. With depth
  off, the script falls back to Ken Burns for them, and prints a warning for each. The on/off gap therefore includes
  the parallax drawing and the ONNX inference. The two are not separated here.
- Child processes are ffmpeg: clip decode, audio mix and the H.264 encode. On the laptop they are 28 to 49 percent of
  the CPU total. The script adds them, because the process's own timer does not see them.
- Voice lines are cached by an untimed warm-up render, so Kokoro synthesis is not in these numbers. A new voice line
  pays it once. Depth-on rows start with the depth cache emptied, so their inference is timed.
- Contention: another agent's pytest runs were active on this laptop during the benchmark (process start times
  03:06 and 03:09 local). Wall times are inflated by that. CPU times are less affected, but not immune to shared
  caches and clock changes. Rerun on an idle machine before using the wall figures for planning.
- OpenGL: the laptop's frame drawing ran on Intel Iris Xe Graphics, with `software=False`. On a host with no GPU,
  OpenGL falls back to Mesa llvmpipe, which runs on the CPU. The host is therefore likely to cost more on the
  parallax shots. That is not measured here.
- For scale only: the owner's 23-second reel took about 3.5 minutes on the laptop iGPU, which is about 9 wall
  seconds per output second (210 / 23). This demo reel took 1.7 to 3.5 wall seconds per output second on the same
  laptop. The reels differ, so the table does not predict the 23-second reel.
- The Linux child-CPU path (`getrusage(RUSAGE_CHILDREN)`) is written for the host but was not run in this benchmark.
  The Windows path was used here.

## Reproduce on the host

```
python examples/reel_demo/make_assets.py          # once: the demo's input files are git-ignored
python -m funkbox reel setup-models               # once: depth model and voices
python tools/bench_reel_cpu.py --repeat 2         # add FUNK_MODELS=<models folder> if the models live elsewhere
```

Record the host's Python and onnxruntime versions with the row.

## Raw output (laptop run)

```
# timer: cpu = time.process_time() (this process, all threads) + child processes (ffmpeg); wall = time.perf_counter()
# child cpu: GetProcessTimes on each child process handle (Windows)
# host: ZvisLaptop Windows-11-10.0.26300-SP0; python 3.12.13
# gl: Intel(R) Iris(R) Xe Graphics (software=False)
# depth model: available at C:\dev\funkbox\models\depth_anything_v2_small.onnx
# reel: examples/reel_demo/demo.reel.json at 30 fps, voice-over and music on
# mp4s: C:\dev\funkbox-e4\out\bench-reel-cpu
size,depth,output_s,cpu_python_s,cpu_children_s,cpu_total_s,cpu_per_output_s,wall_s,wall_per_output_s,status,note
540x960,on,24.50,89.37,35.04,124.41,5.08,54.31,2.22,ok,"median of 2 runs, each field separately; cpu totals 131.1, 117.7 s"
540x960,off,24.50,34.10,32.06,66.16,2.70,42.69,1.74,ok,"median of 2 runs, each field separately; cpu totals 71.7, 60.7 s"
720x1280,on,24.50,117.84,49.10,166.94,6.81,84.72,3.46,ok,"median of 2 runs, each field separately; cpu totals 170.1, 163.8 s"
720x1280,off,24.50,70.73,56.60,127.33,5.20,82.75,3.38,ok,"median of 2 runs, each field separately; cpu totals 130.7, 123.9 s"
```
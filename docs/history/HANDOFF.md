You are implementing **funk v2**, a GPU shader engine for an existing procedural-art tool.

Working directory: the `funkbox` folder that contains this file, `PLAN.md`, `funk.py`,
`funk_mcp.py` and `README.md`.

Do this:
1. Read `PLAN.md` completely before writing any code. It is the spec, and it overrides your
   own preferences.
2. Read `funk.py` and `funk_mcp.py`. You will import from `funk.py` (PALETTES, render_frame,
   ffmpeg_exe, Recipe and friends) but MUST NOT modify either file.
3. Execute milestones M0 → M7 in order. For each milestone:
   - implement it;
   - run its acceptance check;
   - fix until green;
   - `git commit -m "v2 M<n>: <title>"`.
4. GL backend: on Linux with no display, set `FUNK_GL_BACKEND=egl` (EGL + Mesa llvmpipe
   works there). On Windows, leave it unset so the default backend is used. Keep test
   renders small (≤ 256px, ≤ 30 frames).
5. When a milestone produces images, look at them (read the PNG) at least once to sanity-check
   orientation and that the output isn't blank.
6. Follow PLAN.md section 7 ("When stuck") exactly. Never loosen thresholds or skip tests
   to get green.
7. Finish by writing `BUILD_REPORT.md` as specified in M7. Then reply with a summary of no
   more than 15 lines that contains:
   - the milestone statuses;
   - the pytest summary line;
   - the per-shader fps table;
   - any deviations.

Do not touch any other machine, any Claude/MCP configuration, or anything outside the
`funkbox` folder.

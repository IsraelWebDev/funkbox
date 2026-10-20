You are implementing **funk v3.1 "projects"**: automation on top of funk v3 "reel".

Working directory: the `funkbox` folder containing this file.

1. Read `PLAN-v3.1-projects.md` completely. Then read the parts of `PLAN-v3-reel.md` and
   `PLAN.md` it references. Skim `reel/` (validate.py, copydeck.py, render.py,
   storyboard.py), `reel_mcp.py` and `reel_cli.py`, so you extend them rather than duplicate.
2. Don't modify v1/v2 files, except the one documented `allowed_roots` hook. v3 files may
   change only as the plan says. All 164 existing tests must stay green.
3. Execute P1 → P7 in order. For each one:
   - implement it;
   - run its acceptance check;
   - fix until green;
   - `git commit -m "v3.1 P<n>: <title>"`.
4. GL: `FUNK_GL_BACKEND=egl` on headless Linux. Keep test renders small. Temporary files go
   only under `out/`.
5. The copy approval/lock tests (P3) carry the exactness guarantee. Never weaken them, and
   never add any path by which the agent can write `copy.json` without `approve_copy`'s
   checks.
6. Look at every contact sheet, swatch and storyboard you produce at least once.
7. Finish with `BUILD_REPORT-v3.1.md`, then reply with a summary of no more than 12 lines:
   - statuses;
   - the pytest line;
   - whether elicitation worked in the stdio test;
   - deviations.

Do not touch any other machine, any Claude/MCP configuration, or anything outside `funkbox`.

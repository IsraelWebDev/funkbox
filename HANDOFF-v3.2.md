You are implementing **funk v3.2 "stills"** on top of funkbox (v1, v2, v3 reel, v3.1 projects).

Working directory: the funkbox repo containing this file.

1. Read `PLAN-v3.2-stills.md` completely, then skim `funkgl/` (engine, blend, post, text),
   `reel/product.py`, `reel/copydeck.py`, `reel/project.py` + the approval/lock module,
   `studio/studio_mcp.py`, `studio/start_local_mcp.py`, `install.py`, `.mcp.json` and the
   existing tests. Reuse code; do not duplicate engines.
2. Do not modify v1/v2 behaviour. Don't weaken any existing test. The full suite must stay green
   both here and in a clean venv with only `requirements.txt` + pytest (that's what CI runs).
3. Execute S1 → S5 in order. For each one:
   - implement it;
   - run its acceptance check;
   - fix until green;
   - commit `v3.2 S<n>: <title>` with the author `funk builder <builder@example.com>`.

   Don't push, and don't rewrite history.
4. GL: `FUNK_GL_BACKEND=egl` on headless Linux. Temp files go under `out/` only. FastSD is not
   available here: use `STUDIO_FAKE_SD=1` in tests. Don't try to install FastSD.
5. Look at every poster, contact sheet and before/after image you produce at least once (read the
   PNG). Make the demo poster (`examples/stills/glowmop_poster.poster.json`) actually look good:
   it goes in the README.
6. Finish with `BUILD_REPORT-v3.2.md`, then reply in ≤ 12 lines:
   - statuses;
   - the pytest line (full and clean-venv);
   - deviations;
   - anything the supervisor must verify on the Windows laptop with real FastSD.

Do not touch any other machine, Claude/MCP config, or anything outside this repo.

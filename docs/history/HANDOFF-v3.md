You are implementing **funk v3 "reel"**: a shot-list → vertical-video-ad engine built on top of
funk v2.

Working directory: the `funkbox` folder that contains this file.

1. Read `PLAN-v3-reel.md` completely. Then read the sections of `PLAN.md` it references
   (1, 5 and 7). Then skim `funkgl/` (engine.py, recipe.py, encode.py, text.py) and
   `funk2_mcp.py`, so you reuse what exists rather than rewrite it.
2. Do not modify `funk.py`, `funk_mcp.py`, `funk2.py`, `funk2_mcp.py` or `funkgl/`, except
   for documented backwards-compatible hooks.
3. Execute R0 → R8 in order. For each one:
   - implement it;
   - run its acceptance check;
   - fix until green;
   - `git commit -m "v3 R<n>: <title>"`.
4. GL backend: on Linux with no display, set `FUNK_GL_BACKEND=egl`. On Windows, leave it
   unset. Keep test renders small.
5. Models: the container may not reach Hugging Face. Implement `setup-models` fully, but
   test with the stubs the plan describes. Never download anything at render time.
6. Look at every storyboard or still you produce at least once (read the PNG).
7. The exactness tests (R3, R4, R5) are the point of v3. Never weaken them. Follow "When
   stuck" exactly.
8. Finish with `BUILD_REPORT-v3.md`. Then reply with a summary of no more than 15 lines:
   milestone statuses, the pytest summary line, demo draft-render time, model
   licences/hashes (or "not fetched"), and deviations.

Do not touch any other machine, any Claude/MCP configuration, or anything outside `funkbox`.

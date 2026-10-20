You are implementing **funkbox v4: consolidation**. Same repo, same rules as before.

1. Read `PLAN-v4-consolidate.md` completely. Skim every top-level script and package it names before
   moving anything.
2. Execute C1 → C5 in order. For each one:
   - implement it;
   - run its acceptance check;
   - fix until green;
   - commit `v4 C<n>: <title>` with the author `funk builder <builder@example.com>`.
   Don't push, and don't rewrite history.
3. GL: `FUNK_GL_BACKEND=egl` here. Temp files go only under `out/`. Use `STUDIO_FAKE_SD=1` for
   image tools.
4. Look at the v1→v4 preset comparison sheet and one demo render before you call C2 done.
5. Never weaken the exactness, copy-lock, fence or loop tests (PLAN section 9).
6. Finish with `docs/history/BUILD_REPORT-v4.md`, then reply in ≤ 12 lines:
   - statuses;
   - the pytest lines (full and clean venv);
   - LOC, file and tool counts before → after;
   - deviations;
   - what the supervisor must check on the Windows laptop.

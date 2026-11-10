# CLAUDE.md

## Keep the docs and the catalog in step with options

Tool schemas and option lists (looks, palettes, effects, shaders, presets) are read by clients, so a change to
any of them has to reach every place that lists them. When you change a tool's signature or docstring, or an
option list a tool reports, update all of these in the same change:

1. Regenerate the tool catalog: `python -m funkbox catalog` (writes `docs/catalog.json`).
2. Update the tool count in `README.md` and `llms.txt` if it changed.
3. Update the doc that lists the options (for looks, `docs/loops.md`; for other lists, the doc that names them).
4. Run `tests/test_catalog.py` and `tests/test_docs_follow_options.py`, plus the tests for the area you changed,
   before you push.

If an option set is closed, prefer an enum in the tool's schema over a free string, so the catalog shows the
valid values.

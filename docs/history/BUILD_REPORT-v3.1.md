# BUILD REPORT v3.1 "projects"

All of P1 to P7 are done. Full suite: 241 passed, 1 skipped (baseline was 164 passed, 1 skipped).

| milestone | status | notes |
|---|---|---|
| P1 project + ingest | done | classification, product candidate ranking, cut-out quality score, contact sheet, duplicates by sha256, nothing deleted |
| P2 brand | done | Lab k-means palette, WCAG text colour, user fonts win over bundled, swatch image |
| P3 copy draft/approve/lock | done | `propose_copy` writes only `copy.draft.json`; `approve` is the only writer of `copy.json`; sha256 lock enforced by the validator; elicitation with a chat fallback; tests never weakened |
| P4 auto_reel + collision rule | done | deterministic (seeded by project name); product/caption overlap at most 8%, also a validator error; funk2 brand-colour fillers |
| P5 MCP + shortcuts + docs | done | 11 new tools, new CLI verbs, desktop shortcuts, README section |
| P6 self-install packaging | done | `install.py` (stdlib, idempotent, `--dry-run`, `--register desktop/code/none`, `--unregister`, `--with reel,fastsd,gimp`, `--shortcuts`), `pyproject.toml` (synced with requirements by a test), studio path resolver (env, `funkbox.toml`, `vendor/`), GIMP launcher template, Claude Code plugin files, `AGENTS.md`, CI workflow |
| P7 publication hygiene | done | demo brand is GLOWMOP / glowmop.example / DEMO-123; personal data removed; LICENSE (MIT), THIRD_PARTY_NOTICES.md, .gitignore, file-size and secret tests, public README with preview, long sections moved to `docs/` |

## Deviations and decisions
1. The collision rule needed the demo's product placement changed (low product, captions on top) so the shipped demo passes it.
2. `reel_mcp.py`'s tool-set test was changed from equality to "v3 tools plus the documented v3.1 additions", with a check that no tool name suggests file writing.
3. Claude Code registration (`--register code`) runs `claude mcp add --scope user` (or prints the lines) instead of writing a project `.mcp.json`, because the repo-root `.mcp.json` is the plugin's own server list.
4. The plugin `.mcp.json` and the gimp entry (`uv run --directory vendor/gimp-mcp gimp_mcp_server.py`, corrected by the supervisor from upstream's README) follow the documented schema and upstream README as I know them; the plugin was not installed into a live Claude Code here.
5. `uv.lock` is git-ignored (not committed) so it cannot drift from `requirements*.txt`.
6. Commits use a neutral identity; the pre-existing history contains a personal commit e-mail, which the publisher should squash or rewrite before pushing.
7. Not exercised here (no Windows, no GIMP, no network for FastSD): the real `--with fastsd,gimp` installs, the Windows `.lnk` creation and the Desktop-config merge on a real Claude Desktop. The merge, backup and unregister logic is tested against temp files; the other paths are covered by `--dry-run`.

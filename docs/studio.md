# studio: start the heavy apps on demand

`studio/studio_mcp.py` is a small MCP server (`studio` in Claude Desktop) that manages the local apps:

| tool | does |
|---|---|
| `status()` | FastSD on :8000, GIMP MCP on :9877, inputs folder |
| `start(service, wait_s=0)` | `fastsd`: launches the localhost-only launcher and waits (~20 s). `gimp`: tells Claude what to ask you (double-click **GIMP + MCP** on the desktop); with `wait_s` it waits for GIMP's port |
| `stop(service)` | stops FastSD (frees ~4 GB). Never closes GIMP |
| `generate_image(prompt, ...)` | auto-starts FastSD, saves `inputs/<name>.png` (ready for a funk2 image layer), returns the image |

FastSD shuts itself down after 20 idle minutes if studio started it (`STUDIO_FASTSD_IDLE_MIN`). 512x512 stays
warm (~3-5 s per image); the first image at any other size recompiles the iGPU model (~2.5 min).

**Why GIMP is started by you, not by Claude:** Claude Desktop is a sandboxed (MSIX) app that kills everything an MCP
server launched when it stops that server, so a Claude-launched GIMP would close and lose unsaved work. It also
sees a virtualized `%APPDATA%`. The **GIMP + MCP** shortcut (`studio/GIMP-MCP.cmd`, rendered from `GIMP-MCP.cmd.template` by `install.py --with gimp`) runs in your normal session: it
syncs the plug-in into your real GIMP profile and opens GIMP with the MCP server already listening.
`studio/start_local_mcp.py` is the FastSD launcher (copied into `vendor/fastsdcpu` by `install.py --with fastsd`): it binds 127.0.0.1 only and
works around FastSD's FastAPI-kwargs crash. Register everything with `python install.py --register desktop` (quit Claude Desktop first).


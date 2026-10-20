# Executed inside GIMP by python-fu-eval (see GIMP-MCP.cmd.template).
# GIMP can run -b batch commands before every plug-in procedure is visible in the PDB
# (seen on GIMP 3.2.6 / Windows: lookup_procedure() returned None on some cold starts),
# so wait for the MCP plug-in to register before starting its server. Progress is logged to
# <funkbox>/out/gimp_start.log because GIMP on Windows has no visible console.
import os
import time
import traceback

_log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                         if "__file__" in globals() else ".",
                         "out", "gimp_start.log")


def _log(msg):
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


try:
    from gi.repository import Gimp
    _pdb = Gimp.get_pdb()
    _proc = None
    for _i in range(240):                       # up to ~120 s (first run queries plug-ins)
        _proc = _pdb.lookup_procedure("plug-in-mcp-server")
        if _proc is not None:
            break
        time.sleep(0.5)
    _log(f"lookup after {_i * 0.5:.1f}s: {'found' if _proc else 'MISSING'}")
    if _proc is None:
        raise RuntimeError("plug-in-mcp-server not registered")
    _cfg = _proc.create_config()
    _cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    _log("starting MCP server")
    _proc.run(_cfg)                            # blocks: the plug-in's GLib loop serves MCP clients
except Exception:
    _log("FAILED:\n" + traceback.format_exc())
    raise

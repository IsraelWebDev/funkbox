

def test_sync_launcher_refreshes_stale_files(tmp_path):
    import importlib, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "studio"))
    try:
        import studio_mcp as sm
    finally:
        sys.path.pop(0)
    (tmp_path / "start_local_mcp.py").write_text("old")
    copied = sm.sync_launcher(tmp_path)
    assert set(copied) == {"start_local_mcp.py", "sdcore.py"}
    assert (tmp_path / "start_local_mcp.py").read_bytes() == (root / "studio" / "start_local_mcp.py").read_bytes()
    assert sm.sync_launcher(tmp_path) == []

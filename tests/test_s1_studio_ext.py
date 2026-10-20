

def test_sync_launcher_refreshes_stale_files(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    from funkbox.studio import apps as sm
    (tmp_path / "start_local_mcp.py").write_text("old")
    copied = sm.sync_launcher(tmp_path)
    assert set(copied) == {"start_local_mcp.py", "sdcore.py"}
    assert (tmp_path / "start_local_mcp.py").read_bytes() == (root / "funkbox" / "studio" / "start_local_mcp.py").read_bytes()
    assert sm.sync_launcher(tmp_path) == []

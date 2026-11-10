"""When a tool's options change, the docs that list them change with it (see CLAUDE.md)."""

from pathlib import Path

from funkbox.loops import presets

ROOT = Path(__file__).resolve().parents[1]


def test_every_look_is_named_in_the_loops_doc():
    doc = (ROOT / "docs" / "loops.md").read_text(encoding="utf-8")
    missing = [name for name in presets.LOOKS if name not in doc]
    assert not missing, f"docs/loops.md does not name these looks: {missing}"


def test_claude_md_states_the_catalog_rule():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "python -m funkbox catalog" in text
    assert "tests/test_catalog.py" in text

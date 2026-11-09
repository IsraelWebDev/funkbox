"""The tool catalog (docs/catalog.json) matches the MCP registry, and the docs agree with it.

ADR plan-driven renders, decision 1 (IsraelWebDev/funkbox-online docs/adr-plan-driven-renders.md). A failure here
means: run ``python -m funkbox catalog`` and update the counts in README.md and llms.txt.
"""

import re
from pathlib import Path

from funkbox import catalog

ROOT = Path(__file__).resolve().parents[1]


def test_every_registry_tool_is_in_the_catalog_once_with_a_description_and_an_object_schema():
    doc = catalog.build()
    names = [t["name"] for t in doc["tools"]]
    assert len(names) == len(set(names)) == doc["tool_count"]
    for t in doc["tools"]:
        assert t["description"], f"{t['name']} has no description"
        assert t["input_schema"].get("type") == "object", f"{t['name']} has no object input schema"
        assert t["area"] in catalog.AREAS, f"{t['name']} is not in a known area"


def test_the_committed_catalog_is_the_generated_one():
    assert catalog.is_current(), "docs/catalog.json is out of date: run `python -m funkbox catalog`"


def test_readme_and_llms_txt_state_the_registry_tool_count():
    n = catalog.build()["tool_count"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
    assert re.search(rf"one MCP server \(`funkbox`, {n} tools\)", readme), f"README.md does not say {n} tools"
    assert re.search(rf"\(`funkbox`, {n} tools\)", llms), f"llms.txt does not say {n} tools"
    assert "docs/catalog.json" in llms, "llms.txt should point at the catalog"

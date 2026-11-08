"""The tool catalog: one machine-readable list of the MCP tools, built from the server registry.

Decision 1 of docs/adr-plan-driven-renders.md in IsraelWebDev/funkbox-online. The catalog is generated, never
written by hand: ``python -m funkbox catalog`` writes docs/catalog.json, and the tests fail when that file, the
README or llms.txt disagree with the registry. The website pins one catalog version.

Each entry: name, area (the tool-name prefix), description, and the JSON Schema of its input. Cost class, output
kind and host-path flags are not in the registry yet; they are a follow-up in the ADR.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from funkbox import __version__

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "docs" / "catalog.json"
AREAS = ("loop", "reel", "project", "poster", "image", "apps", "music", "video")


def registry_tools() -> list:
    """The tools the MCP server registers, sorted by name. Imports the MCP package, so only the server path needs it."""
    from funkbox import server

    return sorted(asyncio.run(server.build_server().list_tools()), key=lambda t: t.name)


def _schema(tool) -> dict:
    # mcp 2.x names the field input_schema; mcp 1.x uses inputSchema. The engine supports both.
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}


def build() -> dict:
    tools = [
        {
            "name": t.name,
            "area": t.name.split("_", 1)[0],
            # Collapse all whitespace: docstring continuation indentation differs between Python versions,
            # and the committed catalog must be the same on every supported version.
            "description": " ".join((t.description or "").split()),
            "input_schema": _schema(t),
        }
        for t in registry_tools()
    ]
    return {
        "catalog_version": 1,
        "server": "funkbox",
        "engine_version": __version__,
        "tool_count": len(tools),
        "tools": tools,
    }


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def write(path: Path = CATALOG_PATH) -> None:
    path.write_text(dumps(build()), encoding="utf-8")


def is_current(path: Path = CATALOG_PATH) -> bool:
    return path.exists() and path.read_text(encoding="utf-8") == dumps(build())

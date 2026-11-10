"""The closed option sets the MCP tools accept, as schema enums.

Clients plan calls from the catalog (docs/catalog.json), so an option that is only described in prose is invisible
to them. Each alias here is a ``Literal`` built from the engine's own constant, so the schema lists exactly the values
the engine accepts and cannot drift from it. Tests: tests/test_tool_options.py.

The recipe's own options (palette names, effects, blend modes, layer types) sit inside a dict parameter, so they are
declared as JSON-schema properties (``RECIPE``) rather than as types. The engine still validates the recipe, and it
accepts keys the schema does not list (text, post, settings), so the recipe schema stays open.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from funkbox.gl.recipe import BLEND_MODES, LAYER_TYPES, PALETTES, V1_EFFECTS
from funkbox.loops.presets import LOOKS
from funkbox.reel.autoreel import PACE_SECONDS
from funkbox.reel.captions import list_styles
from funkbox.stills.schema import PRESETS as STILL_SIZES
from funkbox.studio.sdcore import ASPECTS

#: a named loop look (loop list): effect, palette and the rest of its recipe
LookName = Literal[tuple(LOOKS)]  # type: ignore[valid-type]
#: a canvas size for a still (square, story, landscape ...)
StillSize = Literal[tuple(STILL_SIZES)]  # type: ignore[valid-type]
#: an image aspect for FastSD
Aspect = Literal[tuple(ASPECTS)]  # type: ignore[valid-type]
#: which app: studio's FastSD, GIMP, or both
Service = Literal["all", "fastsd", "gimp"]
#: how a poster fits the canvas when sized: the canvas, or just its drawn content
FitTo = Literal["canvas", "content"]
#: how a reference picture is fitted to a candidate for comparison
CompareFit = Literal["cover", "stretch"]
#: a caption style, one of the files in the caption templates
CaptionStyle = Literal[tuple(list_styles())]  # type: ignore[valid-type]
#: where burned-in subtitles sit
SubtitleAnchor = Literal["top", "center", "bottom"]
#: the cut pace of an auto reel
Pace = Literal[tuple(PACE_SECONDS)]  # type: ignore[valid-type]
#: what a reel timeline call does: open the page, or report the edit log
TimelineAction = Literal["open", "changes"]

#: the recipe's closed options, as JSON-schema properties (the recipe itself stays an open object)
RECIPE_PROPERTIES = {
    "palette": {
        "type": "object",
        "description": 'The recipe palette: {"name": one of the palettes, "cycles": number}.',
        "properties": {"name": {"type": "string", "enum": list(PALETTES)}},
    },
    "layers": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": [*LAYER_TYPES, "v1"]},
                "effect": {"type": "string", "enum": list(V1_EFFECTS)},
                "blend": {"type": "string", "enum": list(BLEND_MODES)},
                "palette": {"type": "string", "enum": list(PALETTES)},
                "mask": {
                    "description": "What scales this layer's alpha: an image path (PNG with alpha), or typed text drawn "
                    "by the poster text pipeline (the text is checked, so it is drawn exactly).",
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "The words to draw."},
                                "font": {"type": "string", "description": "A font, as the poster text uses."},
                                "size": {
                                    "type": "number",
                                    "minimum": 0.01,
                                    "maximum": 0.5,
                                    "description": "Text height as a fraction of the frame height (poster range).",
                                },
                                "align": {"type": "string", "enum": ["left", "center", "right"]},
                            },
                            "required": ["text"],
                        },
                    ],
                },
                "mask_fit": {"type": "string", "enum": ["cover", "contain", "stretch"]},
                "bevel": {
                    "type": "object",
                    "description": "Light the masked shape from its edges so it reads as raised. Colour only; needs a mask.",
                    "properties": {
                        "width": {"type": "integer", "minimum": 1, "maximum": 64, "description": "Edge band, px."},
                        "strength": {"type": "number", "minimum": 0, "maximum": 1},
                        "angle": {"type": "number", "description": "Light direction, degrees."},
                    },
                },
            },
        },
    },
}

#: a loop recipe: a JSON object, validated by the engine (see loop_list for the full shape)
Recipe = Annotated[dict, Field(json_schema_extra={"properties": RECIPE_PROPERTIES})]

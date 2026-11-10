"""Every closed option set a tool takes is an enum in its schema, read from the engine's own constant.

Clients (the hosted site, Claude Code, Claude Desktop) plan calls from the catalog, so an option that is only
described in prose is invisible to them. Each test here names one option set and checks the catalog shows exactly
the values the engine accepts. The engine stays the authority: these enums come from the same constants.
"""

import pytest

from funkbox import catalog
from funkbox.gl.recipe import BLEND_MODES, LAYER_TYPES, PALETTES, V1_EFFECTS
from funkbox.loops import presets
from funkbox.loops.tools import _recipe_arg
from funkbox.reel.autoreel import PACE_SECONDS
from funkbox.reel.captions import list_styles
from funkbox.studio.sdcore import ASPECTS
from funkbox.stills.schema import PRESETS as STILL_SIZES


def _tools() -> dict:
    return {t["name"]: t["input_schema"] for t in catalog.build()["tools"]}


def _prop(tool: str, param: str) -> dict:
    return _tools()[tool]["properties"][param]


def _enum(prop: dict) -> list:
    """The enum of a property, whether it is plain or optional (anyOf with null)."""
    if "enum" in prop:
        return prop["enum"]
    for alt in prop.get("anyOf", []):
        if "enum" in alt:
            return alt["enum"]
    raise AssertionError(f"no enum in {prop}")


def _recipe_prop(tool: str) -> dict:
    """The recipe parameter's schema (a dict, or a string-or-dict union for loop_still)."""
    prop = _prop(tool, "recipe")
    for alt in prop.get("anyOf", [prop]):
        if alt.get("type") == "object":
            return alt
    raise AssertionError(f"no object branch in {prop}")


# --- looks and sizes on the loop tools ------------------------------------------------------------------------ #


@pytest.mark.parametrize("tool", ["loop_preview", "loop_sheet", "loop_render", "loop_still"])
def test_every_loop_tool_that_takes_a_look_shows_the_look_names(tool):
    assert _enum(_prop(tool, "look")) == list(presets.LOOKS)


def test_loop_still_preset_is_a_canvas_size_and_the_schema_says_so():
    assert _enum(_prop("loop_still", "preset")) == list(STILL_SIZES)


def test_the_look_and_recipe_inputs_are_optional_and_exclusive_in_the_engine():
    assert _recipe_arg(None, "liquid-metal") == presets.PRESETS["liquid-metal"]
    assert _recipe_arg({"layers": []}, None) == {"layers": []}
    with pytest.raises(ValueError):
        _recipe_arg({"layers": []}, "liquid-metal")
    with pytest.raises(ValueError):
        _recipe_arg(None, None)


# --- the recipe's own options (effects, palettes, blend modes, layer types) ---------------------------------- #


def test_recipe_palette_names_are_an_enum():
    recipe = _recipe_prop("loop_render")
    assert _enum(recipe["properties"]["palette"]["properties"]["name"]) == list(PALETTES)


def test_recipe_layer_fields_are_enums():
    layer = _recipe_prop("loop_render")["properties"]["layers"]["items"]["properties"]
    assert _enum(layer["effect"]) == list(V1_EFFECTS)
    assert _enum(layer["blend"]) == list(BLEND_MODES)
    assert _enum(layer["type"]) == [*LAYER_TYPES, "v1"]


def test_the_recipe_schema_still_accepts_extra_keys():
    # the recipe is validated by the engine, which knows keys the schema does not list (text, post, settings)
    assert _recipe_prop("loop_render").get("additionalProperties") is True


@pytest.mark.parametrize("effect", V1_EFFECTS)
def test_every_listed_v1_effect_is_accepted_by_the_engine(effect):
    from funkbox.gl.recipe import Layer

    Layer(type="v1", effect=effect)


# --- scalar options on the other tools ------------------------------------------------------------------------ #


def test_image_aspects_are_an_enum_on_generate_and_restyle():
    assert _enum(_prop("image_generate", "aspect")) == list(ASPECTS)
    assert _enum(_prop("image_restyle", "aspect")) == list(ASPECTS)


def test_app_services_are_enums_on_start_and_stop():
    assert _enum(_prop("apps_start", "service")) == ["all", "fastsd", "gimp"]
    assert _enum(_prop("apps_stop", "service")) == ["all", "fastsd", "gimp"]


def test_poster_fit_modes_are_enums():
    assert _enum(_prop("poster_sizes", "fit_to")) == ["canvas", "content"]
    assert _enum(_prop("poster_compare", "fit")) == ["cover", "stretch"]


def test_caption_styles_and_anchors_are_enums_on_subtitles_and_auto_reel():
    assert _enum(_prop("video_subtitles", "style")) == list_styles()
    assert _enum(_prop("video_subtitles", "anchor")) == ["top", "center", "bottom"]
    assert _enum(_prop("project_auto_reel", "style")) == list_styles()


def test_auto_reel_pace_is_an_enum():
    assert _enum(_prop("project_auto_reel", "pace")) == list(PACE_SECONDS)


def test_timeline_action_is_an_enum():
    assert _enum(_prop("reel_timeline", "action")) == ["open", "changes"]


def test_the_loops_doc_says_which_inputs_take_a_look():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[1] / "docs" / "loops.md").read_text(encoding="utf-8")
    for tool in ("loop_preview", "loop_sheet", "loop_render", "loop_still"):
        assert f"`{tool}`" in doc
    assert "`look`" in doc and "canvas size" in doc

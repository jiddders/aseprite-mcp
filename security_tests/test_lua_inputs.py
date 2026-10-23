"""Regression checks for data interpolated into drawing-tool Lua scripts.

The Aseprite runner is replaced for every test. In particular, the injection
cases must never execute the generated Lua while testing the vulnerable code.
"""

import asyncio
import json
import re

import pytest

from aseprite_mcp.core.commands import AsepriteCommand
from aseprite_mcp.tools import drawing, quality


TOOLS = ("draw_pixels", "draw_pixels_at", "draw_polygon", "draw_path")


def _call(tool_name, filename, coordinates, layer_name="body"):
    if tool_name == "draw_pixels":
        call = drawing.draw_pixels(
            filename, [{**coordinates, "color": "#336699"}]
        )
    elif tool_name == "draw_pixels_at":
        call = drawing.draw_pixels_at(
            filename, layer_name, 1,
            [{**coordinates, "color": "#336699"}],
        )
    else:
        points = [coordinates, {"x": 4, "y": 5}, {"x": 6, "y": 7}]
        if tool_name == "draw_path":
            points.pop()
        call = getattr(drawing, tool_name)(filename, layer_name, 1, points)
    return asyncio.run(call)


@pytest.fixture
def sprite_path(tmp_path):
    # Drawing tools only check for existence before invoking Aseprite.
    path = tmp_path / "sprite.aseprite"
    path.touch()
    return str(path)


@pytest.fixture
def captured_scripts(monkeypatch):
    scripts = []

    def capture(script, filename=None):
        scripts.append((script, filename))
        return True, "OK"

    monkeypatch.setattr(AsepriteCommand, "execute_lua_script_checked", capture)
    return scripts


@pytest.mark.parametrize("tool_name", TOOLS)
@pytest.mark.parametrize("axis", ("x", "y"))
@pytest.mark.parametrize(
    "bad_coordinate",
    ('0); error("INJECTED"); --', True, 1.5, [0], {"value": 0}),
    ids=("lua-code", "boolean", "fraction", "list", "mapping"),
)
def test_bad_nested_coordinate_never_reaches_aseprite(
    tool_name, axis, bad_coordinate, sprite_path, captured_scripts
):
    coordinates = {"x": 2, "y": 3}
    coordinates[axis] = bad_coordinate

    try:
        result = _call(tool_name, sprite_path, coordinates)
    except (TypeError, ValueError):
        pass  # A direct Python caller may receive a validation exception.
    else:
        assert "drawn" not in result.lower(), result

    assert captured_scripts == []


@pytest.mark.parametrize("tool_name", TOOLS)
@pytest.mark.parametrize("axis", ("x", "y"))
def test_missing_nested_coordinate_never_reaches_aseprite(
    tool_name, axis, sprite_path, captured_scripts
):
    coordinates = {"x": 2, "y": 3}
    del coordinates[axis]

    try:
        result = _call(tool_name, sprite_path, coordinates)
    except (TypeError, ValueError, KeyError):
        pass
    else:
        assert "drawn" not in result.lower(), result

    assert captured_scripts == []


@pytest.mark.parametrize("tool_name", TOOLS)
def test_integer_coordinates_reach_aseprite(tool_name, sprite_path, captured_scripts):
    result = _call(tool_name, sprite_path, {"x": -2, "y": 3})

    assert "drawn" in result.lower(), result
    assert len(captured_scripts) == 1
    assert captured_scripts[0][1] == sprite_path
    assert "-2" in captured_scripts[0][0]
    assert "3" in captured_scripts[0][0]


@pytest.mark.parametrize("tool_name", TOOLS[1:])
@pytest.mark.parametrize(
    "layer_name",
    (
        'quote "); error("INJECTED"); --',
        "backslash \\ path",
        "first line\nsecond line",
    ),
    ids=("quotes-and-code", "backslash", "newline"),
)
def test_layer_name_remains_lua_string_data(
    tool_name, layer_name, sprite_path, captured_scripts
):
    result = _call(tool_name, sprite_path, {"x": 2, "y": 3}, layer_name)

    assert "drawn" in result.lower(), result
    assert len(captured_scripts) == 1
    script, _ = captured_scripts[0]
    # JSON's quoted-string syntax is shared with these Lua escapes. Matching
    # the whole find_layer call ensures a quote cannot terminate the literal.
    match = re.search(r'find_layer\(spr,\s*("(?:\\.|[^"\\])*")\)', script)
    assert match is not None, script
    assert json.loads(match.group(1)) == layer_name


@pytest.mark.parametrize("tool_name", ("audit_animation", "animation_sanitize"))
@pytest.mark.parametrize(
    "layer_name",
    (
        'quote "); error("INJECTED"); --',
        "backslash \\ path",
        "first line\nsecond line",
    ),
    ids=("quotes-and-code", "backslash", "newline"),
)
def test_quality_layer_names_remain_lua_table_data(
    tool_name, layer_name, sprite_path, captured_scripts
):
    result = asyncio.run(getattr(quality, tool_name)(
        sprite_path,
        overlap_pairs=[f"{layer_name},body"],
        layer_frame_ranges=[f"{layer_name}:1-2"],
    ))

    assert not result.startswith("Failed"), result
    assert len(captured_scripts) == 1
    script, _ = captured_scripts[0]
    # The names in both table constructors must be complete quoted values.
    quoted = json.dumps(layer_name)
    assert f"[{quoted}]=" in script
    assert f"{{{quoted},\"body\"}}" in script

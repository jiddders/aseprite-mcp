"""Lua escape hatch (script.py)."""
import pytest

from conftest import ok, run

from aseprite_mcp.tools import script


@pytest.fixture
def unsafe_lua(monkeypatch):
    monkeypatch.setenv("ASEPRITE_UNSAFE_LUA", "1")
    monkeypatch.setattr(script, "UNSAFE_LUA", True)


def test_run_lua_script_returns_printed_output(sprite, unsafe_lua):
    out = ok(run(script.run_lua_script(
        'local spr = app.activeSprite print("size=" .. spr.width .. "x" .. spr.height)',
        sprite)))
    assert "size=32x32" in out


def test_run_lua_script_rejects_empty(unsafe_lua):
    assert run(script.run_lua_script("  ")) == "Script cannot be empty"

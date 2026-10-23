import ast
import asyncio
import importlib
import inspect
import json
from pathlib import Path
import subprocess

import pytest

from aseprite_mcp.core.workspace import Workspace
from aseprite_mcp.core import commands, fonts


@pytest.fixture
def scope(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    for module in [commands, fonts]:
        monkeypatch.setattr(module, 'workspace', workspace)
    (tmp_path / 'sprite.aseprite').write_bytes(b'sprite')
    return workspace


@pytest.mark.parametrize('raw', ['../x', 'sub/../x', '/outside', 'a\x00b', 'a\nb', 'a\tb', '{layer}.png', '*.png', 'a?b', 'a[1]', 'a%02d', 'a\\b'])
def test_reject_path_syntax(scope, raw):
    with pytest.raises(ValueError):
        scope.output_file(raw)


def test_normal_paths_and_root_capture(scope, monkeypatch, tmp_path):
    assert scope.input_file('sprite.aseprite') == str(tmp_path / 'sprite.aseprite')
    assert scope.output_file('new/file.png') == str(tmp_path / 'new/file.png')
    monkeypatch.chdir(tmp_path.parent)
    assert scope.output_file('new.png') == str(tmp_path / 'new.png')
    with pytest.raises(Exception):
        scope.root = tmp_path.parent


@pytest.mark.parametrize('dangling', [False, True])
def test_all_links_rejected(scope, tmp_path, dangling):
    target = tmp_path / ('missing' if dangling else 'sprite.aseprite')
    (tmp_path / 'link').symlink_to(target)
    with pytest.raises(ValueError):
        scope.output_file('link')
    (tmp_path / 'dirlink').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        scope.output_file('dirlink/new.png')


@pytest.mark.parametrize('sheet', ['../sprite.aseprite', '/outside.png', 'link.png'])
def test_descriptor_sheets_reject_before_pillow(scope, tmp_path, monkeypatch, sheet):
    directory = tmp_path / 'fonts' / 'font'
    directory.mkdir(parents=True)
    (directory / 'link.png').symlink_to(tmp_path / 'sprite.aseprite')
    (directory / 'font.json').write_text(json.dumps({'sheets': [{'file': sheet}]}))
    monkeypatch.setattr(fonts.Image, 'open', lambda *a, **k: pytest.fail('Pillow reached'))
    with pytest.raises(ValueError):
        fonts.BitmapFont(str(directory))


def test_only_workspace_fonts(scope, tmp_path):
    assert fonts.available_fonts() == []
    (tmp_path / 'fonts').mkdir()
    (tmp_path / 'fonts' / 'local.ttf').write_bytes(b'font')
    (tmp_path / 'fonts' / 'linked.ttf').symlink_to(tmp_path / 'sprite.aseprite')
    assert [f['name'] for f in fonts.available_fonts()] == ['local']


INVENTORY = json.loads((Path(__file__).parent / 'path_inventory.json').read_text())
MODULES = {}
for file in (Path(__file__).parents[1] / 'aseprite_mcp' / 'tools').glob('*.py'):
    for node in ast.parse(file.read_text()).body:
        if isinstance(node, ast.AsyncFunctionDef):
            MODULES[node.name] = file.stem


@pytest.mark.parametrize('name,parameter', [(name, p) for name, paths in INVENTORY.items() for p in paths])
def test_all_declared_paths_reject_before_sink(scope, monkeypatch, name, parameter):
    module = importlib.import_module('aseprite_mcp.tools.' + MODULES[name])
    monkeypatch.setattr(module, 'workspace', scope)
    monkeypatch.setattr(commands.AsepriteCommand, 'run_command', lambda *a, **k: pytest.fail('native sink reached'))
    monkeypatch.setattr(commands.AsepriteCommand, 'execute_lua_script', lambda *a, **k: pytest.fail('Lua sink reached'))
    fn = getattr(module, name)
    kwargs = {}
    for key, arg in inspect.signature(fn).parameters.items():
        if key in INVENTORY[name]:
            role = INVENTORY[name][key]
            kwargs[key] = 'sprite.aseprite' if role == 'input_file' else 'output.png'
        elif arg.default is inspect.Parameter.empty:
            kwargs[key] = 1 if arg.annotation in (int, float) else '#000000' if key == 'color' else [] if key in ('pixels', 'points') else 'x'
    kwargs[parameter] = '../outside'
    with pytest.raises(ValueError):
        asyncio.run(fn(**kwargs))


def test_inventory_reconciles_all_tools_and_path_parameters():
    path_names = {'filename', 'source_filename', 'target_filename', 'output_filename', 'data_filename', 'output_directory', 'image_path'}
    for name, module in MODULES.items():
        if module in ('preview', 'script'):
            continue
        assert name in INVENTORY
        function = getattr(importlib.import_module('aseprite_mcp.tools.' + module), name)
        args = set(inspect.signature(function).parameters)
        assert set(INVENTORY[name]) == args & path_names
        assert not {p for p in args if ('file' in p or 'directory' in p or 'path' in p) and p not in path_names}


def test_lua_controls_are_unambiguous():
    assert commands.lua_escape('\x001\t\x1f\x7f"\\') == '\\0001\\t\\031\\127\\"\\\\'


@pytest.mark.parametrize('error', [OSError('missing'), subprocess.TimeoutExpired('aseprite', 120)])
def test_launch_and_timeout_are_reported(monkeypatch, error):
    def fail(*args, **kwargs):
        assert kwargs['timeout'] == 120
        raise error
    monkeypatch.setattr(commands.subprocess, 'run', fail)
    assert commands.AsepriteCommand.run_command([])[0] is False


def test_owned_scratch_is_removed(scope):
    with scope.scratch() as directory:
        assert Path(directory).parent == scope.root
    assert not Path(directory).exists()


def test_raw_lua_registration_and_guard_in_fresh_process(tmp_path):
    import os
    import sys
    code = '''
import asyncio
from aseprite_mcp import mcp
from aseprite_mcp.tools import script
from aseprite_mcp.core.commands import AsepriteCommand
AsepriteCommand.execute_lua_script = lambda *args: (True, 'executed')
async def check():
    names = [tool.name for tool in await mcp.list_tools()]
    assert ('run_lua_script' in names) == script.UNSAFE_LUA
    result = await script.run_lua_script('print(1)')
    assert result == 'executed' if script.UNSAFE_LUA else 'disabled' in result
asyncio.run(check())
'''
    for enabled in ('0', '1'):
        env = dict(os.environ, ASEPRITE_UNSAFE_LUA=enabled, ASEPRITE_WORKSPACE=str(tmp_path))
        subprocess.run([sys.executable, '-c', code], env=env, check=True, capture_output=True, text=True)


def test_numeric_layer_exports_do_not_use_metadata_for_paths(scope, monkeypatch):
    from aseprite_mcp.tools import export
    monkeypatch.setattr(export, 'workspace', scope)
    name = '../outside"\\\n'
    monkeypatch.setattr(commands.AsepriteCommand, 'execute_lua_script_checked', lambda *a: (True, json.dumps([{'name': name, 'visible': True}])))
    def render(args):
        path = args[args.index('--save-as') + 1]
        assert Path(path).name == 'render.png'
        Path(path).write_bytes(b'png')
        return True, ''
    monkeypatch.setattr(commands.AsepriteCommand, 'run_command', render)
    assert 'unsupported' in asyncio.run(export.export_layers('sprite.aseprite', 'layers'))
    name = 'quote"\\\n'
    result = json.loads(asyncio.run(export.export_layers('sprite.aseprite', 'layers')))
    assert result['layers'] == [{'files': ['layer-0001.png'], 'layer': name}]
    assert (scope.root / 'layers' / 'layer-0001.png').exists()


def test_duplicate_layer_names_fail(scope, monkeypatch):
    from aseprite_mcp.tools import export
    monkeypatch.setattr(export, 'workspace', scope)
    monkeypatch.setattr(commands.AsepriteCommand, 'execute_lua_script_checked', lambda *a: (True, json.dumps([{'name': 'same', 'visible': True}] * 2)))
    monkeypatch.setattr(commands.AsepriteCommand, 'run_command', lambda *a: pytest.fail('native export reached'))
    assert 'duplicate' in asyncio.run(export.export_layers('sprite.aseprite', 'layers'))


def test_generated_sequence_cannot_follow_existing_link(scope, monkeypatch, tmp_path):
    from aseprite_mcp.tools import export
    monkeypatch.setattr(export, 'workspace', scope)
    sentinel = tmp_path / 'sentinel'
    sentinel.write_text('unchanged')
    (tmp_path / 'out1.png').symlink_to(sentinel)
    def render(args):
        output = Path(args[args.index('--save-as') + 1])
        output.with_name('render1.png').write_bytes(b'png')
        return True, ''
    monkeypatch.setattr(commands.AsepriteCommand, 'run_command', render)
    with pytest.raises(ValueError):
        asyncio.run(export.export_sprite('sprite.aseprite', 'out.png'))
    assert sentinel.read_text() == 'unchanged'


def test_suffix_complete_destination_is_rechecked(scope, monkeypatch, tmp_path):
    from aseprite_mcp.tools import export
    monkeypatch.setattr(export, 'workspace', scope)
    (tmp_path / 'out.png').symlink_to(tmp_path / 'sprite.aseprite')
    monkeypatch.setattr(commands.AsepriteCommand, 'run_command', lambda *a: pytest.fail('native reached'))
    with pytest.raises(ValueError):
        asyncio.run(export.export_frame('sprite.aseprite', 1, 'out'))


def test_inventory_matches_registered_surface():
    from aseprite_mcp import mcp
    from aseprite_mcp.core.workspace import UNSAFE_LUA
    registered = {tool.name for tool in asyncio.run(mcp.list_tools())}
    expected = set(INVENTORY) | {'start_preview_server', 'stop_preview_server'}
    if UNSAFE_LUA:
        expected.add('run_lua_script')
    assert registered == expected


@pytest.mark.parametrize('tool', ['measure_text', 'draw_text'])
def test_font_paths_cannot_reach_pillow(scope, monkeypatch, tool):
    from aseprite_mcp.tools import text
    monkeypatch.setattr(text, 'workspace', scope)
    monkeypatch.setattr(fonts.ImageFont, 'truetype', lambda *a, **k: pytest.fail('Pillow reached'))
    kwargs = {'text': 'hello', 'font': '../outside.ttf'}
    if tool == 'draw_text':
        kwargs.update(filename='sprite.aseprite', x=0, y=0)
    result = asyncio.run(getattr(text, tool)(**kwargs))
    assert 'error' in result.lower() or 'traversal' in result.lower()

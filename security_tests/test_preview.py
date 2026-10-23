import asyncio
import http.client
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from aseprite_mcp import _lifespan
from aseprite_mcp.core.preview import PreviewRegistry, previews


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def request(port, path, method='GET', headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        if headers is None:
            connection.request(method, path)
        else:
            connection.putrequest(method, path, skip_host=True)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


@pytest.fixture
def registry():
    registry = PreviewRegistry()
    yield registry
    registry.close_all()


def test_owned_lifecycle_and_regular_files(tmp_path, registry):
    (tmp_path / 'sprite.png').write_bytes(b'png-bytes')
    port = free_port()
    url = registry.start(str(tmp_path), port)
    assert f'http://127.0.0.1:{port}/' in url
    assert registry.start(str(tmp_path), port) == url
    assert request(port, '/sprite.png')[:2] == (200, b'png-bytes')
    status, body, headers = request(port, '/sprite.png', 'HEAD')
    assert (status, body, headers['Content-Length']) == (200, b'', '9')
    assert request(port, '/')[0] == 404
    assert request(port, '/sprite.png', 'POST')[0] == 501
    with pytest.raises(ValueError, match='another directory'):
        registry.start(str(tmp_path.parent), port)
    assert 'stopped' in registry.stop(port)
    assert 'No owned' in registry.stop(port)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))
    registry.start(str(tmp_path), port)
    registry.close_all()
    registry.close_all()
    assert not registry._previews


@pytest.mark.parametrize('method', ('GET', 'HEAD'))
def test_preview_rejects_unexpected_authority_before_file_access(tmp_path, registry, method):
    (tmp_path / 'probe.txt').write_text('private preview')
    port = free_port()
    registry.start(str(tmp_path), port)
    server = registry._previews[port].server
    authority = f'127.0.0.1:{port}'
    origin = f'http://{authority}'
    opened = []
    original_open_file = server.open_file

    def track_open(parts):
        opened.append(parts)
        return original_open_file(parts)

    server.open_file = track_open
    rejected_headers = [
        [],
        [('Host', authority), ('Host', authority)],
        [('Host', '127.0.0.1')],
        [('Host', '127.0.0.1:abc')],
        [('Host', f'localhost:{port}')],
        [('Host', f'attacker.example:{port}')],
        [('Host', f'127.0.0.1:{port + 1}')],
        [('Host', authority), ('Origin', f'http://attacker.example:{port}')],
        [('Host', authority), ('Origin', 'null')],
        [('Host', authority), ('Origin', origin), ('Origin', origin)],
    ]
    for headers in rejected_headers:
        assert request(port, '/probe.txt', method, headers)[0] == 403, headers
        assert opened == [], headers

    status, body, headers = request(port, '/probe.txt', method, [('Host', authority)])
    assert status == 200
    assert body == (b'private preview' if method == 'GET' else b'')
    assert 'Access-Control-Allow-Origin' not in headers
    assert request(port, '/probe.txt', method, [('Host', authority), ('Origin', origin)])[0] == 200


def test_external_port_never_owned_or_stopped(tmp_path, registry, monkeypatch):
    monkeypatch.setattr(os, 'kill', lambda *args: pytest.fail('Must never signal a PID'))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(OSError):
            registry.start(str(tmp_path), port)
        assert 'No owned' in registry.stop(port)
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            pass


def test_traversal_symlinks_and_post_start_swaps(tmp_path, registry):
    root = tmp_path / 'served'
    root.mkdir()
    (tmp_path / 'secret.txt').write_text('secret')
    (root / 'safe').mkdir()
    (root / 'safe' / 'file.txt').write_text('safe')
    (root / 'link.txt').symlink_to(tmp_path / 'secret.txt')
    port = free_port()
    registry.start(str(root), port)
    for path in ('/../secret.txt', '/%2e%2e/secret.txt', '/%2e%2e%2fsecret.txt',
                 '/safe/../../secret.txt', '/link.txt', '/safe', '/%00', '/%5c..%5csecret.txt'):
        assert request(port, path)[0] == 404, path
    assert request(port, '/safe/file.txt')[:2] == (200, b'safe')
    (root / 'safe' / 'file.txt').unlink()
    (root / 'safe' / 'file.txt').symlink_to(tmp_path / 'secret.txt')
    assert request(port, '/safe/file.txt')[0] == 404
    (root / 'safe').rename(root / 'old-safe')
    (root / 'safe').symlink_to(tmp_path, target_is_directory=True)
    assert request(port, '/safe/secret.txt')[0] == 404
    os.mkfifo(root / 'pipe')
    assert request(port, '/pipe')[0] == 404


def test_root_symlink_swap_cannot_redirect_reads(tmp_path, registry):
    root = tmp_path / 'served'
    root.mkdir()
    (root / 'file.txt').write_text('safe')
    (tmp_path / 'file.txt').write_text('secret')
    port = free_port()
    registry.start(str(root), port)
    root.rename(tmp_path / 'original')
    root.symlink_to(tmp_path, target_is_directory=True)
    assert request(port, '/file.txt')[:2] == (200, b'safe')


def test_fastmcp_lifespan_cleanup(tmp_path):
    port = free_port()

    async def exercise():
        async with _lifespan(None):
            previews.start(str(tmp_path), port)
        assert not previews._previews

    asyncio.run(exercise())
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))


def test_process_death_releases_listener(tmp_path):
    port = free_port()
    code = '''
import os, sys
from aseprite_mcp.core.preview import previews
previews.start(sys.argv[1], int(sys.argv[2]))
print('ready', flush=True)
sys.stdin.readline()
os._exit(0)
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path), str(port)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        assert request(port, '/')[0] == 404
        process.communicate('\n', timeout=5)
        assert process.returncode == 0
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', port))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize('port', [0, -1, 65536, True, '8000'])
def test_invalid_ports(tmp_path, registry, port):
    with pytest.raises(ValueError):
        registry.start(str(tmp_path), port)


def test_symlink_root_or_ancestor_rejected(tmp_path, registry):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'real' / 'child').mkdir()
    (tmp_path / 'alias').symlink_to(tmp_path / 'real', target_is_directory=True)
    for directory in (tmp_path / 'alias', tmp_path / 'alias' / 'child'):
        with pytest.raises(OSError):
            registry.start(str(directory), free_port())
    assert not registry._previews


def test_lifespan_cleans_up_on_error(tmp_path):
    async def exercise():
        with pytest.raises(RuntimeError):
            async with _lifespan(None):
                previews.start(str(tmp_path), free_port())
                raise RuntimeError('session cancelled')
        assert not previews._previews

    asyncio.run(exercise())


def test_tool_validates_workspace_before_start(tmp_path, monkeypatch):
    from aseprite_mcp.core.workspace import Workspace
    from aseprite_mcp.tools import preview as tool

    root = tmp_path / 'workspace'
    root.mkdir()
    monkeypatch.setattr(tool, 'workspace', Workspace(root))
    port = free_port()
    try:
        assert 'Error' in asyncio.run(tool.start_preview_server(str(tmp_path), port))
        assert 'No owned' in asyncio.run(tool.stop_preview_server(port))
        assert '127.0.0.1' in asyncio.run(tool.start_preview_server(str(root), port))
        assert 'stopped' in asyncio.run(tool.stop_preview_server(port))
    finally:
        previews.close_all()


def test_root_serves_index_without_listing(tmp_path, registry):
    port = free_port()
    registry.start(str(tmp_path), port)
    assert request(port, '/')[0] == 404
    (tmp_path / 'index.html').write_bytes(b'<html>preview</html>')
    assert request(port, '/')[:2] == (200, b'<html>preview</html>')
    status, body, headers = request(port, '/', 'HEAD')
    assert (status, body) == (200, b'')
    assert headers['Content-Length'] == str(len(b'<html>preview</html>'))
    (tmp_path / 'index.html').unlink()
    (tmp_path / 'index.html').symlink_to(tmp_path.parent / 'outside.html')
    assert request(port, '/')[0] == 404

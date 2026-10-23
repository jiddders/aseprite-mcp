import atexit
import mimetypes
import os
import shutil
import stat
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import unquote, urlsplit


def _directory_fd(directory: str) -> int:
    if not hasattr(os, 'O_NOFOLLOW') or os.open not in os.supports_dir_fd:
        raise ValueError('Preview requires descriptor-relative no-follow file access')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in directory.split('/'):
            if part:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class _PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, directory: str, port: int):
        self.root_lock = Lock()
        self.root_fd = _directory_fd(directory)
        try:
            super().__init__(('127.0.0.1', port), _PreviewHandler)
        except BaseException:
            os.close(self.root_fd)
            raise

    def open_file(self, parts: list[str]):
        with self.root_lock:
            fd = os.dup(self.root_fd)
        try:
            for i, part in enumerate(parts):
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if i < len(parts) - 1:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Only regular files can be previewed')
            stream = os.fdopen(fd, 'rb')
            fd = -1
            return stream
        finally:
            if fd >= 0:
                os.close(fd)

    def close_root(self):
        with self.root_lock:
            if self.root_fd >= 0:
                os.close(self.root_fd)
                self.root_fd = -1


class _PreviewHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def _serve(self, *, head: bool):
        authority = f'127.0.0.1:{self.server.server_port}'
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        if hosts != [authority] or (origins and origins != [f'http://{authority}']):
            self.send_error(403, 'Preview authority not allowed')
            return
        try:
            path = unquote(urlsplit(self.path).path, errors='strict')
            parts = ['index.html'] if path == '/' else path.lstrip('/').split('/')
            if any(part in ('', '.', '..') or '\\' in part or '\x00' in part for part in parts):
                raise ValueError('Invalid preview path')
            stream = self.server.open_file(parts)
        except (OSError, ValueError, UnicodeError):
            self.send_error(404, 'File not available')
            return
        with stream:
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(parts[-1])[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if not head:
                try:
                    shutil.copyfileobj(stream, self.wfile)
                except (OSError, TimeoutError):
                    pass


@dataclass
class Preview:
    directory: str
    server: _PreviewServer
    thread: Thread


class PreviewRegistry:
    def __init__(self):
        self._previews: dict[int, Preview] = {}
        self._lock = Lock()

    def start(self, directory: str, port: int) -> str:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Preview port must be an integer from 1 to 65535')
        with self._lock:
            existing = self._previews.get(port)
            if existing:
                if existing.directory != directory:
                    raise ValueError(f'Preview port {port} already serves another directory')
                return f'Preview server started: http://127.0.0.1:{port}/'
            server = _PreviewServer(directory, port)
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            try:
                thread.start()
            except BaseException:
                server.server_close()
                server.close_root()
                raise
            self._previews[port] = Preview(directory, server, thread)
            return f'Preview server started: http://127.0.0.1:{port}/'

    def stop(self, port: int) -> str:
        with self._lock:
            preview = self._previews.pop(port, None)
            if preview is None:
                return f'No owned preview server on port {port}'
            self._close(preview)
            return f'Preview server stopped on port {port}'

    @staticmethod
    def _close(preview: Preview):
        preview.server.shutdown()
        preview.thread.join()
        preview.server.server_close()
        preview.server.close_root()

    def close_all(self) -> None:
        with self._lock:
            for preview in self._previews.values():
                self._close(preview)
            self._previews.clear()


previews = PreviewRegistry()
atexit.register(previews.close_all)

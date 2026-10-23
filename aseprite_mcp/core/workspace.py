from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class Workspace:
    root: Path

    def __post_init__(self):
        root = Path(self.root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Workspace must be an existing directory")
        object.__setattr__(self, "root", root)

    def _path(self, raw: str) -> Path:
        if not isinstance(raw, str) or not raw:
            raise ValueError("Path must be a nonempty string")
        if any(ord(c) < 32 or ord(c) == 127 for c in raw):
            raise ValueError("Path contains control characters")
        if any(c in raw for c in '{}*?[]%') or '\\' in raw:
            raise ValueError("Path contains template, glob, or backslash syntax")
        if '..' in raw.split('/'):
            raise ValueError("Parent directory traversal is not allowed")
        path = Path(raw)
        if not path.is_absolute():
            path = self.root / path
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ValueError("Path is outside the configured workspace") from None
        current = self.root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Symlinks are not allowed in workspace paths")
        return path

    def input_file(self, raw: str) -> str:
        path = self._path(raw)
        if not path.is_file():
            raise ValueError(f"File not found: {raw}")
        return str(path)

    def input_dir(self, raw: str) -> str:
        path = self._path(raw)
        if not path.is_dir():
            raise ValueError(f"Directory not found: {raw}")
        return str(path)

    def output_file(self, raw: str) -> str:
        path = self._path(raw)
        if path.exists() and not path.is_file():
            raise ValueError("Output must be a regular file")
        return str(path)

    def output_dir(self, raw: str) -> str:
        path = self._path(raw)
        if path.exists() and not path.is_dir():
            raise ValueError("Output must be a directory")
        return str(path)

    def descriptor_child(self, directory: str, raw: str) -> str:
        parent = Path(self.input_dir(directory))
        if not isinstance(raw, str) or Path(raw).is_absolute():
            raise ValueError("Descriptor sheet must be a relative child path")
        return self.input_file(str(parent / raw))

    @contextmanager
    def scratch(self):
        with tempfile.TemporaryDirectory(prefix='.aseprite-mcp-', dir=self.root) as directory:
            yield self.input_dir(directory)


workspace = Workspace(Path(os.environ.get('ASEPRITE_WORKSPACE', os.getcwd())))
UNSAFE_LUA = os.environ.get('ASEPRITE_UNSAFE_LUA') == '1'

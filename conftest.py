"""Give every pytest process one owned workspace before collecting tests."""

import os
from pathlib import Path
import tempfile


_test_directory = tempfile.TemporaryDirectory(prefix="ase-pytest-", dir="/tmp")
os.environ["ASEPRITE_WORKSPACE"] = str(Path(_test_directory.name).resolve())


def pytest_sessionfinish(session, exitstatus):
    _test_directory.cleanup()

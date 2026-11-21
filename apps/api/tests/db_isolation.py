"""Unique SQLite paths per test process. Never point at an existing DB.

Each independently launched pytest process (including exec-started xdist
workers) owns a separate temporary root. Module filenames stay separate and
the root is removed at normal process exit. Forking after this module was
imported is not supported. Explicit database overrides remain the caller's responsibility.
"""
from pathlib import Path
from tempfile import TemporaryDirectory


_TEST_ROOT = TemporaryDirectory(prefix="coupon-go-pytest-")


def sqlite_test_path(filename: str) -> str:
    if Path(filename).name != filename or filename in ("", ".", ".."):
        raise ValueError("test DB filename must be a plain basename")
    return str(Path(_TEST_ROOT.name) / filename)

"""Shadow suite never touches the database.

The marker applies only to items collected under ``tests/shadow``. A
session-wide mark skips the jobs-table wipe in ``tests/conftest.py`` and
breaks ``test_jobs.py::test_worker_next_returns_204_when_empty``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_SHADOW = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    mark = pytest.mark.no_db
    for item in items:
        path = Path(str(item.path)).resolve()
        if path == _SHADOW or _SHADOW in path.parents:
            item.add_marker(mark)

"""Shadow suite never touches the database."""
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    mark = pytest.mark.no_db
    for item in items:
        item.add_marker(mark)

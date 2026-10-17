import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_db

_spec = importlib.util.spec_from_file_location(
    "drive_resolver_tick", Path(__file__).resolve().parents[1] / "scripts" / "drive_resolver_tick.py")
tick = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tick)


def test_ruleset_allowed_urls_are_candidates_with_scheme():
    c = SimpleNamespace(source_metadata={
        "reference_materials": [],
        "detail": {"reference_material": [{"url": "https://docs.google.com/document/d/x/edit"}]},
        "ruleset": {"source": {"allowed_urls": ["youtube.com/@Gattouze", "https://kick.com/gattouz0", "", 3]}},
    })
    urls = tick._candidate_urls(c)
    assert "https://youtube.com/@Gattouze" in urls
    assert "https://kick.com/gattouz0" in urls
    assert len(urls) == 3

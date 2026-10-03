"""#56 — the decider may return hook_end and must not invent one."""
import importlib.util
from pathlib import Path


def _mod():
    path = Path(__file__).resolve().parents[2] / "scripts" / "grok_clip_decider_tick.py"
    spec = importlib.util.spec_from_file_location("grok_clip_decider_tick", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_hook_end_is_optional_and_rounded():
    mod = _mod()
    assert mod._hook_end(None) is None
    assert mod._hook_end("") is None
    assert mod._hook_end("later") is None
    assert mod._hook_end(1.234) == 1.23
    assert mod._hook_end("2") == 2.0
    assert mod._hook_end(0, 10) is None
    assert mod._hook_end(-1.2, 10) is None
    assert mod._hook_end(10.01, 10) is None
    assert mod._hook_end(1.5, 10) == 1.5
    assert mod._hook_end(10, 10) == 10.0

"""LangGraph clip-decider that runs in shadow: same inputs, zero DB writes."""

from app.shadow.decider_graph.state import ShadowRuntime

__all__ = ["build_graph", "run_decider", "ShadowRuntime"]


def __getattr__(name: str):
    # graph.py needs langgraph; keep package import cheap for nodes-only tests.
    if name in {"build_graph", "run_decider"}:
        from app.shadow.decider_graph.graph import build_graph, run_decider

        return build_graph if name == "build_graph" else run_decider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

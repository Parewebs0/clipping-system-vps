"""Compile the shadow clip-decider graph.

Nodes never write to the DB. `run_decider` wraps the session as read-only and
always sets shadow=True so human_approval records the interrupt instead of waiting.
"""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.shadow.decider_graph import nodes
from app.shadow.decider_graph.state import DeciderState, ShadowRuntime


def _bind(fn: Callable[[DeciderState, ShadowRuntime], dict[str, Any]]):
    def wrapped(state: DeciderState, config: RunnableConfig) -> dict[str, Any]:
        conf = (config or {}).get("configurable") or {}
        runtime = conf.get("runtime") or ShadowRuntime()
        return fn(state, runtime)

    wrapped.__name__ = fn.__name__
    return wrapped


def build_graph(*, checkpointer: Any = None):
    g = StateGraph(DeciderState)
    g.add_node("load_context", _bind(nodes.load_context))
    g.add_node("extract_rules", _bind(nodes.extract_rules))
    g.add_node("propose_clips", _bind(nodes.propose_clips))
    g.add_node("score_clips", _bind(nodes.score_clips))
    g.add_node("need_more_context", _bind(nodes.need_more_context))
    g.add_node("decide", _bind(nodes.decide))
    g.add_node("human_approval", _bind(nodes.human_approval))
    g.add_node("report", _bind(nodes.report))

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "extract_rules")
    g.add_edge("extract_rules", "propose_clips")
    g.add_edge("propose_clips", "score_clips")
    g.add_conditional_edges(
        "score_clips",
        nodes.route_after_score,
        {"need_more_context": "need_more_context", "decide": "decide"},
    )
    g.add_edge("need_more_context", "score_clips")
    g.add_edge("decide", "human_approval")
    g.add_edge("human_approval", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)


def run_decider(asset_id: str, runtime: ShadowRuntime | None = None) -> dict[str, Any]:
    runtime = runtime or ShadowRuntime()
    runtime.db = nodes.wrap_db(runtime.db)
    runtime.shadow = True
    graph = build_graph()
    return graph.invoke(
        {"asset_id": str(asset_id), "shadow": True},
        config={"configurable": {"runtime": runtime, "thread_id": str(asset_id)}},
    )

"""Graph-level checks for the SuperLocalMemory guidance seam.

Asserts that the SLM rerank in ``_retrieve_guidance`` is purely additive: with
SLM disabled, the node returns the same retrieved-guidance list the
deterministic baseline produces. With a stub SLM client, the node uses the
reranked list and emits an SLM note.
"""

from __future__ import annotations

from typing import Any

import pytest

from warships.agentic import graph as graph_module
from warships.agentic.retrieval import retrieve_doctrine_guidance


TASK = "fix clan hydration regression"


def _baseline_state() -> dict[str, Any]:
    return {"task": TASK, "guidance_notes": []}


def test_retrieve_guidance_baseline_matches_deterministic_retrieval(monkeypatch):
    """With SLM disabled, the node passes the deterministic list through."""

    monkeypatch.setattr(graph_module, "get_slm_client", lambda context: None)

    expected = retrieve_doctrine_guidance(TASK, limit=3)
    result = graph_module._retrieve_guidance(_baseline_state())

    assert result["status"] == "guidance_loaded"
    assert result["retrieved_guidance"] == expected
    # Baseline path must not mention SLM in notes.
    assert not any("SuperLocalMemory" in note for note in result["guidance_notes"])


def test_retrieve_guidance_uses_slm_when_client_present(monkeypatch):
    """A non-None SLM client triggers ensure_corpus_indexed and rerank."""

    sentinel_client = object()
    rerank_calls: dict[str, Any] = {}
    index_calls: list[Any] = []

    reranked = [
        {
            "path": "agents/runbooks/runbook-from-slm.md",
            "title": "From SLM",
            "excerpt": "stub",
            "doc_type": "runbook",
            "slm_score": 0.9,
        }
    ]

    def fake_get_client(context):
        return sentinel_client

    def fake_index(client):
        index_calls.append(client)
        return {"indexed": 1, "skipped": 2, "errors": 0, "files": 3}

    def fake_rerank(client, task, candidates, limit):
        rerank_calls["task"] = task
        rerank_calls["candidates"] = candidates
        rerank_calls["limit"] = limit
        return reranked

    monkeypatch.setattr(graph_module, "get_slm_client", fake_get_client)
    monkeypatch.setattr(graph_module, "slm_ensure_corpus_indexed", fake_index)
    monkeypatch.setattr(graph_module, "slm_rerank_guidance", fake_rerank)

    result = graph_module._retrieve_guidance(_baseline_state())

    assert index_calls == [sentinel_client]
    assert rerank_calls["task"] == TASK
    assert result["retrieved_guidance"] == reranked
    assert any(
        "SuperLocalMemory reranked guidance" in note
        for note in result["guidance_notes"]
    )


def test_graph_module_no_longer_references_hindsight():
    """Regression guard: Phase 4 removed Hindsight in full."""

    source = graph_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as fh:
        text = fh.read()

    assert "hindsight" not in text.lower(), (
        "graph.py should no longer mention hindsight after the SLM migration"
    )
    # Sanity: confirm SLM wiring is in place.
    assert "superlocalmemory" in text.lower()
    assert "slm_rerank_guidance" in text


def test_graph_compile_does_not_pass_store_argument():
    """Phase 4 removed the store= argument from graph_builder.compile."""

    source = graph_module.__file__
    assert source is not None
    with open(source, encoding="utf-8") as fh:
        text = fh.read()

    # The compile call should not pass a store= keyword.
    assert "store=" not in text, (
        "graph_builder.compile should no longer take a store= argument"
    )

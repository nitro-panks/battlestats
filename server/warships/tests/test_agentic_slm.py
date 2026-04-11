"""Tests for the optional SuperLocalMemory wrapper.

The wrapper has a relative ``from .retrieval import GUIDANCE_GLOBS`` so it
must be imported through the regular package machinery rather than via
``importlib.spec_from_file_location``. The upstream ``superlocalmemory``
package is patched in/out of the wrapper module so the tests run with or
without the optional dependency installed.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest


def _load_module():
    module = importlib.import_module("warships.agentic.superlocalmemory")
    return importlib.reload(module)


def _clear_slm_env(monkeypatch):
    for key in (
        "BATTLESTATS_SLM_ENABLED",
        "BATTLESTATS_SLM_MODE",
        "BATTLESTATS_SLM_DB_PATH",
        "BATTLESTATS_SLM_REINDEX_ON_BOOT",
    ):
        monkeypatch.delenv(key, raising=False)


def test_slm_config_disabled_by_default(monkeypatch):
    _clear_slm_env(monkeypatch)
    module = _load_module()

    summary = module.get_slm_config_summary()

    assert summary["enabled"] is False
    assert summary["configured"] is False
    assert summary["mode"] == module.DEFAULT_SLM_MODE
    assert summary["reindex_on_boot"] is False
    assert summary["db_path"].endswith("server/logs/agentic/slm/corpus.db")
    assert isinstance(summary["guidance_globs"], list)
    assert summary["guidance_globs"], "guidance_globs should not be empty"


def test_slm_client_returns_none_when_disabled(monkeypatch):
    _clear_slm_env(monkeypatch)
    module = _load_module()

    assert module.get_slm_client() is None


def test_slm_client_returns_none_when_dependency_missing(monkeypatch):
    _clear_slm_env(monkeypatch)
    monkeypatch.setenv("BATTLESTATS_SLM_ENABLED", "1")
    module = _load_module()
    monkeypatch.setattr(module, "_slm", None, raising=False)

    summary = module.get_slm_config_summary()
    assert summary["enabled"] is True
    assert summary["dependency_available"] is False
    assert summary["configured"] is False
    assert module.get_slm_client() is None


def test_slm_client_uses_first_available_factory(monkeypatch, tmp_path):
    _clear_slm_env(monkeypatch)
    monkeypatch.setenv("BATTLESTATS_SLM_ENABLED", "1")
    monkeypatch.setenv("BATTLESTATS_SLM_DB_PATH", str(tmp_path / "corpus.db"))
    module = _load_module()

    calls: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            calls["kwargs"] = kwargs
            self.db_path = kwargs.get("db_path")

    fake_pkg = SimpleNamespace(Client=FakeClient)
    monkeypatch.setattr(module, "_slm", fake_pkg, raising=False)

    client = module.get_slm_client()

    assert isinstance(client, FakeClient)
    assert calls["kwargs"]["mode"] == module.DEFAULT_SLM_MODE
    assert calls["kwargs"]["db_path"].endswith("corpus.db")


def test_slm_ensure_corpus_indexed_idempotent(monkeypatch, tmp_path):
    module = _load_module()

    # Build a tiny fake repo with one indexable doc.
    fake_repo = tmp_path / "repo"
    runbooks = fake_repo / "agents" / "runbooks"
    runbooks.mkdir(parents=True)
    doc = runbooks / "runbook-fake.md"
    doc.write_text("# fake runbook\nbody\n", encoding="utf-8")

    monkeypatch.setattr(module, "_repo_root", lambda: fake_repo)

    remembered: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def __init__(self):
            self.db_path = str(tmp_path / "corpus.db")

        def remember(self, content, metadata=None):
            remembered.append((content, metadata or {}))

    client = FakeClient()

    first = module.ensure_corpus_indexed(client)
    assert first["files"] >= 1
    assert first["indexed"] >= 1
    assert first["errors"] == 0
    assert len(remembered) == first["indexed"]

    # Second call should be a no-op for unchanged files.
    second = module.ensure_corpus_indexed(client)
    assert second["indexed"] == 0
    assert second["skipped"] >= 1
    assert len(remembered) == first["indexed"], (
        "remember should not be called again on unchanged files"
    )


def test_slm_rerank_returns_input_when_no_hits():
    module = _load_module()

    class EmptyClient:
        def recall(self, query, limit=5):
            return []

    candidates = [
        {"path": "agents/runbooks/runbook-a.md", "title": "A", "excerpt": "x"},
        {"path": "agents/runbooks/runbook-b.md", "title": "B", "excerpt": "y"},
    ]
    result = module.rerank_guidance(EmptyClient(), "task text", candidates)

    assert result == candidates


def test_slm_rerank_preserves_baseline_shape_and_adds_score():
    module = _load_module()

    class FakeClient:
        def recall(self, query, limit=5):
            return [
                {"metadata": {"path": "agents/runbooks/runbook-b.md"}},
                {"metadata": {"path": "agents/runbooks/runbook-a.md"}},
            ]

    candidates = [
        {
            "path": "agents/runbooks/runbook-a.md",
            "title": "A",
            "excerpt": "x",
            "doc_type": "runbook",
            "score": 1.0,
        },
        {
            "path": "agents/runbooks/runbook-b.md",
            "title": "B",
            "excerpt": "y",
            "doc_type": "runbook",
            "score": 1.0,
        },
    ]
    result = module.rerank_guidance(FakeClient(), "task text", candidates)

    assert len(result) == 2
    paths = [item["path"] for item in result]
    # Highest SLM score is the first hit (index 0 of recall) → runbook-b first.
    assert paths[0] == "agents/runbooks/runbook-b.md"
    for item in result:
        for key in ("path", "title", "excerpt", "doc_type", "slm_score"):
            assert key in item


def test_slm_rerank_appends_new_hits_outside_baseline():
    module = _load_module()

    class FakeClient:
        def recall(self, query, limit=5):
            return [
                {"metadata": {"path": "agents/runbooks/runbook-new.md"}},
                {"metadata": {"path": "agents/runbooks/runbook-a.md"}},
            ]

    candidates = [
        {
            "path": "agents/runbooks/runbook-a.md",
            "title": "A",
            "excerpt": "x",
            "doc_type": "runbook",
        },
    ]
    result = module.rerank_guidance(FakeClient(), "task", candidates, limit=5)

    paths = {item["path"] for item in result}
    assert "agents/runbooks/runbook-new.md" in paths
    assert "agents/runbooks/runbook-a.md" in paths
    new_hit = next(
        item for item in result
        if item["path"] == "agents/runbooks/runbook-new.md"
    )
    assert new_hit["doc_type"] == "slm-hit"
    assert "slm_score" in new_hit

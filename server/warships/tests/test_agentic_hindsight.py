from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "agentic" / "hindsight.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("battlestats_agentic_hindsight_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hindsight_config_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BATTLESTATS_HINDSIGHT_ENABLED", raising=False)
    monkeypatch.delenv("BATTLESTATS_HINDSIGHT_API_URL", raising=False)
    monkeypatch.delenv("HINDSIGHT_API_KEY", raising=False)

    module = _load_module()
    summary = module.get_hindsight_config_summary()

    assert summary["enabled"] is False
    assert summary["configured"] is False
    assert summary["api_url"] == module.DEFAULT_HINDSIGHT_API_URL
    assert summary["api_key_configured"] is False


def test_hindsight_store_uses_env_configuration(monkeypatch):
    module = _load_module()
    calls: dict[str, object] = {}

    class FakeStore:
        def __init__(self, **kwargs):
            calls["store_kwargs"] = kwargs

    def fake_configure(**kwargs):
        calls["configure_kwargs"] = kwargs

    monkeypatch.setenv("BATTLESTATS_HINDSIGHT_ENABLED", "1")
    monkeypatch.setenv("BATTLESTATS_HINDSIGHT_API_URL", "http://localhost:3000")
    monkeypatch.setenv("HINDSIGHT_API_KEY", "secret")
    monkeypatch.setenv("BATTLESTATS_HINDSIGHT_BUDGET", "high")
    monkeypatch.setenv("BATTLESTATS_HINDSIGHT_MAX_TOKENS", "8192")
    monkeypatch.setenv("BATTLESTATS_HINDSIGHT_TAGS", "project:battlestats,engine:langgraph")
    monkeypatch.setattr(module, "HindsightStore", FakeStore)
    monkeypatch.setattr(module, "configure_hindsight", fake_configure)

    store = module.get_hindsight_store()

    assert isinstance(store, FakeStore)
    assert calls["configure_kwargs"] == {
        "hindsight_api_url": "http://localhost:3000",
        "api_key": "secret",
        "budget": "high",
        "max_tokens": 8192,
        "tags": ["project:battlestats", "engine:langgraph"],
    }
    assert calls["store_kwargs"] == {
        "hindsight_api_url": "http://localhost:3000",
        "api_key": "secret",
        "tags": ["project:battlestats", "engine:langgraph"],
    }
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

try:
    import langsmith as ls
    from langsmith import Client
    from langsmith.run_helpers import get_current_run_tree
except ImportError:  # pragma: no cover - optional dependency guard
    ls = None
    Client = None

    def get_current_run_tree() -> None:
        return None


DEFAULT_LANGSMITH_PROJECT = "battlestats-agentic"
_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def is_langsmith_tracing_enabled() -> bool:
    if ls is None:
        return False

    return any(
        _env_flag(name)
        for name in (
            "LANGSMITH_TRACING_V2",
            "LANGSMITH_TRACING",
            "LANGCHAIN_TRACING_V2",
            "LANGCHAIN_TRACING",
        )
    )


def get_langsmith_project_name() -> str:
    for name in (
        "BATTLESTATS_LANGSMITH_PROJECT",
        "LANGSMITH_PROJECT",
        "LANGCHAIN_PROJECT",
    ):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return DEFAULT_LANGSMITH_PROJECT


@contextmanager
def trace_block(
    name: str,
    *,
    run_type: str = "chain",
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[Any | None]:
    if ls is None or not is_langsmith_tracing_enabled():
        yield None
        return

    parent_run = get_current_run_tree()
    client = None
    if parent_run is None:
        if Client is None:
            yield None
            return
        try:
            client = Client()
        except Exception:
            yield None
            return

    with ls.trace(
        name,
        run_type=run_type,
        inputs=inputs or {},
        metadata=metadata or {},
        tags=tags or [],
        project_name=get_langsmith_project_name(),
        client=client,
    ) as run:
        try:
            yield run
        finally:
            if client is not None:
                client.flush()


def get_current_trace_url() -> str | None:
    if ls is None or not is_langsmith_tracing_enabled():
        return None

    run = get_current_run_tree()
    if run is None:
        return None

    try:
        return run.get_url()
    except Exception:
        return None

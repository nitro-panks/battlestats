from __future__ import annotations

import os
from typing import Any

try:
    from hindsight_langgraph import HindsightStore, configure as configure_hindsight
except ImportError:  # pragma: no cover - optional dependency lane
    HindsightStore = None  # type: ignore[assignment]
    configure_hindsight = None  # type: ignore[assignment]


_TRUTHY = {"1", "true", "yes", "on"}
DEFAULT_HINDSIGHT_API_URL = "https://api.hindsight.vectorize.io"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in _TRUTHY


def _resolve_text(context: dict[str, Any] | None, *names: str) -> str | None:
    resolved_context = context or {}
    for name in names:
        if name in resolved_context:
            value = str(resolved_context.get(name) or "").strip()
            if value:
                return value
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _resolve_tags(context: dict[str, Any] | None) -> list[str] | None:
    resolved_context = context or {}
    if "hindsight_tags" in resolved_context:
        raw_tags = resolved_context.get("hindsight_tags")
        if isinstance(raw_tags, str):
            tags = [item.strip()
                    for item in raw_tags.split(",") if item.strip()]
            return tags or None
        if isinstance(raw_tags, (list, tuple)):
            tags = [str(item).strip()
                    for item in raw_tags if str(item).strip()]
            return tags or None

    env_value = os.getenv("BATTLESTATS_HINDSIGHT_TAGS", "").strip()
    if not env_value:
        return None
    tags = [item.strip() for item in env_value.split(",") if item.strip()]
    return tags or None


def _resolve_int(context: dict[str, Any] | None, key: str, env_name: str, default: int) -> int:
    resolved_context = context or {}
    if key in resolved_context:
        try:
            return int(resolved_context[key])
        except (TypeError, ValueError):
            return default

    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def is_hindsight_enabled(context: dict[str, Any] | None = None) -> bool:
    resolved_context = context or {}
    if "hindsight_enabled" in resolved_context:
        return bool(resolved_context.get("hindsight_enabled"))
    return _env_flag("BATTLESTATS_HINDSIGHT_ENABLED", default=False)


def get_hindsight_config_summary(context: dict[str, Any] | None = None) -> dict[str, Any]:
    api_url = _resolve_text(context, "hindsight_api_url", "BATTLESTATS_HINDSIGHT_API_URL",
                            "HINDSIGHT_API_URL") or DEFAULT_HINDSIGHT_API_URL
    api_key = _resolve_text(context, "hindsight_api_key", "HINDSIGHT_API_KEY")
    budget = _resolve_text(context, "hindsight_budget",
                           "BATTLESTATS_HINDSIGHT_BUDGET") or "mid"
    max_tokens = _resolve_int(
        context, "hindsight_max_tokens", "BATTLESTATS_HINDSIGHT_MAX_TOKENS", 4096)
    tags = _resolve_tags(context)
    enabled = is_hindsight_enabled(context)
    available = HindsightStore is not None and configure_hindsight is not None

    return {
        "enabled": enabled,
        "dependency_available": available,
        "configured": enabled and available,
        "api_url": api_url,
        "api_key_configured": bool(api_key),
        "budget": budget,
        "max_tokens": max_tokens,
        "tags": tags or [],
    }


def get_hindsight_store(context: dict[str, Any] | None = None) -> Any | None:
    summary = get_hindsight_config_summary(context)
    if not summary["enabled"] or not summary["dependency_available"]:
        return None

    if configure_hindsight is None or HindsightStore is None:
        return None

    configure_hindsight(
        hindsight_api_url=summary["api_url"],
        api_key=_resolve_text(
            context, "hindsight_api_key", "HINDSIGHT_API_KEY"),
        budget=summary["budget"],
        max_tokens=summary["max_tokens"],
        tags=summary["tags"] or None,
    )
    return HindsightStore(
        hindsight_api_url=summary["api_url"],
        api_key=_resolve_text(
            context, "hindsight_api_key", "HINDSIGHT_API_KEY"),
        tags=summary["tags"] or None,
    )

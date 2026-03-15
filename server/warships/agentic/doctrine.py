from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


DEFAULT_TEAM_DOCTRINE: dict[str, list[str]] = {
    "preferred_patterns": [
        "Prefer incremental evolution over big-bang rewrites.",
        "Prefer additive API changes when existing consumers must remain stable.",
        "Reuse existing fetch paths, shared components, and validation patterns when practical.",
        "Favor non-blocking background hydration over synchronous page-load fan-out.",
    ],
    "discouraged_patterns": [
        "Avoid new browser-triggered WG API calls when stored or server-fetched data already exists.",
        "Avoid unbounded polling, queue fan-out, or retry loops.",
        "Avoid large unscoped refactors during feature delivery.",
        "Avoid undocumented payload drift between code, tests, and runbooks.",
    ],
    "review_priorities": [
        "Correctness before optimization.",
        "Observable failure modes and validation evidence.",
        "Bounded local and upstream API load.",
        "Rollback clarity and migration safety.",
        "Consistency with existing battlestats UX and contracts.",
    ],
    "decision_rules": [
        "Prefer the smallest safe vertical slice.",
        "Prefer reversible changes over clever shortcuts.",
        "Validate touched areas with focused tests before widening scope.",
        "Preserve current user-facing behavior unless the task explicitly changes it.",
    ],
}

TEAM_DOCTRINE_FILE = "agents/knowledge/agentic-team-doctrine.json"


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    for candidate in current.parents:
        if (candidate / "manage.py").exists():
            return candidate
    return current.parents[3]


def _dedupe_list(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _merge_list_field(base: list[str], override: Any) -> list[str]:
    if override is None:
        return list(base)
    if isinstance(override, str):
        return _dedupe_list([*base, override])
    if isinstance(override, list):
        return _dedupe_list([*base, *override])
    return list(base)


def load_repo_team_doctrine(path: str | None = None) -> dict[str, list[str]]:
    target = _repo_root() / (path or TEAM_DOCTRINE_FILE)
    merged = deepcopy(DEFAULT_TEAM_DOCTRINE)
    if not target.exists():
        return merged

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return merged

    if not isinstance(payload, dict):
        return merged

    for key in DEFAULT_TEAM_DOCTRINE.keys():
        merged[key] = _merge_list_field(merged.get(key, []), payload.get(key))
    return merged


def merge_team_doctrine(
    base: dict[str, list[str]] | None = None,
    overrides: dict[str, Any] | None = None,
    team_style_snippets: list[str] | None = None,
) -> dict[str, list[str]]:
    merged = deepcopy(base or load_repo_team_doctrine())
    resolved_overrides = overrides or {}

    for key in DEFAULT_TEAM_DOCTRINE.keys():
        merged[key] = _merge_list_field(merged.get(key, []), resolved_overrides.get(key))

    style_snippets = _dedupe_list(team_style_snippets or [])
    if style_snippets:
        merged["review_priorities"] = _dedupe_list(
            [*merged.get("review_priorities", []), *style_snippets]
        )

    return merged


def summarize_team_doctrine(doctrine: dict[str, list[str]]) -> dict[str, str]:
    summary: dict[str, str] = {}
    for key, values in doctrine.items():
        if not values:
            summary[key] = "None recorded."
            continue
        summary[key] = "; ".join(values[:3])
    return summary
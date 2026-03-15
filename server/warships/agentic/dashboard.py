from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

from warships.data import (
    PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION,
    PLAYER_RANKED_WR_BATTLES_CORRELATION_CONFIG,
)

from .tracing import get_langsmith_project_name, is_langsmith_tracing_enabled


def _project_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    for candidate in current.parents:
        if (candidate / "manage.py").exists():
            return candidate
    return current.parents[3]


def _log_root() -> Path:
    project_root = _project_root()
    if (project_root / "manage.py").exists():
        return project_root / "logs" / "agentic"
    return project_root / "server" / "logs" / "agentic"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _relative_path(path: Path) -> str:
    repo_root = _project_root()
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _read_payload(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def _recent_log_paths(limit: int) -> list[Path]:
    log_root = _log_root()
    if not log_root.exists():
        return []

    paths = [path for path in log_root.glob("*/*.json") if path.is_file()]
    paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return paths[:limit]


def _common_entries(items: list[str], limit: int = 5) -> list[dict[str, Any]]:
    counter = Counter(item for item in items if item)
    return [
        {"label": label, "count": count}
        for label, count in counter.most_common(limit)
    ]


def _format_decimal(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _build_ranked_heatmap_learning_note() -> dict[str, Any]:
    config = PLAYER_RANKED_WR_BATTLES_CORRELATION_CONFIG
    growth_factor = float(config['x_bin_growth_factor'])
    base_edge = int(config['base_x_edges'][0])
    preview_edges = [base_edge]

    while preview_edges[-1] < 200:
        next_edge = int(round(preview_edges[-1] * growth_factor))
        if next_edge <= preview_edges[-1]:
            next_edge = preview_edges[-1] + 1
        preview_edges.append(next_edge)

    preview_ranges = [
        f"{preview_edges[index]}-{preview_edges[index + 1]}"
        for index in range(min(4, len(preview_edges) - 1))
    ]

    return {
        "slug": "ranked_wr_battles_heatmap",
        "title": "Ranked heatmap granularity",
        "summary": "The ranked battles vs win rate heatmap now uses quarter-octave total-games bins and 0.75-point win-rate bands.",
        "runbook_path": "agents/runbooks/runbook-ranked-wr-battles-heatmap-granularity.md",
        "details": [
            {
                "label": "X growth factor",
                "value": f"2^(1/4) (~{_format_decimal(growth_factor)})",
            },
            {
                "label": "Y bin width",
                "value": f"{_format_decimal(float(config['y_bin_width']))} win-rate points",
            },
            {
                "label": "Major x ticks",
                "value": "50, 100, 200, 400, ...",
            },
            {
                "label": "Early x bins",
                "value": ", ".join(preview_ranges),
            },
            {
                "label": "Cache version",
                "value": PLAYER_RANKED_WR_BATTLES_CORRELATION_CACHE_VERSION,
            },
        ],
    }


def _extract_run_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    engine = path.parent.name
    langgraph_result = payload.get("langgraph_result") if isinstance(
        payload.get("langgraph_result"), dict) else {}
    crew_result = payload.get("crew_result") if isinstance(
        payload.get("crew_result"), dict) else {}
    verification_source = langgraph_result or payload
    command_results = verification_source.get("command_results") if isinstance(
        verification_source.get("command_results"), list) else []
    issues = verification_source.get("issues") if isinstance(
        verification_source.get("issues"), list) else []
    verification_commands = verification_source.get("verification_commands") if isinstance(
        verification_source.get("verification_commands"), list) else []
    touched_files = verification_source.get("touched_files") if isinstance(
        verification_source.get("touched_files"), list) else []
    summary = payload.get("summary") if isinstance(payload.get(
        "summary"), list) else verification_source.get("summary", [])
    task = verification_source.get("task") or payload.get("task")
    logged_at = payload.get("logged_at") or verification_source.get(
        "logged_at") or crew_result.get("logged_at")
    route_rationale = payload.get("route_rationale")
    selected_engine = payload.get("selected_engine") or engine
    trace_url = payload.get("langsmith_trace_url") or verification_source.get(
        "langsmith_trace_url") or crew_result.get("langsmith_trace_url")
    checks_passed = verification_source.get("checks_passed") if isinstance(
        verification_source.get("checks_passed"), bool) else None
    boundary_ok = verification_source.get("boundary_ok") if isinstance(
        verification_source.get("boundary_ok"), bool) else None

    return {
        "workflow_id": str(payload.get("workflow_id") or verification_source.get("workflow_id") or path.stem),
        "engine": engine,
        "selected_engine": selected_engine,
        "status": str(payload.get("status") or verification_source.get("status") or "unknown"),
        "task": str(task or "No task recorded."),
        "logged_at": logged_at,
        "route_rationale": route_rationale,
        "summary": [str(item) for item in summary[:3]],
        "checks_passed": checks_passed,
        "boundary_ok": boundary_ok,
        "issue_count": len(issues),
        "command_failure_count": len([result for result in command_results if not result.get("ok")]),
        "verification_command_count": len(verification_commands),
        "touched_file_count": len(touched_files),
        "langsmith_trace_url": trace_url,
        "run_log_path": _relative_path(path),
    }


def get_agentic_trace_dashboard(limit: int = 12) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    raw_payloads: list[dict[str, Any]] = []

    for path in _recent_log_paths(limit):
        payload = _read_payload(path)
        if payload is None:
            continue
        raw_payloads.append(payload)
        runs.append(_extract_run_summary(path, payload))

    runs.sort(
        key=lambda run: _parse_timestamp(run.get("logged_at")) or datetime.min,
        reverse=True,
    )

    verification_runs = [run for run in runs if run.get(
        "checks_passed") is not None]
    verification_pass_rate = None
    if verification_runs:
        verification_pass_rate = round(
            100 * sum(1 for run in verification_runs if run.get("checks_passed")
                      ) / len(verification_runs),
            1,
        )

    recurring_issues: list[str] = []
    verification_commands: list[str] = []
    touched_files: list[str] = []
    route_rationales: list[str] = []
    for payload in raw_payloads:
        verification_source = payload.get("langgraph_result") if isinstance(
            payload.get("langgraph_result"), dict) else payload
        issues = verification_source.get("issues") if isinstance(
            verification_source.get("issues"), list) else []
        commands = verification_source.get("verification_commands") if isinstance(
            verification_source.get("verification_commands"), list) else []
        files = verification_source.get("touched_files") if isinstance(
            verification_source.get("touched_files"), list) else []
        recurring_issues.extend(str(issue) for issue in issues)
        verification_commands.extend(str(command) for command in commands)
        touched_files.extend(str(file_path) for file_path in files)
        if payload.get("route_rationale"):
            route_rationales.append(str(payload["route_rationale"]))

    api_host = _env_value("LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT")
    api_key_configured = _env_value(
        "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY") is not None

    return {
        "project_name": get_langsmith_project_name(),
        "tracing_enabled": is_langsmith_tracing_enabled(),
        "api_key_configured": api_key_configured,
        "api_host": api_host,
        "recent_runs": runs,
        "diagnostics": {
            "total_runs": len(runs),
            "runs_with_trace_urls": sum(1 for run in runs if run.get("langsmith_trace_url")),
            "boundary_block_count": sum(1 for run in runs if run.get("boundary_ok") is False),
            "verification_pass_rate": verification_pass_rate,
            "engine_mix": dict(Counter(run["selected_engine"] for run in runs)),
            "status_mix": dict(Counter(run["status"] for run in runs)),
            "latest_logged_at": runs[0]["logged_at"] if runs else None,
        },
        "learning": {
            "recurring_issues": _common_entries(recurring_issues),
            "common_verification_commands": _common_entries(verification_commands),
            "common_touched_files": _common_entries(touched_files),
            "common_route_rationales": _common_entries(route_rationales),
            "chart_tuning_notes": [_build_ranked_heatmap_learning_note()],
        },
    }

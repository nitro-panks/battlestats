from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


_SENSITIVE_KEY_RE = re.compile(
    r"(?:password|secret|token|api[_-]?key|credential|private[_-]?key|authorization|cookie)",
    re.IGNORECASE,
)
_CONNECTION_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:/\s:@]+:)([^@\s]+)(@)")
_ASSIGNMENT_SECRET_RE = re.compile(
    r"((?:password|secret|token|api[_-]?key|authorization)\s*[=:]\s*)([^,\s]+)",
    re.IGNORECASE,
)


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


def _redact_string(value: str) -> str:
    redacted = _CONNECTION_URL_RE.sub(r"\1[REDACTED]\3", value)
    return _ASSIGNMENT_SECRET_RE.sub(r"\1[REDACTED]", redacted)


def _redact_payload(value: Any, key_path: tuple[str, ...] = ()) -> Any:
    if key_path and _SENSITIVE_KEY_RE.search(key_path[-1]):
        return "[REDACTED]"

    if isinstance(value, dict):
        return {
            str(key): _redact_payload(item, key_path + (str(key),))
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [_redact_payload(item, key_path) for item in value]

    if isinstance(value, tuple):
        return [_redact_payload(item, key_path) for item in value]

    if isinstance(value, str):
        return _redact_string(value)

    return value


def write_agent_run_log(engine: str, payload: dict[str, Any]) -> str:
    workflow_id = str(payload.get("workflow_id")
                      or f"{engine}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
    target_dir = _log_root() / engine
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{workflow_id}.json"

    enriched_payload = _redact_payload(dict(payload))
    enriched_payload.setdefault(
        "logged_at", datetime.utcnow().isoformat() + "Z")
    target_file.write_text(json.dumps(
        enriched_payload, indent=2), encoding="utf-8")
    return str(target_file)

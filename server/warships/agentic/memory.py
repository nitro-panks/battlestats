from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator, Literal

try:
    from langgraph.store.memory import InMemoryStore
except ImportError:  # pragma: no cover - dependency is installed in the repo env
    InMemoryStore = None  # type: ignore[assignment]

try:
    from langgraph.store.postgres import PostgresStore
except ImportError:  # pragma: no cover - optional backend
    PostgresStore = None  # type: ignore[assignment]

MemoryType = Literal["procedural", "episodic", "operational"]
MemoryBackend = Literal["file", "langgraph_memory", "langgraph_postgres"]
MEMORY_STORE_VERSION = 1
PHASE0_MEMORY_LIMIT = 3
_REVIEWED_STATUSES = {"reviewed", "approved"}
_PENDING_REVIEW_STATUS = "pending_review"
_SUPERSEDED_STATUS = "superseded"
_REJECTED_STATUS = "rejected"
_PENDING_NAMESPACE_KEY = "pending_review"

_LANGGRAPH_IN_MEMORY_STORE: InMemoryStore | None = None


def _project_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    for candidate in current.parents:
        if (candidate / "manage.py").exists():
            return candidate
    return current.parents[3]


def _memory_root() -> Path:
    project_root = _project_root()
    if (project_root / "manage.py").exists():
        return project_root / "logs" / "agentic" / "memory"
    return project_root / "server" / "logs" / "agentic" / "memory"


def _reviewed_root() -> Path:
    return _memory_root() / "reviewed"


def _pending_root() -> Path:
    return _memory_root() / "pending"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _normalize_environment(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"", "dev", "development", "local"}:
        return "local"
    if normalized in {"stage", "staging", "test", "qa"}:
        return "staging"
    if normalized in {"prod", "production", "prod-agentic"}:
        return "prod-agentic"
    return normalized or "local"


def get_memory_environment() -> str:
    return _normalize_environment(
        os.getenv("BATTLESTATS_AGENTIC_ENV")
        or os.getenv("BATTLESTATS_ENV")
        or os.getenv("DJANGO_ENV")
    )


def get_memory_namespace(memory_type: MemoryType, environment: str | None = None) -> tuple[str, str, str]:
    return (
        "battlestats",
        _normalize_environment(
            environment) if environment else get_memory_environment(),
        memory_type,
    )


def _pending_namespace(environment: str, workflow_id: str) -> tuple[str, str, str, str]:
    return ("battlestats", _normalize_environment(environment), _PENDING_NAMESPACE_KEY, workflow_id)


def _reviewed_namespace_uri(namespace: tuple[str, ...], backend: str) -> str:
    return f"langgraph://{backend}/{'/'.join(namespace)}"


def _pending_namespace_uri(namespace: tuple[str, ...], backend: str) -> str:
    return f"langgraph://{backend}/{'/'.join(namespace)}"


def get_memory_postgres_url(context: dict[str, Any] | None = None) -> str | None:
    resolved_context = context or {}
    candidates = [
        resolved_context.get("memory_postgres_url"),
    os.getenv("BATTLESTATS_AGENTIC_MEMORY_POSTGRES_URL"),
    os.getenv("LANGGRAPH_STORE_POSTGRES_URL"),
    os.getenv("LANGGRAPH_CHECKPOINT_POSTGRES_URL"),
    ]
    for value in candidates:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return None


def get_memory_backend(context: dict[str, Any] | None = None) -> MemoryBackend:
    resolved_context = context or {}
    explicit = str(
        resolved_context.get("memory_backend")
    or os.getenv("BATTLESTATS_AGENTIC_MEMORY_BACKEND")
    or os.getenv("BATTLESTATS_LANGMEM_BACKEND")
    or "file"
    ).strip().lower()
    if explicit in {"", "file", "filesystem", "json"}:
        return "file"
    if explicit in {"memory", "in-memory", "in_memory", "langgraph-memory", "langgraph_memory"}:
        return "langgraph_memory"
    if explicit in {"postgres", "langgraph-postgres", "langgraph_postgres"}:
        return "langgraph_postgres"
    if explicit == "auto":
        if get_memory_postgres_url(resolved_context):
            return "langgraph_postgres"
        if InMemoryStore is not None:
            return "langgraph_memory"
        return "file"
    return "file"


def is_phase0_memory_enabled(engine: str, context: dict[str, Any] | None = None) -> bool:
    if engine.strip().lower() != "langgraph":
        return False

    if context and "memory_enabled" in context:
        return bool(context.get("memory_enabled"))

    return _env_flag("BATTLESTATS_LANGMEM_ENABLED", default=False)


def infer_workflow_kind(
    task: str,
    touched_files: list[str] | None = None,
    verification_commands: list[str] | None = None,
) -> str:
    normalized_task = task.lower()
    file_hints = " ".join((touched_files or [])).lower()
    command_hints = " ".join((verification_commands or [])).lower()
    corpus = " ".join([normalized_task, file_hints, command_hints])

    if any(token in corpus for token in ("playwright", "e2e", "route smoke", "browser")):
        return "client_route_smoke"
    if any(token in corpus for token in ("cache", "ttl", "hydrate", "warming", "poll", "stale")):
        return "cache_behavior"
    if any(token in corpus for token in ("api", "serializer", "endpoint", "payload", "contract")):
        return "api_contract_change"
    if any(token in corpus for token in ("trace", "langgraph", "crewai", "agentic", "checkpoint")):
        return "agentic_workflow"
    if any(token in corpus for token in ("upstream", "encyclopedia", "wargaming", "contract review")):
        return "upstream_contract_review"
    if any(token in corpus for token in ("performance", "slow", "latency", "regression")):
        return "performance_regression"
    return "agentic_workflow"


def _normalize_review_status(record: dict[str, Any]) -> str:
    return str(record.get("review_status") or "").strip().lower()


def _normalize_confidence(record: dict[str, Any]) -> float:
    try:
        return float(record.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        return datetime.min
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min


def _normalize_created_at(record: dict[str, Any]) -> datetime:
    return _normalize_datetime(
        record.get("reviewed_at")
        or record.get("updated_at")
        or record.get("created_at")
    )


def _relative_memory_path(path: Path) -> str:
    repo_root=_project_root()
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _namespace_slug(namespace: tuple[str, str, str]) -> str:
    return "__".join(str(part).replace("/", "_") for part in namespace)


def _reviewed_namespace_path(namespace: tuple[str, str, str]) -> Path:
    return _reviewed_root() / f"{_namespace_slug(namespace)}.json"


def _pending_workflow_path(workflow_id: str) -> Path:
    return _pending_root() / f"{workflow_id}.json"


def _read_json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)

    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)

    return payload if isinstance(payload, dict) else dict(default)


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2,
                    sort_keys=True) + "\n", encoding="utf-8")


def _dedupe_strings(values: list[str], limit: int | None=None) -> list[str]:
    deduped: list[str]=[]
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    if limit is not None:
        return deduped[:limit]
    return deduped


def _record_identity(record: dict[str, Any]) -> str:
    namespace=tuple(record.get("namespace") or ())
    raw="::".join([
        "/".join(str(part) for part in namespace),
        str(record.get("memory_type") or ""),
        str(record.get("workflow_kind") or ""),
        str(record.get("summary") or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _merge_evidence(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_paths": _dedupe_strings([
            *[str(item) for item in existing.get("file_paths", [])],
            *[str(item) for item in incoming.get("file_paths", [])],
        ], limit=8),
        "validation_commands": _dedupe_strings([
            *[str(item) for item in existing.get("validation_commands", [])],
            *[str(item) for item in incoming.get("validation_commands", [])],
        ], limit=6),
        "guidance_paths": _dedupe_strings([
            *[str(item) for item in existing.get("guidance_paths", [])],
            *[str(item) for item in incoming.get("guidance_paths", [])],
        ], limit=6),
        "trace_url": incoming.get("trace_url") or existing.get("trace_url"),
        "run_log_path": incoming.get("run_log_path") or existing.get("run_log_path"),
    }


def _merge_reviewed_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged=dict(existing)
    merged.update({
        "summary": incoming.get("summary") or existing.get("summary"),
        "detail": incoming.get("detail") or existing.get("detail"),
        "confidence": max(_normalize_confidence(existing), _normalize_confidence(incoming)),
        "engine": incoming.get("engine") or existing.get("engine"),
        "source_run_id": incoming.get("source_run_id") or existing.get("source_run_id"),
        "review_status": "reviewed",
        "reviewed_at": incoming.get("reviewed_at") or existing.get("reviewed_at"),
        "reviewed_by": incoming.get("reviewed_by") or existing.get("reviewed_by"),
        "updated_at": incoming.get("updated_at") or datetime.utcnow().isoformat() + "Z",
        "provenance": incoming.get("provenance") or existing.get("provenance") or {},
    })
    merged["supersedes"]=_dedupe_strings([
        *[str(item) for item in existing.get("supersedes", [])],
        *[str(item) for item in incoming.get("supersedes", [])],
    ])
    merged["evidence"]=_merge_evidence(
        existing.get("evidence") if isinstance(
            existing.get("evidence"), dict) else {},
        incoming.get("evidence") if isinstance(
            incoming.get("evidence"), dict) else {},
    )
    return merged


def load_reviewed_memory_records(namespace: tuple[str, str, str]) -> list[dict[str, Any]]:
    payload=_read_json_file(
        _reviewed_namespace_path(namespace),
        {"version": MEMORY_STORE_VERSION,
            "namespace": list(namespace), "records": []},
    )
    records=payload.get("records")
    return [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []


def _store_reviewed_memory_records(namespace: tuple[str, str, str], records: list[dict[str, Any]]) -> str:
    path=_reviewed_namespace_path(namespace)
    _write_json_file(
        path,
        {
            "version": MEMORY_STORE_VERSION,
            "namespace": list(namespace),
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "records": records,
        },
    )
    return _relative_memory_path(path)


def _candidate_provenance(result: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_run_id": result.get("workflow_id") or candidate.get("source_run_id"),
        "engine": result.get("selected_engine") or candidate.get("engine"),
        "workflow_kind": result.get("workflow_kind") or candidate.get("workflow_kind"),
        "trace_url": result.get("langsmith_trace_url"),
        "comparison_paths": list(candidate.get("comparison_paths", []))[:4],
        "evidence": candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {},
    }


def queue_memory_candidates(result: dict[str, Any]) -> dict[str, Any]:
    candidates=result.get("memory_candidates") if isinstance(
        result.get("memory_candidates"), list) else []
    workflow_id=str(result.get("workflow_id") or "").strip()
    if not candidates or not workflow_id:
        return {
            "queued_candidate_count": 0,
            "candidate_queue_path": None,
        }

    path=_pending_workflow_path(workflow_id)
    payload=_read_json_file(
        path,
        {
            "version": MEMORY_STORE_VERSION,
            "workflow_id": workflow_id,
            "workflow_kind": result.get("workflow_kind"),
            "memory_environment": result.get("memory_environment"),
            "namespace": list(result.get("memory_namespace", [])),
            "candidates": [],
        },
    )
    existing_by_id={
        str(candidate.get("candidate_id")): candidate
        for candidate in payload.get("candidates", [])
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    queued_at=datetime.utcnow().isoformat() + "Z"

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id=str(candidate.get("candidate_id")
                         or _record_identity(candidate))
        queued=dict(existing_by_id.get(candidate_id, {}))
        queued.update(candidate)
        queued["candidate_id"]=candidate_id
        queued.setdefault("memory_id", candidate.get(
            "memory_id") or _record_identity(candidate))
        if _normalize_review_status(queued) in {"", "candidate"}:
            queued["review_status"]=_PENDING_REVIEW_STATUS
        queued.setdefault("queued_at", queued_at)
        queued["updated_at"]=queued_at
        queued["provenance"]=_candidate_provenance(result, queued)
        existing_by_id[candidate_id]=queued

    payload.update(
        {
            "version": MEMORY_STORE_VERSION,
            "workflow_id": workflow_id,
            "workflow_kind": result.get("workflow_kind"),
            "memory_environment": result.get("memory_environment"),
            "namespace": list(result.get("memory_namespace", [])),
            "updated_at": queued_at,
            "candidates": list(existing_by_id.values()),
        }
    )
    _write_json_file(path, payload)
    return {
        "queued_candidate_count": len(candidates),
        "candidate_queue_path": _relative_memory_path(path),
    }


def _review_context_mapping(review_context: dict[str, Any], key: str) -> dict[str, list[str]]:
    raw=review_context.get(key)
    if not isinstance(raw, dict):
        return {}
    mapping: dict[str, list[str]]={}
    for candidate_id, values in raw.items():
        if isinstance(values, list):
            mapping[str(candidate_id)]=[str(value)
                        for value in values if value]
    return mapping


def promote_reviewed_candidates(result: dict[str, Any], review_context: dict[str, Any] | None=None) -> dict[str, Any]:
    review_context=review_context or {}
    workflow_id=str(result.get("workflow_id") or "").strip()
    approved_ids={str(value) for value in review_context.get(
        "approved_candidate_ids", []) if value}
    rejected_ids={str(value) for value in review_context.get(
        "rejected_candidate_ids", []) if value}
    if not workflow_id:
        return {
            "promoted_count": 0,
            "rejected_count": 0,
            "reviewed_store_paths": [],
        }

    path=_pending_workflow_path(workflow_id)
    payload=_read_json_file(
        path,
        {"version": MEMORY_STORE_VERSION,
            "workflow_id": workflow_id, "candidates": []},
    )
    candidates=[candidate for candidate in payload.get(
        "candidates", []) if isinstance(candidate, dict)]
    supersedes_map=_review_context_mapping(review_context, "supersedes")
    reviewer=str(review_context.get("reviewed_by") or "explicit-review")
    reviewed_store_paths: list[str]=[]
    promoted_count=0
    rejected_count=0
    reviewed_at=datetime.utcnow().isoformat() + "Z"

    for candidate in candidates:
        candidate_id=str(candidate.get("candidate_id") or "")
        if candidate_id in approved_ids:
            namespace=tuple(candidate.get("namespace")
                            or result.get("memory_namespace") or ())
            if len(namespace) != 3:
                namespace=get_memory_namespace(
                    "procedural", environment=result.get("memory_environment"))
            records=load_reviewed_memory_records(namespace)
            memory_id=str(candidate.get("memory_id")
                          or _record_identity(candidate))
            promoted=dict(candidate)
            promoted.update(
                {
                    "memory_id": memory_id,
                    "review_status": "reviewed",
                    "reviewed_at": reviewed_at,
                    "reviewed_by": reviewer,
                    "updated_at": reviewed_at,
                    "supersedes": supersedes_map.get(candidate_id, list(candidate.get("supersedes", []))),
                    "provenance": candidate.get("provenance") or _candidate_provenance(result, candidate),
                }
            )

            replaced=False
            for index, record in enumerate(records):
                if str(record.get("memory_id") or "") == memory_id:
                    records[index]=_merge_reviewed_record(record, promoted)
                    replaced=True
                    break
            if not replaced:
                records.append(promoted)

            superseded_ids=set(promoted.get("supersedes") or [])
            if superseded_ids:
                for record in records:
                    if str(record.get("memory_id") or "") in superseded_ids:
                        record["review_status"]=_SUPERSEDED_STATUS
                        record["superseded_by"]=memory_id
                        record["updated_at"]=reviewed_at

            reviewed_store_paths.append(
                _store_reviewed_memory_records(namespace, records))
            candidate["review_status"]="reviewed"
            candidate["reviewed_at"]=reviewed_at
            candidate["reviewed_by"]=reviewer
            candidate["memory_id"]=memory_id
            promoted_count += 1
        elif candidate_id in rejected_ids:
            candidate["review_status"]=_REJECTED_STATUS
            candidate["rejected_at"]=reviewed_at
            candidate["reviewed_by"]=reviewer
            rejected_count += 1

    if candidates:
        payload["candidates"]=candidates
        payload["updated_at"]=reviewed_at
        _write_json_file(path, payload)

    return {
        "promoted_count": promoted_count,
        "rejected_count": rejected_count,
        "reviewed_store_paths": _dedupe_strings(reviewed_store_paths),
    }


def persist_phase0_memory_artifacts(result: dict[str, Any], review_context: dict[str, Any] | None=None) -> dict[str, Any]:
    if not result.get("memory_enabled"):
        return {
            "backend": "disabled",
            "queued_candidate_count": 0,
            "promoted_count": 0,
            "rejected_count": 0,
            "reviewed_store_paths": [],
            "candidate_queue_path": None,
        }
    if str(result.get("selected_engine") or "").strip().lower() != "langgraph":
        return {
            "backend": "file",
            "queued_candidate_count": 0,
            "promoted_count": 0,
            "rejected_count": 0,
            "reviewed_store_paths": [],
            "candidate_queue_path": None,
            "note": "Durable memory writes remain LangGraph-owned in this tranche.",
        }

    queue_activity=queue_memory_candidates(result)
    promotion_activity=promote_reviewed_candidates(
        result, review_context=review_context)
    return {
        "backend": get_memory_backend(result),
        **queue_activity,
        **promotion_activity,
    }


def get_pending_memory_candidates(workflow_id: str, context: dict[str, Any] | None=None) -> list[dict[str, Any]]:
    path=_pending_workflow_path(str(workflow_id))
    payload=_read_json_file(path, {"candidates": []})
    return [candidate for candidate in payload.get("candidates", []) if isinstance(candidate, dict)]


def review_memory_candidates(workflow_id: str, review_context: dict[str, Any] | None, context: dict[str, Any] | None=None) -> dict[str, Any]:
    result: dict[str, Any]={"workflow_id": str(workflow_id)}
    return promote_reviewed_candidates(result, review_context)


def get_memory_store_snapshot(limit: int=12, context: dict[str, Any] | None=None) -> dict[str, Any]:
    reviewed_records: list[dict[str, Any]]=[]
    pending_candidates: list[dict[str, Any]]=[]
    reviewed_root=_reviewed_root()
    pending_root=_pending_root()

    for path in reviewed_root.glob("*.json") if reviewed_root.exists() else []:
        payload=_read_json_file(path, {"records": []})
        for record in payload.get("records", []):
            if isinstance(record, dict):
                enriched=dict(record)
                enriched.setdefault("namespace", payload.get("namespace", []))
                enriched.setdefault("store_path", _relative_memory_path(path))
                reviewed_records.append(enriched)

    for path in pending_root.glob("*.json") if pending_root.exists() else []:
        payload=_read_json_file(path, {"candidates": []})
        for candidate in payload.get("candidates", []):
            if isinstance(candidate, dict):
                enriched=dict(candidate)
                enriched.setdefault("namespace", payload.get("namespace", []))
                enriched.setdefault("store_path", _relative_memory_path(path))
                enriched.setdefault("workflow_id", payload.get("workflow_id"))
                pending_candidates.append(enriched)

    reviewed_records.sort(
        key=lambda record: (
            1 if _normalize_review_status(record) in _REVIEWED_STATUSES else 0,
            _normalize_created_at({
                "created_at": record.get("reviewed_at") or record.get("updated_at") or record.get("created_at")
            }) or datetime.min,
        ),
        reverse=True,
    )
    pending_candidates.sort(
        key=lambda record: _normalize_created_at({
            "created_at": record.get("updated_at") or record.get("queued_at") or record.get("created_at")
        }) or datetime.min,
        reverse=True,
    )

    return {
        "backend": get_memory_backend({}),
        "storage_root": _relative_memory_path(_memory_root()),
        "reviewed_total": len([record for record in reviewed_records if _normalize_review_status(record) in _REVIEWED_STATUSES]),
        "pending_review_total": len([record for record in pending_candidates if _normalize_review_status(record) == _PENDING_REVIEW_STATUS]),
        "superseded_total": len([record for record in reviewed_records if _normalize_review_status(record) == _SUPERSEDED_STATUS]),
        "recent_reviewed": reviewed_records[:limit],
        "recent_candidates": pending_candidates[:limit],
    }


def retrieve_reviewed_memories(
    records: list[dict[str, Any]] | None,
    *,
    workflow_kind: str,
    namespace: tuple[str, str, str],
    limit: int=PHASE0_MEMORY_LIMIT,
) -> list[dict[str, Any]]:
    if not records:
        return []

    filtered: list[dict[str, Any]]=[]
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("memory_type") or "").strip().lower() != namespace[2]:
            continue

        record_namespace=record.get("namespace")
        if record_namespace is not None and tuple(record_namespace) != namespace:
            continue

        if _normalize_review_status(record) not in _REVIEWED_STATUSES:
            continue

        record_workflow_kind=str(record.get(
            "workflow_kind") or "").strip().lower()
        if record_workflow_kind and record_workflow_kind != workflow_kind:
            continue

        filtered.append(record)

    filtered.sort(
        key=lambda record: (
            _normalize_confidence(record),
            _normalize_created_at(record),
        ),
        reverse=True,
    )
    return filtered[:max(0, limit)]


def _memory_note(record: dict[str, Any]) -> str:
    summary=str(record.get("summary") or "").strip()
    evidence=record.get("evidence") if isinstance(
        record.get("evidence"), dict) else {}
    command_count=len(evidence.get("validation_commands") or [])
    file_count=len(evidence.get("file_paths") or [])
    return f"Reviewed procedural memory: {summary} (files: {file_count}, commands: {command_count})"


def prepare_phase0_memory_context(task: str, context: dict[str, Any] | None=None) -> dict[str, Any]:
    resolved_context=context or {}
    enabled=is_phase0_memory_enabled("langgraph", resolved_context)
    workflow_kind=infer_workflow_kind(
        task,
        touched_files=list(resolved_context.get("touched_files", [])),
        verification_commands=list(
            resolved_context.get("verification_commands", [])),
    )
    environment=get_memory_environment()
    namespace=get_memory_namespace("procedural", environment=environment)
    limit=int(resolved_context.get("memory_limit",
              PHASE0_MEMORY_LIMIT) or PHASE0_MEMORY_LIMIT)

    if not enabled:
        return {
            "memory_enabled": False,
            "memory_environment": environment,
            "memory_namespace": namespace,
            "workflow_kind": workflow_kind,
            "retrieved_memories": [],
            "memory_notes": [
                "Phase 0 agentic memory is disabled; proceeding without retrieved memory context.",
            ],
        }

    stored_records=load_reviewed_memory_records(
        namespace) if get_memory_backend(resolved_context) == "file" else []
    inline_records=resolved_context.get("memory_records") if isinstance(
        resolved_context.get("memory_records"), list) else []
    retrieved_memories=retrieve_reviewed_memories(
        [*stored_records, *inline_records],
        workflow_kind=workflow_kind,
        namespace=namespace,
        limit=limit,
    )
    notes=[
        f"Phase 0 agentic memory enabled for {workflow_kind} in {environment}.",
    ]
    if retrieved_memories:
        notes.extend(_memory_note(record) for record in retrieved_memories)
    else:
        notes.append("No reviewed procedural memories matched this workflow.")
    if stored_records:
        notes.append(
            f"Loaded {len(stored_records)} reviewed memory record(s) from the durable store.")

    return {
        "memory_enabled": True,
        "memory_environment": environment,
        "memory_namespace": namespace,
        "workflow_kind": workflow_kind,
        "retrieved_memories": retrieved_memories,
        "memory_notes": notes,
    }


def _comparison_paths(result: dict[str, Any]) -> list[str]:
    paths: list[str]=[]
    for item in result.get("retrieved_guidance", []):
        if isinstance(item, dict) and item.get("path"):
            paths.append(str(item["path"]))

    workflow_kind=str(result.get("workflow_kind") or "")
    default_paths={
        "cache_behavior": [
            "/memories/repo/performance-notes.md",
            "/memories/repo/battlestats-notes.md",
        ],
        "agentic_workflow": [
            "agents/runbooks/runbook-langgraph-opinionated-workflow.md",
            "/memories/repo/battlestats-notes.md",
        ],
        "client_route_smoke": [
            "/memories/repo/performance-notes.md",
        ],
    }
    paths.extend(default_paths.get(workflow_kind, []))

    deduped: list[str]=[]
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped[:4]


def build_phase0_memory_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not result.get("memory_enabled"):
        return []
    if str(result.get("status") or "") != "completed":
        return []

    workflow_kind=str(result.get("workflow_kind") or "agentic_workflow")
    environment=str(result.get("memory_environment")
                    or get_memory_environment())
    namespace=get_memory_namespace("procedural", environment=environment)
    evidence={
        "file_paths": list(result.get("touched_files", []))[:6],
        "validation_commands": list(result.get("verification_commands", []))[:4],
        "run_log_path": result.get("run_log_path"),
        "trace_url": result.get("langsmith_trace_url"),
        "guidance_paths": [
            str(item.get("path"))
            for item in result.get("retrieved_guidance", [])
            if isinstance(item, dict) and item.get("path")
        ][:3],
    }
    candidates: list[dict[str, Any]]=[]

    if evidence["validation_commands"]:
        candidate_id=f"{result.get('workflow_id')}:candidate:1"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "memory_id": _record_identity({
                    "namespace": namespace,
                    "memory_type": "procedural",
                    "workflow_kind": workflow_kind,
                    "summary": f"Reuse the validated command set for {workflow_kind} workflows.",
                }),
                "memory_type": "procedural",
                "workflow_kind": workflow_kind,
                "namespace": namespace,
                "summary": f"Reuse the validated command set for {workflow_kind} workflows.",
                "detail": "Focused validation commands succeeded for this run and are candidates for reviewed procedural memory.",
                "review_status": "candidate",
                "confidence": 0.6,
                "source_run_id": result.get("workflow_id"),
                "engine": result.get("selected_engine") or "langgraph",
                "evidence": evidence,
                "comparison_paths": _comparison_paths(result),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
        )

    if evidence["file_paths"]:
        candidate_id=f"{result.get('workflow_id')}:candidate:2"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "memory_id": _record_identity({
                    "namespace": namespace,
                    "memory_type": "procedural",
                    "workflow_kind": workflow_kind,
                    "summary": f"Start {workflow_kind} work from the previously touched battlestats files and guidance artifacts.",
                }),
                "memory_type": "procedural",
                "workflow_kind": workflow_kind,
                "namespace": namespace,
                "summary": f"Start {workflow_kind} work from the previously touched battlestats files and guidance artifacts.",
                "detail": "Touched files and retrieved guidance identified likely source-of-truth entry points for similar future work.",
                "review_status": "candidate",
                "confidence": 0.5,
                "source_run_id": result.get("workflow_id"),
                "engine": result.get("selected_engine") or "langgraph",
                "evidence": evidence,
                "comparison_paths": _comparison_paths(result),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
        )

    return candidates[:2]


def summarize_phase0_memory_activity(result: dict[str, Any]) -> dict[str, Any]:
    retrieved=result.get("retrieved_memories") if isinstance(
        result.get("retrieved_memories"), list) else []
    candidates=result.get("memory_candidates") if isinstance(
        result.get("memory_candidates"), list) else []
    store_activity=result.get("memory_store_activity") if isinstance(
        result.get("memory_store_activity"), dict) else {}
    return {
        "enabled": bool(result.get("memory_enabled")),
        "workflow_kind": result.get("workflow_kind"),
        "environment": result.get("memory_environment"),
        "namespace": list(result.get("memory_namespace", [])),
        "retrieved_count": len(retrieved),
        "candidate_count": len(candidates),
        "used_memory": bool(retrieved),
        "store_activity": store_activity,
    }

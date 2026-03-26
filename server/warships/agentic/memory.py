from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


PHASE0_MEMORY_LIMIT = 3
REVIEWED_MEMORY_STATUSES = {"reviewed", "approved"}
_LANGGRAPH_IN_MEMORY_STORE: dict[str, Any] | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    return _project_root() / "logs" / "agentic" / "memory"


def _normalize_backend(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    if normalized in {"", "file"}:
        return "file"
    if normalized in {"langgraph_memory", "langgraph_store", "langmem"}:
        return "langgraph_memory"
    if normalized == "langgraph_postgres":
        return "langgraph_postgres"
    return "file"


def _normalize_environment(value: str | None) -> str:
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized in {"", "local", "dev", "development"}:
        return "local"
    if normalized in {"prod", "production"}:
        return "prod-agentic"
    if normalized in {"stage", "staging"}:
        return "staging"
    return normalized


def _reviewed_store_filename(namespace: tuple[str, str, str]) -> str:
    return "__".join(namespace) + ".json"


def _logical_memory_path(section: str, filename: str) -> str:
    return f"logs/agentic/memory/{section}/{filename}"


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback

    return payload if isinstance(payload, dict) else fallback


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)


def _namespace_key(namespace: tuple[str, str, str]) -> str:
    return "::".join(namespace)


def _ensure_langgraph_store() -> dict[str, Any]:
    global _LANGGRAPH_IN_MEMORY_STORE
    if _LANGGRAPH_IN_MEMORY_STORE is None:
        _LANGGRAPH_IN_MEMORY_STORE = {
            "reviewed": {},
            "pending": {},
        }
    return _LANGGRAPH_IN_MEMORY_STORE


def get_memory_environment(explicit: str | None = None) -> str:
    return _normalize_environment(explicit or os.getenv("BATTLESTATS_AGENTIC_ENV"))


def get_memory_namespace(memory_type: str, environment: str | None = None) -> tuple[str, str, str]:
    return ("battlestats", get_memory_environment(environment), str(memory_type).strip().lower() or "procedural")


def get_memory_backend(context: dict[str, Any] | None = None) -> str:
    context = context or {}
    return _normalize_backend(context.get("memory_backend") or os.getenv("BATTLESTATS_AGENTIC_MEMORY_BACKEND"))


def is_phase0_memory_enabled(engine: str, context: dict[str, Any] | None = None) -> bool:
    if str(engine).strip().lower() != "langgraph":
        return False

    context = context or {}
    if isinstance(context.get("memory_enabled"), bool):
        return bool(context["memory_enabled"])

    return _is_truthy(os.getenv("BATTLESTATS_LANGMEM_ENABLED"))


def infer_workflow_kind(task: str, touched_files: list[str] | None = None, verification_commands: list[str] | None = None) -> str:
    normalized_task = (task or "").lower()
    touched_files = [str(path) for path in (touched_files or [])]
    verification_commands = [str(command) for command in (verification_commands or [])]
    combined = " ".join([normalized_task, *touched_files, *verification_commands]).lower()

    if any(token in combined for token in ("client/e2e", "playwright", "browser smoke", "test:e2e", "player detail tabs")):
        return "client_route_smoke"
    if "agentic" in combined or "langgraph" in combined or "crewai" in combined:
        return "agentic_workflow"
    if any(token in combined for token in ("cache", "pending", "warm", "hydrate", "ttl", "refresh")):
        return "cache_behavior"
    return "general_workflow"


def retrieve_reviewed_memories(
    records: list[dict[str, Any]],
    workflow_kind: str,
    namespace: tuple[str, str, str],
    limit: int = PHASE0_MEMORY_LIMIT,
) -> list[dict[str, Any]]:
    filtered = []
    for record in records:
        if not isinstance(record, dict):
            continue

        record_namespace = tuple(record.get("namespace") or ())
        if record_namespace != namespace:
            continue
        if record.get("workflow_kind") != workflow_kind:
            continue
        if str(record.get("review_status") or "").lower() not in REVIEWED_MEMORY_STATUSES:
            continue
        filtered.append(record)

    filtered.sort(
        key=lambda record: _parse_timestamp(
            str(record.get("reviewed_at") or record.get("created_at") or "")
        ),
        reverse=True,
    )
    return filtered[:max(limit, 0)]


def _list_file_reviewed_records() -> list[dict[str, Any]]:
    root = _memory_root() / "reviewed"
    if not root.exists():
        return []

    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path, {"records": []})
        namespace = tuple(payload.get("namespace") or ())
        for record in payload.get("records", []):
            if isinstance(record, dict):
                enriched = dict(record)
                if namespace and not enriched.get("namespace"):
                    enriched["namespace"] = namespace
                records.append(enriched)
    return records


def _list_file_pending_candidates() -> list[dict[str, Any]]:
    root = _memory_root() / "pending"
    if not root.exists():
        return []

    candidates: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path, {"candidates": []})
        workflow_id = str(payload.get("workflow_id") or path.stem)
        namespace = tuple(payload.get("namespace") or ())
        for candidate in payload.get("candidates", []):
            if isinstance(candidate, dict):
                enriched = dict(candidate)
                enriched.setdefault("workflow_id", workflow_id)
                if namespace and not enriched.get("namespace"):
                    enriched["namespace"] = namespace
                candidates.append(enriched)
    return candidates


def _list_langgraph_reviewed_records() -> list[dict[str, Any]]:
    store = _ensure_langgraph_store()
    records: list[dict[str, Any]] = []
    for payload in store["reviewed"].values():
        namespace = tuple(payload.get("namespace") or ())
        for record in payload.get("records", []):
            enriched = dict(record)
            if namespace and not enriched.get("namespace"):
                enriched["namespace"] = namespace
            records.append(enriched)
    return records


def _list_langgraph_pending_candidates() -> list[dict[str, Any]]:
    store = _ensure_langgraph_store()
    candidates: list[dict[str, Any]] = []
    for workflow_id, payload in store["pending"].items():
        namespace = tuple(payload.get("namespace") or ())
        for candidate in payload.get("candidates", []):
            enriched = dict(candidate)
            enriched.setdefault("workflow_id", workflow_id)
            if namespace and not enriched.get("namespace"):
                enriched["namespace"] = namespace
            candidates.append(enriched)
    return candidates


def _list_reviewed_records(backend: str) -> list[dict[str, Any]]:
    if backend == "langgraph_memory":
        return _list_langgraph_reviewed_records()
    return _list_file_reviewed_records()


def _list_pending_candidates(backend: str) -> list[dict[str, Any]]:
    if backend == "langgraph_memory":
        return _list_langgraph_pending_candidates()
    return _list_file_pending_candidates()


def prepare_phase0_memory_context(task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    backend = get_memory_backend(context)
    environment = get_memory_environment(context.get("memory_environment"))
    workflow_kind = infer_workflow_kind(
        task,
        touched_files=context.get("touched_files") if isinstance(context.get("touched_files"), list) else [],
        verification_commands=context.get("verification_commands") if isinstance(context.get("verification_commands"), list) else [],
    )
    namespace = get_memory_namespace("procedural", environment=environment)
    enabled = bool(context.get("memory_enabled")) or is_phase0_memory_enabled("langgraph", context)
    notes: list[str] = []

    if not enabled:
        notes.append("Phase-0 memory is disabled for this run.")
        return {
            "memory_enabled": False,
            "memory_backend": backend,
            "memory_environment": environment,
            "memory_namespace": namespace,
            "workflow_kind": workflow_kind,
            "retrieved_memories": [],
            "memory_notes": notes,
        }

    records = context.get("memory_records") if isinstance(context.get("memory_records"), list) else _list_reviewed_records(backend)
    limit = int(context.get("memory_limit") or PHASE0_MEMORY_LIMIT)
    retrieved_memories = retrieve_reviewed_memories(records, workflow_kind=workflow_kind, namespace=namespace, limit=limit)

    if retrieved_memories:
        notes.append(
            f"Reviewed procedural memory retrieved for {workflow_kind}: {len(retrieved_memories)} item(s)."
        )
    else:
        notes.append(f"No reviewed procedural memory found for {workflow_kind}.")

    return {
        "memory_enabled": True,
        "memory_backend": backend,
        "memory_environment": environment,
        "memory_namespace": namespace,
        "workflow_kind": workflow_kind,
        "retrieved_memories": retrieved_memories,
        "memory_notes": notes,
    }


def build_phase0_memory_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    if not bool(state.get("memory_enabled")):
        return []
    if str(state.get("status") or "") != "completed":
        return []

    workflow_id = str(state.get("workflow_id") or "").strip()
    workflow_kind = str(state.get("workflow_kind") or "").strip()
    if not workflow_id or not workflow_kind:
        return []

    namespace = tuple(state.get("memory_namespace") or get_memory_namespace("procedural", environment=state.get("memory_environment")))
    touched_files = [str(path) for path in state.get("touched_files", []) if path]
    verification_commands = [str(command) for command in state.get("verification_commands", []) if command]
    comparison_paths = [
        str(item.get("path"))
        for item in state.get("retrieved_guidance", [])
        if isinstance(item, dict) and item.get("path")
    ]
    created_at = _utc_now_iso()
    engine = str(state.get("selected_engine") or "langgraph")

    return [
        {
            "candidate_id": f"{workflow_id}:candidate:1",
            "memory_id": f"{workflow_id}:memory:1",
            "memory_type": "procedural",
            "workflow_kind": workflow_kind,
            "namespace": namespace,
            "summary": f"Reuse the validated command set for {workflow_kind} workflows.",
            "detail": "Focused validation commands succeeded for the touched workflow surface.",
            "review_status": "candidate",
            "confidence": 0.6,
            "source_run_id": workflow_id,
            "engine": engine,
            "evidence": {
                "validation_commands": verification_commands,
                "file_paths": touched_files,
            },
            "comparison_paths": comparison_paths,
            "created_at": created_at,
        },
        {
            "candidate_id": f"{workflow_id}:candidate:2",
            "memory_id": f"{workflow_id}:memory:2",
            "memory_type": "procedural",
            "workflow_kind": workflow_kind,
            "namespace": namespace,
            "summary": f"Keep verification scoped before broad reruns for {workflow_kind} changes.",
            "detail": "Prefer focused validation on touched files before widening the test surface.",
            "review_status": "candidate",
            "confidence": 0.55,
            "source_run_id": workflow_id,
            "engine": engine,
            "evidence": {
                "validation_commands": verification_commands,
                "file_paths": touched_files,
            },
            "comparison_paths": comparison_paths,
            "created_at": created_at,
        },
    ]


def _load_pending_payload(workflow_id: str, backend: str, namespace: tuple[str, str, str]) -> dict[str, Any]:
    if backend == "langgraph_memory":
        store = _ensure_langgraph_store()
        existing = store["pending"].get(workflow_id)
        if existing:
            return {
                "workflow_id": workflow_id,
                "namespace": tuple(existing.get("namespace") or namespace),
                "candidates": [dict(candidate) for candidate in existing.get("candidates", [])],
            }
        return {"workflow_id": workflow_id, "namespace": namespace, "candidates": []}

    path = _memory_root() / "pending" / f"{workflow_id}.json"
    payload = _read_json(path, {"workflow_id": workflow_id, "namespace": list(namespace), "candidates": []})
    return {
        "workflow_id": workflow_id,
        "namespace": tuple(payload.get("namespace") or namespace),
        "candidates": [dict(candidate) for candidate in payload.get("candidates", []) if isinstance(candidate, dict)],
    }


def _save_pending_payload(workflow_id: str, payload: dict[str, Any], backend: str) -> str | None:
    filename = f"{workflow_id}.json"
    logical_path = _logical_memory_path("pending", filename)
    candidates = payload.get("candidates", [])

    if backend == "langgraph_memory":
        store = _ensure_langgraph_store()
        if candidates:
            store["pending"][workflow_id] = {
                "workflow_id": workflow_id,
                "namespace": tuple(payload.get("namespace") or ()),
                "candidates": [dict(candidate) for candidate in candidates],
            }
            return logical_path

        store["pending"].pop(workflow_id, None)
        return None

    path = _memory_root() / "pending" / filename
    if candidates:
        _write_json(path, {
            "version": 1,
            "workflow_id": workflow_id,
            "namespace": list(payload.get("namespace") or ()),
            "candidates": candidates,
        })
        return logical_path

    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return None


def _load_reviewed_store(namespace: tuple[str, str, str], backend: str) -> dict[str, Any]:
    if backend == "langgraph_memory":
        store = _ensure_langgraph_store()
        payload = store["reviewed"].get(_namespace_key(namespace))
        if payload:
            return {
                "namespace": tuple(payload.get("namespace") or namespace),
                "records": [dict(record) for record in payload.get("records", [])],
            }
        return {"namespace": namespace, "records": []}

    path = _memory_root() / "reviewed" / _reviewed_store_filename(namespace)
    payload = _read_json(path, {"namespace": list(namespace), "records": []})
    return {
        "namespace": tuple(payload.get("namespace") or namespace),
        "records": [dict(record) for record in payload.get("records", []) if isinstance(record, dict)],
    }


def _save_reviewed_store(namespace: tuple[str, str, str], payload: dict[str, Any], backend: str) -> str:
    filename = _reviewed_store_filename(namespace)
    logical_path = _logical_memory_path("reviewed", filename)

    if backend == "langgraph_memory":
        store = _ensure_langgraph_store()
        store["reviewed"][_namespace_key(namespace)] = {
            "namespace": namespace,
            "records": [dict(record) for record in payload.get("records", [])],
        }
        return logical_path

    path = _memory_root() / "reviewed" / filename
    _write_json(path, {
        "version": 1,
        "namespace": list(namespace),
        "records": payload.get("records", []),
    })
    return logical_path


def get_pending_memory_candidates(workflow_id: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    context = context or {}
    backend = get_memory_backend(context)
    environment = get_memory_environment(context.get("memory_environment"))
    namespace = get_memory_namespace("procedural", environment=environment)
    payload = _load_pending_payload(str(workflow_id), backend, namespace)
    return payload.get("candidates", [])


def review_memory_candidates(workflow_id: str, review_context: dict[str, Any] | None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    review_context = review_context or {}
    backend = get_memory_backend(context)
    environment = get_memory_environment(context.get("memory_environment"))
    namespace = get_memory_namespace("procedural", environment=environment)
    pending_payload = _load_pending_payload(str(workflow_id), backend, namespace)
    pending_candidates = pending_payload.get("candidates", [])
    approved_ids = {str(value) for value in review_context.get("approved_candidate_ids", []) if value}
    rejected_ids = {str(value) for value in review_context.get("rejected_candidate_ids", []) if value}
    reviewed_by = str(review_context.get("reviewed_by") or "reviewer")
    supersedes_map = review_context.get("supersedes") if isinstance(review_context.get("supersedes"), dict) else {}

    reviewed_store = _load_reviewed_store(tuple(pending_payload.get("namespace") or namespace), backend)
    reviewed_records = reviewed_store.get("records", [])
    promoted_count = 0
    rejected_count = 0
    remaining_candidates: list[dict[str, Any]] = []

    for candidate in pending_candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id in approved_ids:
            supersedes = [str(value) for value in supersedes_map.get(candidate_id, candidate.get("supersedes", [])) if value]
            for record in reviewed_records:
                if record.get("memory_id") in supersedes and str(record.get("review_status") or "").lower() in REVIEWED_MEMORY_STATUSES:
                    record["review_status"] = "superseded"
                    record["superseded_by"] = candidate.get("memory_id")

            reviewed_record = {
                **candidate,
                "namespace": tuple(candidate.get("namespace") or pending_payload.get("namespace") or namespace),
                "review_status": "reviewed",
                "reviewed_at": _utc_now_iso(),
                "reviewed_by": reviewed_by,
                "provenance": {
                    "source_run_id": candidate.get("source_run_id") or workflow_id,
                    "engine": candidate.get("engine") or "langgraph",
                },
                "supersedes": supersedes,
            }
            reviewed_records = [record for record in reviewed_records if record.get("memory_id") != reviewed_record.get("memory_id")]
            reviewed_records.append(reviewed_record)
            promoted_count += 1
            continue

        if candidate_id in rejected_ids:
            rejected_count += 1
            continue

        remaining_candidates.append(candidate)

    reviewed_store["records"] = reviewed_records
    reviewed_store_path = _save_reviewed_store(tuple(reviewed_store.get("namespace") or namespace), reviewed_store, backend)
    pending_payload["candidates"] = remaining_candidates
    candidate_queue_path = _save_pending_payload(str(workflow_id), pending_payload, backend)

    return {
        "backend": backend,
        "queued_candidate_count": len(remaining_candidates),
        "promoted_count": promoted_count,
        "rejected_count": rejected_count,
        "candidate_queue_path": candidate_queue_path,
        "reviewed_store_paths": [reviewed_store_path] if promoted_count else [],
    }


def persist_phase0_memory_artifacts(
    result: dict[str, Any],
    review_context: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    backend = get_memory_backend({**context, **result})
    environment = get_memory_environment(result.get("memory_environment") or context.get("memory_environment"))

    if not bool(result.get("memory_enabled")):
        return {
            "backend": "disabled",
            "queued_candidate_count": 0,
            "promoted_count": 0,
            "rejected_count": 0,
            "reviewed_store_paths": [],
            "candidate_queue_path": None,
        }

    workflow_id = str(result.get("workflow_id") or "").strip()
    namespace = tuple(result.get("memory_namespace") or get_memory_namespace("procedural", environment=environment))
    pending_payload = _load_pending_payload(workflow_id, backend, namespace)
    existing_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in pending_payload.get("candidates", [])
        if candidate.get("candidate_id")
    }
    queued_count = 0

    for candidate in result.get("memory_candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            continue
        if candidate_id not in existing_by_id:
            queued_count += 1
        existing_by_id[candidate_id] = {
            **candidate,
            "namespace": tuple(candidate.get("namespace") or namespace),
        }

    pending_payload["namespace"] = namespace
    pending_payload["candidates"] = list(existing_by_id.values())
    candidate_queue_path = _save_pending_payload(workflow_id, pending_payload, backend)

    if review_context:
        review_result = review_memory_candidates(
            workflow_id,
            review_context,
            context={**context, "memory_backend": backend, "memory_environment": environment},
        )
        review_result["queued_candidate_count"] = queued_count
        if candidate_queue_path and review_result.get("candidate_queue_path") is None:
            review_result["candidate_queue_path"] = candidate_queue_path
        return review_result

    return {
        "backend": backend,
        "queued_candidate_count": queued_count,
        "promoted_count": 0,
        "rejected_count": 0,
        "reviewed_store_paths": [],
        "candidate_queue_path": candidate_queue_path,
    }


def get_memory_store_snapshot(limit: int = 10, context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    backend = get_memory_backend(context)
    reviewed_records = _list_reviewed_records(backend)
    pending_candidates = _list_pending_candidates(backend)

    reviewed_records.sort(
        key=lambda record: _parse_timestamp(str(record.get("reviewed_at") or record.get("created_at") or "")),
        reverse=True,
    )
    pending_candidates.sort(
        key=lambda candidate: _parse_timestamp(str(candidate.get("created_at") or "")),
        reverse=True,
    )

    return {
        "backend": backend,
        "reviewed_total": sum(1 for record in reviewed_records if str(record.get("review_status") or "").lower() in REVIEWED_MEMORY_STATUSES),
        "pending_review_total": len(pending_candidates),
        "superseded_total": sum(1 for record in reviewed_records if str(record.get("review_status") or "").lower() == "superseded"),
        "recent_reviewed": [
            record for record in reviewed_records
            if str(record.get("review_status") or "").lower() in REVIEWED_MEMORY_STATUSES
        ][:max(limit, 0)],
        "recent_candidates": pending_candidates[:max(limit, 0)],
    }

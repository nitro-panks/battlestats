"""Optional SuperLocalMemory integration for the agentic guidance retrieval seam.

This module is the canonical pattern for optional agentic memory layers in this
codebase. It is deliberately defensive: every public function returns ``None``
or a no-op result if ``superlocalmemory`` is not installed, the layer is
disabled by env var, or the underlying client raises.

Layer position
--------------

SuperLocalMemory operates *only* on the ``_retrieve_guidance`` graph node
(``server/warships/agentic/graph.py``). It re-ranks the deterministic output of
``retrieve_doctrine_guidance`` (``server/warships/agentic/retrieval.py``) by
issuing a semantic ``recall`` against the ``agents/`` markdown corpus, which is
indexed lazily into a local SQLite database (Mode A — math-only, zero LLM,
droplet-safe).

Public surface
--------------

- ``is_slm_enabled(context) -> bool``
- ``get_slm_config_summary(context) -> dict``
- ``get_slm_client(context) -> Any | None``
- ``ensure_corpus_indexed(client) -> dict``
- ``rerank_guidance(client, task, candidates, limit) -> list[dict]``
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency lane
    import superlocalmemory as _slm  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency lane
    _slm = None  # type: ignore[assignment]


from .retrieval import GUIDANCE_GLOBS

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
DEFAULT_SLM_MODE = "A"
DEFAULT_SLM_DB_PATH = "server/logs/agentic/slm/corpus.db"
DEFAULT_RERANK_LIMIT = 5


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


def is_slm_enabled(context: dict[str, Any] | None = None) -> bool:
    resolved_context = context or {}
    if "slm_enabled" in resolved_context:
        return bool(resolved_context.get("slm_enabled"))
    return _env_flag("BATTLESTATS_SLM_ENABLED", default=False)


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    for candidate in current.parents:
        if (candidate / "manage.py").exists():
            return candidate
    return current.parents[3]


def _resolve_db_path(context: dict[str, Any] | None) -> Path:
    raw = _resolve_text(context, "slm_db_path", "BATTLESTATS_SLM_DB_PATH") or DEFAULT_SLM_DB_PATH
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate


def _resolve_mode(context: dict[str, Any] | None) -> str:
    raw = _resolve_text(context, "slm_mode", "BATTLESTATS_SLM_MODE") or DEFAULT_SLM_MODE
    return raw.strip().upper() or DEFAULT_SLM_MODE


def _resolve_reindex_on_boot(context: dict[str, Any] | None) -> bool:
    resolved_context = context or {}
    if "slm_reindex_on_boot" in resolved_context:
        return bool(resolved_context.get("slm_reindex_on_boot"))
    return _env_flag("BATTLESTATS_SLM_REINDEX_ON_BOOT", default=False)


def get_slm_config_summary(context: dict[str, Any] | None = None) -> dict[str, Any]:
    db_path = _resolve_db_path(context)
    mode = _resolve_mode(context)
    enabled = is_slm_enabled(context)
    available = _slm is not None

    return {
        "enabled": enabled,
        "dependency_available": available,
        "configured": enabled and available,
        "mode": mode,
        "db_path": str(db_path),
        "reindex_on_boot": _resolve_reindex_on_boot(context),
        "guidance_globs": list(GUIDANCE_GLOBS),
    }


def get_slm_client(context: dict[str, Any] | None = None) -> Any | None:
    """Return a configured SuperLocalMemory client, or ``None`` when disabled.

    The client interface is whatever ``superlocalmemory`` exposes at import
    time. We try a small set of likely entry points (a ``Client`` class, a
    ``Memory`` class, or a top-level ``connect`` function), so the wrapper does
    not break if the upstream library renames its public surface.
    """

    summary = get_slm_config_summary(context)
    if not summary["configured"]:
        return None

    db_path = Path(summary["db_path"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    mode = summary["mode"]

    factory = _select_client_factory()
    if factory is None:
        logger.warning(
            "superlocalmemory installed but no recognized client factory; "
            "tried Client, Memory, connect."
        )
        return None

    try:
        return factory(db_path=str(db_path), mode=mode)
    except TypeError:
        # Older / different signatures — fall back to a path-only call.
        try:
            return factory(str(db_path))
        except Exception:  # pragma: no cover - defensive
            logger.exception("failed to instantiate superlocalmemory client")
            return None
    except Exception:  # pragma: no cover - defensive
        logger.exception("failed to instantiate superlocalmemory client")
        return None


def _select_client_factory():
    if _slm is None:
        return None
    for attr in ("Client", "Memory", "SuperLocalMemory", "connect"):
        candidate = getattr(_slm, attr, None)
        if callable(candidate):
            return candidate
    return None


def _index_marker_path(client: Any) -> Path:
    db_path = getattr(client, "db_path", None) or DEFAULT_SLM_DB_PATH
    marker = Path(str(db_path)).with_suffix(".indexed")
    marker.parent.mkdir(parents=True, exist_ok=True)
    return marker


def _read_marker(marker: Path) -> dict[str, float]:
    if not marker.exists():
        return {}
    seen: dict[str, float] = {}
    try:
        for line in marker.read_text(encoding="utf-8").splitlines():
            if "\t" not in line:
                continue
            mtime_str, path_str = line.split("\t", 1)
            try:
                seen[path_str] = float(mtime_str)
            except ValueError:
                continue
    except OSError:
        return {}
    return seen


def _write_marker(marker: Path, entries: dict[str, float]) -> None:
    try:
        marker.write_text(
            "\n".join(f"{mtime}\t{path}" for path, mtime in sorted(entries.items())),
            encoding="utf-8",
        )
    except OSError:  # pragma: no cover - defensive
        logger.exception("failed to write SLM index marker at %s", marker)


def _remember(client: Any, content: str, metadata: dict[str, Any]) -> bool:
    """Best-effort call into the SLM client's remember/add API."""

    for attr in ("remember", "add", "store", "ingest"):
        method = getattr(client, attr, None)
        if not callable(method):
            continue
        try:
            method(content, metadata=metadata)
            return True
        except TypeError:
            try:
                method(content, metadata)
                return True
            except Exception:  # pragma: no cover - defensive
                continue
        except Exception:  # pragma: no cover - defensive
            logger.exception("SLM remember call failed via %s", attr)
            return False
    return False


def _recall(client: Any, query: str, limit: int) -> list[dict[str, Any]]:
    """Best-effort call into the SLM client's recall/search API."""

    for attr in ("recall", "search", "query"):
        method = getattr(client, attr, None)
        if not callable(method):
            continue
        try:
            result = method(query, limit=limit)
        except TypeError:
            try:
                result = method(query)
            except Exception:  # pragma: no cover - defensive
                continue
        except Exception:  # pragma: no cover - defensive
            logger.exception("SLM recall call failed via %s", attr)
            return []
        return _normalize_recall_result(result)
    return []


def _normalize_recall_result(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, dict):
        # Some SLM versions return {"results": [...]} or {"hits": [...]}.
        for key in ("results", "hits", "memories", "items"):
            if isinstance(result.get(key), list):
                result = result[key]
                break
        else:
            return []
    if not isinstance(result, list):
        return []

    normalized: list[dict[str, Any]] = []
    for entry in result:
        if isinstance(entry, dict):
            normalized.append(entry)
    return normalized


def ensure_corpus_indexed(client: Any) -> dict[str, Any]:
    """Walk ``GUIDANCE_GLOBS`` and feed any new/changed files into SLM.

    Returns counters describing what was indexed. Idempotent: subsequent calls
    only re-ingest files whose mtime is newer than the recorded marker.
    """

    if client is None:
        return {"indexed": 0, "skipped": 0, "errors": 0, "files": 0}

    marker = _index_marker_path(client)
    seen = _read_marker(marker)
    new_seen = dict(seen)

    repo_root = _repo_root()
    indexed = 0
    skipped = 0
    errors = 0
    files = 0

    for pattern in GUIDANCE_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            if not path.is_file():
                continue
            files += 1
            relative = str(path.relative_to(repo_root))
            mtime = path.stat().st_mtime
            if seen.get(relative) == mtime:
                skipped += 1
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                errors += 1
                continue
            metadata = {
                "path": relative,
                "source": "battlestats-agents-corpus",
                "mtime": mtime,
            }
            if _remember(client, content, metadata):
                indexed += 1
                new_seen[relative] = mtime
            else:
                errors += 1

    if indexed:
        _write_marker(marker, new_seen)

    return {
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "files": files,
    }


def rerank_guidance(
    client: Any,
    task: str,
    candidates: list[dict[str, Any]],
    limit: int = DEFAULT_RERANK_LIMIT,
) -> list[dict[str, Any]]:
    """Re-rank ``candidates`` using semantic recall against the indexed corpus.

    SLM hits are merged into the deterministic candidate list, preserving the
    dict shape produced by ``retrieve_doctrine_guidance``. The deterministic
    list remains the source of truth: SLM only reorders and supplements it. If
    SLM produces no usable hits, the input is returned unchanged.
    """

    if client is None or not task.strip():
        return list(candidates)

    hits = _recall(client, task, max(limit * 2, len(candidates) + limit))
    if not hits:
        return list(candidates)

    # Build a path → score map from SLM hits.
    slm_scores: dict[str, float] = {}
    for index, hit in enumerate(hits):
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        relative = (
            metadata.get("path")
            or hit.get("path")
            or hit.get("source")
            or ""
        )
        if not relative:
            continue
        # Higher rank = higher score; cap to first ``limit * 2`` hits.
        score = max(0.0, 1.0 - (index / max(len(hits), 1)))
        slm_scores[str(relative)] = score

    if not slm_scores:
        return list(candidates)

    candidate_paths = {str(item.get("path") or ""): item for item in candidates}

    # Rerank: existing candidates retain their dicts but get an additive
    # ``slm_score`` field; new SLM hits that aren't in the candidate set are
    # appended as minimal dicts so the downstream guidance_notes still work.
    reranked: list[dict[str, Any]] = []
    for path, item in candidate_paths.items():
        merged = dict(item)
        merged["slm_score"] = slm_scores.get(path, 0.0)
        reranked.append(merged)

    for path, score in slm_scores.items():
        if path in candidate_paths:
            continue
        reranked.append({
            "path": path,
            "title": Path(path).stem.replace("-", " ").strip() or path,
            "excerpt": "Surfaced by SuperLocalMemory recall.",
            "doc_type": "slm-hit",
            "workflow_kind": "",
            "slm_score": score,
        })

    reranked.sort(
        key=lambda item: (
            item.get("slm_score", 0.0),
            float(item.get("score", 0.0)) if isinstance(item.get("score"), (int, float)) else 0.0,
        ),
        reverse=True,
    )
    return reranked[:limit]

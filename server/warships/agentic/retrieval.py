from __future__ import annotations

from datetime import date
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


GUIDANCE_GLOBS = (
    "agents/knowledge/*.md",
    "agents/runbooks/*.md",
    "agents/reviews/*.md",
    "agents/contracts/**/*.yaml",
)

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "then", "have", "will", "your", "about", "after", "before", "over",
    "under", "more", "than", "they", "them", "their", "task", "plan",
    "work", "best", "does", "dont", "just", "into", "also", "need",
}

DOC_TYPE_WEIGHTS = {
    "runbook": 1.2,
    "spec": 1.1,
    "contract": 1.05,
    "knowledge": 0.9,
    "review": 0.8,
    "index": 0.6,
    "other": 0.5,
}

LIFECYCLE_WEIGHTS = {
    "evergreen": 0.35,
    "dated-active": 0.2,
    "active-spec": 0.15,
    "support-index": 0.05,
}

SECTION_WEIGHTS = {
    "operations": 0.2,
    "platform": 0.18,
    "agentic": 0.18,
    "architecture": 0.16,
    "contracts": 0.16,
    "knowledge": 0.14,
    "quality": 0.12,
    "feature-recovery": 0.08,
    "spec": 0.06,
    "index": 0.04,
}

STATUS_WEIGHTS = {
    "active": 0.2,
    "draft": 0.05,
    "archived": -0.5,
}

WORKFLOW_KIND_KEYWORDS = {
    "client_route_smoke": {"playwright", "browser", "route", "smoke", "client", "navigation"},
    "cache_behavior": {"cache", "ttl", "hydrate", "hydration", "stale", "warming", "poll", "refresh"},
    "api_contract_change": {"api", "endpoint", "payload", "serializer", "schema", "response", "contract", "route", "fetch"},
    "agentic_workflow": {"agentic", "langgraph", "crewai", "trace", "checkpoint", "doctrine", "memory", "workflow"},
    "upstream_contract_review": {"upstream", "encyclopedia", "wargaming", "contract", "review"},
    "performance_regression": {"performance", "latency", "slow", "regression", "benchmark", "memory"},
}


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "docker-compose.yml").exists():
            return candidate
    for candidate in current.parents:
        if (candidate / "manage.py").exists():
            return candidate
    return current.parents[3]


def _tokenize(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in STOPWORDS
    }
    return tokens


def _token_bigrams(tokens: set[str]) -> set[str]:
    ordered = sorted(tokens)
    return {
        f"{ordered[index]}_{ordered[index + 1]}"
        for index in range(len(ordered) - 1)
    }


def _extract_title(content: str, path: Path) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        lowered = stripped.lower()
        if lowered.startswith("title:") or lowered.startswith("id:"):
            _, _, value = stripped.partition(":")
            value = value.strip()
            if value:
                return value
    return path.stem.replace("-", " ").strip()


def _extract_excerpt(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:220]
    return "No excerpt available."


def _extract_doc_type(path: Path, metadata: dict[str, Any] | None = None) -> str:
    if metadata:
        kind = str(metadata.get("kind") or "").strip().lower()
        if kind in {"runbook", "spec", "knowledge", "review", "index"}:
            return kind
        if kind in {"contract", "data-product", "upstream-contract"}:
            return "contract"

    stem = path.stem.lower()
    parent = path.as_posix().lower()
    if path.name.lower() == "readme.md":
        return "index"
    if path.match("agents/runbooks/*.md"):
        if stem.startswith("spec-"):
            return "spec"
        return "runbook"
    if path.match("agents/reviews/*.md"):
        return "review"
    if path.match("agents/knowledge/*.md"):
        return "knowledge"
    if path.match("agents/contracts/**/*.yaml"):
        return "contract"
    if "spec" in stem or "spec" in parent:
        return "spec"
    return "other"


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    raw = metadata.get(key)
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for value in raw:
        normalized = str(value).strip()
        if normalized:
            values.append(normalized)
    return values


@lru_cache(maxsize=1)
def _doc_metadata_registry() -> dict[str, dict[str, Any]]:
    root = _repo_root()
    candidate_roots: list[Path] = []
    for candidate in (root, root.parent):
        if candidate in candidate_roots:
            continue
        if (candidate / "agents").exists():
            candidate_roots.append(candidate)

    for candidate_root in candidate_roots:
        registry_path = candidate_root / "agents" / "doc_registry.json"
        if not registry_path.exists():
            continue
        try:
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        docs = payload.get("docs", payload)
        if isinstance(docs, dict):
            return {
                str(path): metadata
                for path, metadata in docs.items()
                if isinstance(metadata, dict)
            }
    return {}


def _extract_last_updated_ordinal(content: str, path: Path) -> int | None:
    patterns = (
        r"last_updated:\s*(\d{4}-\d{2}-\d{2})",
        r"Last updated:\s*(\d{4}-\d{2}-\d{2})",
        r"_Last updated:\s*(\d{4}-\d{2}-\d{2})_",
        r"(20\d{2}-\d{2}-\d{2})",
    )
    haystacks = ["\n".join(content.splitlines()[:30]), path.name]
    for haystack in haystacks:
        for pattern in patterns:
            match = re.search(pattern, haystack, re.IGNORECASE)
            if not match:
                continue
            try:
                return date.fromisoformat(match.group(1)).toordinal()
            except ValueError:
                continue
    return None


def _infer_task_workflow_kind(task: str) -> str:
    from .memory import infer_workflow_kind

    return infer_workflow_kind(task)


def _workflow_keyword_overlap(task_kind: str, doc_tokens: set[str]) -> list[str]:
    return sorted(WORKFLOW_KIND_KEYWORDS.get(task_kind, set()) & doc_tokens)


def _recency_boost(last_updated_ordinal: int | None, documents: list[dict[str, Any]]) -> float:
    known_ordinals = [
        int(doc["last_updated_ordinal"])
        for doc in documents
        if doc.get("last_updated_ordinal")
    ]
    if not last_updated_ordinal or not known_ordinals:
        return 0.0
    earliest = min(known_ordinals)
    latest = max(known_ordinals)
    if latest == earliest:
        return 0.25
    return round(((last_updated_ordinal - earliest) / (latest - earliest)) * 0.5, 3)


@lru_cache(maxsize=1)
def _guidance_documents() -> list[dict[str, Any]]:
    root = _repo_root()
    metadata_registry = _doc_metadata_registry()
    candidate_roots: list[Path] = []
    for candidate in (root, root.parent):
        if candidate in candidate_roots:
            continue
        if (candidate / "agents").exists():
            candidate_roots.append(candidate)

    if not candidate_roots:
        candidate_roots.append(root)

    docs: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for candidate_root in candidate_roots:
        for pattern in GUIDANCE_GLOBS:
            for path in sorted(candidate_root.glob(pattern)):
                relative_path = str(path.relative_to(candidate_root))
                if relative_path in seen_paths:
                    continue
                seen_paths.add(relative_path)

                content = path.read_text(encoding="utf-8")
                metadata = metadata_registry.get(relative_path, {})
                title = _extract_title(content, path)
                excerpt = _extract_excerpt(content)
                relative_text = relative_path + "\n" + title + \
                    "\n" + excerpt + "\n" + content[:6000]
                tokens = _tokenize(relative_text)
                aliases = _metadata_list(metadata, "aliases")
                tags = _metadata_list(metadata, "tags")
                metadata_text = "\n".join(
                    [
                        str(metadata.get("owner") or ""),
                        str(metadata.get("section") or ""),
                        str(metadata.get("status") or ""),
                        str(metadata.get("lifecycle") or ""),
                    ]
                )
                docs.append({
                    "path": relative_path,
                    "title": title,
                    "excerpt": excerpt,
                    "doc_type": _extract_doc_type(path, metadata),
                    "last_updated_ordinal": _extract_last_updated_ordinal(content, path),
                    "tokens": tokens,
                    "title_tokens": _tokenize(title),
                    "path_tokens": _tokenize(relative_path),
                    "phrase_tokens": _token_bigrams(tokens),
                    "alias_tokens": _tokenize("\n".join(aliases)),
                    "tag_tokens": _tokenize("\n".join(tags)),
                    "metadata_tokens": _tokenize(metadata_text),
                    "aliases": aliases,
                    "tags": tags,
                    "owner": str(metadata.get("owner") or "").strip().lower(),
                    "section": str(metadata.get("section") or "").strip().lower(),
                    "status": str(metadata.get("status") or "").strip().lower(),
                    "lifecycle": str(metadata.get("lifecycle") or "").strip().lower(),
                })
    return docs


def retrieve_doctrine_guidance(task: str, limit: int = 3, workflow_kind: str | None = None) -> list[dict[str, Any]]:
    task_tokens = _tokenize(task)
    if not task_tokens:
        return []
    task_phrase_tokens = _token_bigrams(task_tokens)
    resolved_workflow_kind = (
        workflow_kind or _infer_task_workflow_kind(task)).strip().lower()

    def _score_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scored: list[dict[str, Any]] = []
        for doc in documents:
            overlap = sorted(task_tokens & set(doc["tokens"]))
            title_overlap = sorted(task_tokens & set(doc["title_tokens"]))
            path_overlap = sorted(task_tokens & set(doc["path_tokens"]))
            alias_overlap = sorted(task_tokens & set(
                doc.get("alias_tokens") or set()))
            tag_overlap = sorted(task_tokens & set(
                doc.get("tag_tokens") or set()))
            metadata_overlap = sorted(task_tokens & set(
                doc.get("metadata_tokens") or set()))
            phrase_overlap = sorted(
                task_phrase_tokens & set(doc["phrase_tokens"]))
            workflow_overlap = _workflow_keyword_overlap(
                resolved_workflow_kind,
                set(doc["tokens"]),
            )
            if (
                not overlap and
                not workflow_overlap and
                not phrase_overlap and
                not alias_overlap and
                not tag_overlap and
                not metadata_overlap
            ):
                continue

            token_score = float(len(overlap))
            title_score = float(len(title_overlap)) * 1.5
            path_score = float(len(path_overlap)) * 1.25
            alias_score = float(len(alias_overlap)) * 1.35
            tag_score = float(len(tag_overlap)) * 1.1
            metadata_score = float(len(metadata_overlap)) * 0.7
            phrase_score = float(len(phrase_overlap)) * 1.1
            workflow_score = float(len(workflow_overlap)) * 0.9
            doc_type_score = DOC_TYPE_WEIGHTS.get(
                str(doc.get("doc_type") or "other"), 0.5)
            lifecycle_score = LIFECYCLE_WEIGHTS.get(
                str(doc.get("lifecycle") or ""), 0.0)
            section_score = SECTION_WEIGHTS.get(
                str(doc.get("section") or ""), 0.0)
            status_score = STATUS_WEIGHTS.get(
                str(doc.get("status") or ""), 0.0)
            recency_score = _recency_boost(
                doc.get("last_updated_ordinal"), documents)
            total_score = round(
                token_score + title_score + path_score + alias_score +
                tag_score + metadata_score + phrase_score + workflow_score +
                doc_type_score + lifecycle_score + section_score +
                status_score + recency_score,
                3,
            )

            ranking_reasons: list[str] = []
            if overlap:
                ranking_reasons.append(
                    "token overlap: " + ", ".join(overlap[:5])
                )
            if title_overlap:
                ranking_reasons.append(
                    "title match: " + ", ".join(title_overlap[:4])
                )
            if alias_overlap:
                ranking_reasons.append(
                    "alias match: " + ", ".join(alias_overlap[:4])
                )
            if tag_overlap:
                ranking_reasons.append(
                    "tag match: " + ", ".join(tag_overlap[:5])
                )
            if workflow_overlap:
                ranking_reasons.append(
                    f"workflow relevance for {resolved_workflow_kind}: " +
                    ", ".join(workflow_overlap[:4])
                )
            if doc.get("doc_type"):
                ranking_reasons.append(
                    f"doc type boost: {doc['doc_type']}"
                )
            if doc.get("section"):
                ranking_reasons.append(
                    f"section: {doc['section']}"
                )
            if doc.get("lifecycle"):
                ranking_reasons.append(
                    f"lifecycle: {doc['lifecycle']}"
                )
            scored.append({
                "path": doc["path"],
                "title": doc["title"],
                "excerpt": doc["excerpt"],
                "doc_type": doc.get("doc_type"),
                "workflow_kind": resolved_workflow_kind,
                "matched_terms": sorted({
                    *overlap[:8],
                    *alias_overlap[:6],
                    *tag_overlap[:6],
                    *metadata_overlap[:4],
                })[:10],
                "ranking_reasons": ranking_reasons,
                "score_breakdown": {
                    "token_overlap": token_score,
                    "title_overlap": title_score,
                    "path_overlap": path_score,
                    "alias_overlap": alias_score,
                    "tag_overlap": tag_score,
                    "metadata_overlap": metadata_score,
                    "phrase_overlap": phrase_score,
                    "workflow_overlap": workflow_score,
                    "doc_type": doc_type_score,
                    "lifecycle": lifecycle_score,
                    "section": section_score,
                    "status": status_score,
                    "recency": recency_score,
                },
                "score": total_score,
            })
        return scored

    scored = _score_documents(_guidance_documents())
    if not scored:
        _guidance_documents.cache_clear()
        scored = _score_documents(_guidance_documents())

    scored.sort(key=lambda item: (-float(item["score"]), str(item["path"])))
    return scored[:limit]

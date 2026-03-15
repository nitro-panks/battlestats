from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any


GUIDANCE_GLOBS = (
    "agents/runbooks/*.md",
    "agents/reviews/*.md",
)

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "then", "have", "will", "your", "about", "after", "before", "over",
    "under", "more", "than", "they", "them", "their", "task", "plan",
    "work", "best", "does", "dont", "just", "into", "also", "need",
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


def _extract_title(content: str, path: Path) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem.replace("-", " ").strip()


def _extract_excerpt(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:220]
    return "No excerpt available."


@lru_cache(maxsize=1)
def _guidance_documents() -> list[dict[str, Any]]:
    root = _repo_root()
    docs: list[dict[str, Any]] = []
    for pattern in GUIDANCE_GLOBS:
        for path in sorted(root.glob(pattern)):
            content = path.read_text(encoding="utf-8")
            docs.append({
                "path": str(path.relative_to(root)),
                "title": _extract_title(content, path),
                "excerpt": _extract_excerpt(content),
                "tokens": _tokenize(path.name + "\n" + content[:6000]),
            })
    return docs


def retrieve_doctrine_guidance(task: str, limit: int = 3) -> list[dict[str, Any]]:
    task_tokens = _tokenize(task)
    if not task_tokens:
        return []

    scored: list[dict[str, Any]] = []
    for doc in _guidance_documents():
        overlap = sorted(task_tokens & set(doc["tokens"]))
        if not overlap:
            continue
        scored.append({
            "path": doc["path"],
            "title": doc["title"],
            "excerpt": doc["excerpt"],
            "matched_terms": overlap[:8],
            "score": len(overlap),
        })

    scored.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return scored[:limit]
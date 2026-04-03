# Knowledge Base

This directory is the durable markdown layer for verified findings that should survive beyond a single task or chat.

Use it when a future task would otherwise have to rediscover the same facts from scratch.

Store only:

- Upstream API investigations and current behavior notes.
- Verified system behavior that is expensive to rediscover.
- Architecture or operational constraints that affect future implementation choices.
- Research handoff notes where the next query should resume from a known state.

Do not use this directory for speculative plans, implementation to-do lists, or machine-readable schemas.

Preferred file shape:

- Title
- Last verified date
- Why this matters
- Current conclusion
- Evidence
- Reproduction steps
- Implications for this repo
- Open questions / next checks

Suggested naming:

- `wows-statsbydate-status.md`
- `player-detail-refresh-behavior.md`
- `docker-local-runtime-notes.md`

Rule of thumb: if a future query would otherwise have to rediscover the same fact pattern from scratch, write it here.

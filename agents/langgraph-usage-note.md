# LangGraph Usage Note

This file is only a quick pointer. The canonical operator guide is `agents/runbooks/runbook-langgraph-opinionated-workflow.md`.

## Use LangGraph When

- The task needs investigation before implementation.
- The task crosses frontend, backend, tests, or docs.
- You want an explicit plan before code changes.
- You want verification included, not just a patch.
- You want a summary of what changed, what passed, and what still looks risky.

## Skip LangGraph When

- The request is a small UI polish change.
- The request is a one-file edit with obvious acceptance criteria.
- You already know the exact change and only want it implemented quickly.

Use this note only as the short answer to "should I use the graph for this task?".

Default prompt pattern:

"Investigate [problem], produce a plan, implement the smallest safe change, run focused validation, and return a short summary."

See `agents/runbooks/runbook-langgraph-opinionated-workflow.md` for gates, context keys, tests, and rollback behavior.

# LangGraph Usage Note

This repo's LangGraph workflow is useful when a request needs more than a direct code edit.

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

## Good Fits In This Repo

- Bug investigations such as stale hydration or async update failures.
- Feature slices that need backend contract review plus frontend rendering.
- Runbook or process revisions that need cross-functional framing.
- Changes that should end with targeted tests and a short implementation summary.

## Prompt Pattern

Use this shape when you want the workflow to be explicit:

"Investigate [problem], produce a plan, modify code, run targeted tests or verification, and return a summary."

## Strong Example Prompts

- "Use the agent graph to investigate why clan data does not hydrate on first player-page load, produce a plan, implement the smallest safe fix, run focused tests, and summarize residual risks."
- "Inspect existing ranked battles support, produce a plan, implement the missing player-detail integration, run targeted validation, and return a summary."
- "Review the player activity runbook now that ranked is live, propose the revised framework, update the runbook, and summarize the decision changes."

## Current Repo Reality

LangGraph is not automatically triggered by normal user-facing app flows today.

## Optional LangSmith Tracing

If you want trace visibility for the agent workflows, set:

- `LANGSMITH_TRACING_V2=true`
- `LANGSMITH_API_KEY=...`
- optionally `BATTLESTATS_LANGSMITH_PROJECT=...` or `LANGSMITH_PROJECT=...`

When tracing is enabled, the workflow result payload now includes `langsmith_trace_url`.

It is currently invoked through explicit entrypoints:

- `server/scripts/run_agent_graph.py`
- `server/warships/management/commands/run_agent_graph.py`

That means you should ask for it when you want the workflow, or wire it into an app path if you want it to run as part of the product.

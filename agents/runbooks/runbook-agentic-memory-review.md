---
title: Agentic Memory Review
doc_type: runbook
status: active
last_updated: 2026-03-26
integration: battlestats agentic memory
operator_surface:
  command: python manage.py review_agent_memory
  default_backend: file
  optional_backends:
    - langgraph_memory
    - langgraph_postgres
review_scope:
  - list queued candidates
  - approve durable memories
  - reject low-value candidates
  - supersede stale reviewed memories
write_policy:
  candidate_generation: post-run langgraph only
  durable_write: explicit review required
---

# Runbook: Agentic Memory Review

## Purpose

Use this runbook to review queued agentic memory candidates and promote only reusable, evidence-backed operational facts into durable memory.

This runbook is for internal battlestats development workflows only. It is not for product or visitor memory.

## When To Use It

- After a LangGraph run that completed successfully and queued memory candidates.
- When repeated agentic work is rediscovering the same validation commands, source-of-truth files, or battlestats-specific workflow lessons.
- When stale reviewed memories need to be rejected or superseded.

## Preconditions

- The relevant LangGraph workflow has completed.
- The run produced candidate memory entries.
- You can verify the candidate against code, tests, or run logs.

## Backend Choice

- `file`: best default for local reviewable development.
- `langgraph_memory`: useful for in-process or ephemeral development loops.
- `langgraph_postgres`: use only when shared durable team memory is needed.

If you do not specify a backend, the command follows the configured default.

## Core Commands

Show a snapshot of the current memory store:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py review_agent_memory --backend file
```

Show queued candidates for a workflow:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py review_agent_memory --workflow-id <run-id> --backend file
```

Approve one or more candidates:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py review_agent_memory \
  --workflow-id <run-id> \
  --approve <candidate-id> \
  --reviewed-by <name> \
  --backend file
```

Reject one or more candidates:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py review_agent_memory \
  --workflow-id <run-id> \
  --reject <candidate-id> \
  --reviewed-by <name> \
  --backend file
```

Apply supersession when a new memory replaces an older one:

```json
{
  "run-20260326:candidate:1": ["mem-old-validation-flow"]
}
```

Save that JSON to a file, then run:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py review_agent_memory \
  --workflow-id <run-id> \
  --approve <candidate-id> \
  --reviewed-by <name> \
  --supersedes-file /absolute/path/to/supersedes.json \
  --backend file
```

Emit machine-readable output:

```bash
cd server
/home/august/code/archive/battlestats/.venv/bin/python manage.py review_agent_memory --backend file --json
```

## Review Standard

Approve a candidate only if all of the following are true:

- It captures a reusable battlestats-specific workflow fact.
- The claim is backed by code, tests, run logs, or a verified command sequence.
- You would be comfortable seeing it quoted back into a future implementation plan.
- It does not conflict with current runbooks, doctrine, or known shipped behavior.

Reject a candidate when it is:

- too specific to one transient run,
- based on incomplete verification,
- stale or contradicted by newer code,
- phrased too vaguely to be actionable later.

## Daily Loop

1. List the current store snapshot.
2. Inspect queued candidates for the latest completed LangGraph runs.
3. Approve only the candidates that encode stable operational guidance.
4. Reject noisy or one-off candidates.
5. Supersede older reviewed memories when a newer workflow replaces them.

## Observability Notes

- The dashboard payload includes memory-store totals, recent reviewed memories, recent candidates, provenance, and supersession state.
- The trace endpoint content contract includes memory-store fields, but the endpoint is intentionally down by design at present.
- LangGraph run results include `memory_store_activity` so queued and promoted writes can be inspected in run logs.

## Related Docs

- `agents/runbooks/spec-langmem-agentic-memory-pilot-2026-03-26.md`
- `agents/runbooks/runbook-langgraph-opinionated-workflow.md`
- `server/warships/management/commands/review_agent_memory.py`

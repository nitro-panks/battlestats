---
name: runbook-archive
description: Move a superseded battlestats runbook into agents/runbooks/archive/ and reconcile agents/doc_registry.json. Use when the user says "archive this runbook", "this runbook is superseded", "move to archive", or when a runbook's tranche has closed. Requires explicit user confirmation of the target before moving; stages but does not commit.
---

# runbook-archive

Moves a superseded runbook from `agents/runbooks/` to `agents/runbooks/archive/` and updates `agents/doc_registry.json` if the runbook is registered there. Archiving keeps the active runbook directory as the current source of truth — a doctrine pre-commit requirement.

## When to invoke

- "archive this runbook", "this runbook is superseded", "move to archive"
- After a tranche of work closes (rollout complete, incident fully remediated, spec implemented and reconciled)
- When a successor runbook supersedes an older one

Do **not** invoke for: evergreen operational runbooks (they have no natural archival trigger), runbooks under active investigation, or runbooks the user has not explicitly named.

## Procedure

### 1. Confirm target

Always confirm the exact filename with the user before moving — never infer from a vague phrase. If the user said "archive the cache one" and there are multiple matches in `agents/runbooks/`, list candidates and ask which.

### 2. Move the file

```bash
git mv agents/runbooks/<name>.md agents/runbooks/archive/<name>.md
```

Use `git mv` (not plain `mv`) to preserve history. Verify the file landed in `archive/` and is no longer in the parent.

### 3. Reconcile doc_registry.json

Check whether the runbook has an entry in `agents/doc_registry.json`:

```bash
grep '"agents/runbooks/<name>.md"' agents/doc_registry.json
```

- **If no entry**: nothing to update. Many runbooks are not registered (~50 of ~94 are tracked).
- **If entry exists**: update both the **key** (path) and `status`:
  - Key: `"agents/runbooks/<name>.md"` → `"agents/runbooks/archive/<name>.md"`
  - `status`: `"active"` → `"archived"`
  - Other fields (`lifecycle`, `tags`, `owner`, etc.) stay as-is.

Verify by example — existing archived entries follow this exact pattern (see `runbook-deploy-oom-startup-warmers.md` and `runbook-enrichment-crawler-2026-04-02.md` in the registry).

### 4. Optional successor link

If a successor runbook exists, ask the user whether to prepend a `> Superseded by [<successor>](../<successor>.md)` line to the top of the archived runbook. Do not add this without confirmation — sometimes archival is just "no longer relevant," not "replaced by X."

### 5. Stage

`git add` the moved runbook and the modified `doc_registry.json`. Do **not** commit — the user often wants to bundle archival with the commit that supersedes the runbook.

### 6. Report

```
Archived: agents/runbooks/<name>.md → agents/runbooks/archive/<name>.md
Registry: updated | not registered
Successor link: added | skipped | n/a

Next step: review staged changes (git diff --cached) and commit when ready.
```

## Scope and limits

- One runbook per invocation. For batch archival (e.g. "archive all the 2026-04-02 runbooks"), the user can invoke repeatedly or do it manually with a loop.
- Never deletes a runbook. Archival is move + status update only.
- Never commits. Stages only.
- Refuses to archive anything outside `agents/runbooks/` — that's not what the doctrine archival rule is about.
- Does not un-archive (move from `archive/` back to active). For that, the user does it manually.

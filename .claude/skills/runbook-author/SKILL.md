---
name: runbook-author
description: Create a new battlestats runbook in agents/runbooks/ with the project's naming convention and structural pattern. Use when the user says "write a runbook for X", "document this incident", "create a runbook", "draft a runbook", or asks to capture an investigation, incident, or rollout in durable form. Stages the file but does not commit; the user owns the commit.
---

# runbook-author

Creates a new runbook in `agents/runbooks/` following the project's established conventions. Runbooks are the durable home for incidents, rollouts, design specs, and operational procedures — they outlive the conversation that produced them and are the canonical reference for future agents.

## When to invoke

- "write a runbook for …", "document this incident", "create a runbook", "draft a runbook"
- After resolving an incident or finishing a rollout, when the user wants the work captured
- When an investigation produced findings worth keeping

Do **not** invoke for: throwaway notes (use a comment or commit message), edits to an existing runbook (just edit it directly), or to archive an old runbook (use `runbook-archive`).

## Naming convention

Two patterns observed in `agents/runbooks/`:

- **Date-stamped** (incident-, event-, or tranche-bound): `runbook-<topic-kebab>-YYYY-MM-DD.md`
  - Examples: `runbook-droplet-hardening-2026-04-09.md`, `runbook-incident-celery-zombie-worker-2026-04-12.md`
  - Use when the runbook documents a specific event or a time-bounded rollout that will eventually be archived
- **Evergreen** (operational reference): `runbook-<topic-kebab>.md`
  - Examples: `runbook-cache-audit.md`, `runbook-backend-droplet-deploy.md`, `runbook-seo.md`
  - Use when the runbook is a standing operational reference with no natural archival trigger

Use the system date (today) for the date stamp; never invent. Topic-kebab should be 2–5 words, specific enough that the filename alone tells you what's inside.

## Structural pattern

Open with the H1, then italicized inline metadata, then `## Purpose`. Sample skeleton (matches the dominant pattern in existing runbooks):

```markdown
# Runbook: <Title in Title Case>

_Created: YYYY-MM-DD_
_Context: <one-sentence framing — what triggered this runbook>_
_QA: <optional — verification evidence if applicable>_

## Purpose

<2–4 sentences. What does this runbook exist to do? Who reads it and when?>

## <Body sections — vary by runbook type>

### Findings (for incident/audit runbooks)
### Decisions (for design/spec runbooks)
### Procedure (for operational runbooks)
### Validation
### Follow-ups
```

Body sections are not fixed — choose what fits the runbook type. Spot-check 2–3 similar existing runbooks (incident, deploy, spec) before locking in the section list for a new one. The metadata block uses **italicized inline** (`_Created: …_`), not bold (`**Created:** …`).

## Procedure

### 1. Capture intent

Ask the user (or extract from conversation):
- What is the runbook about? (1–2 sentences)
- Is it incident/event-tied (date-stamped) or evergreen?
- What sections does it need? Default sections by type:
  - Incident: Purpose, Timeline, Root cause, Remediation, Validation, Follow-ups
  - Rollout/feature: Purpose, Decisions, Implementation, Validation, Follow-ups
  - Operational: Purpose, Procedure, Common issues, Related runbooks
  - Audit/findings: Purpose, Findings (one section per finding with Risk/Remediation), Validation

### 2. Compose filename

Apply the naming convention. Confirm with the user before writing if the topic kebab feels ambiguous.

### 3. Pre-populate from context

If the conversation contains relevant material (commits made, files changed, errors encountered, decisions reached), pre-fill the Findings/Timeline/Decisions sections with that material. Do not invent content — only surface what was in the conversation.

### 4. Write the file

Write to `agents/runbooks/<filename>.md` with the metadata block + section skeleton + any pre-populated content.

### 5. Offer registry update

Many runbooks are tracked in `agents/doc_registry.json` (44+ entries vs ~50 untracked). Ask the user whether to add a registry entry. If yes, the entry shape (verify against current schema):

```json
"agents/runbooks/<filename>.md": {
    "kind": "runbook",
    "status": "active",
    "lifecycle": "dated-active" | "evergreen",
    "section": "operations" | "deploy" | "incident" | "spec",
    "owner": "<team>",
    "aliases": ["<short alias>", "..."],
    "tags": ["<tag>", "..."],
    "archive_on": ["<archival trigger>"]
}
```

`lifecycle` is `dated-active` for date-stamped runbooks, `evergreen` for the standing references.

### 6. Stage

`git add` the new runbook (and `doc_registry.json` if updated). Do **not** commit; the user owns the commit and may want to combine it with related code changes.

## Scope and limits

- Writes one new file per invocation. For multi-runbook captures (e.g. an incident that spawns a hardening runbook + a postmortem), invoke twice.
- Does not edit existing runbooks. Use direct edits for that.
- Does not commit. Stages only.
- Does not delete or move existing runbooks. That is `runbook-archive`'s job.

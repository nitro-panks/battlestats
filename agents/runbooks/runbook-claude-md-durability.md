# Runbook: CLAUDE.md Durability

**Lifecycle:** evergreen · **Owner:** platform

Keeps the repo's `CLAUDE.md` a thin dispatch file instead of an always-loaded
encyclopedia. Use this when `CLAUDE.md` has drifted past its budget, accumulated
catalogs/prose, or started costing real context on every task. The ongoing
maintenance rules are enforced at commit time via the `doctrine-precommit` skill
(see `agents/knowledge/agentic-team-doctrine.json` → `claude_md_rules`); this
runbook is the deeper procedure for a full re-slim.

## Operating principle

`CLAUDE.md` is **default context**: every line is paid on a large fraction of
tasks. A line earns its place only if it's useful on most tasks, safety-critical,
defines an autonomy boundary, prevents expensive rediscovery, or encodes a
non-obvious invariant. Everything else moves to a skill, a runbook/reference doc,
or code-local docs — or gets deleted.

## When to re-slim

- `CLAUDE.md` exceeds ~1,500 words / ~200 lines.
- It contains an env-var catalog, a queue/cache/scheduling internals dump, or
  multi-paragraph architecture prose.
- Sections restate each other or restate code/scripts.
- A reader can answer the section from code, tests, or `--help` faster than from
  the doc.

## Procedure

1. Read the current `CLAUDE.md`.
2. Classify every section into one bucket:
   - **Keep** — needed on most tasks; safety-critical; autonomy boundary;
     prevents repo rediscovery; a non-obvious invariant.
   - **Skill** — a repeatable workflow with steps/checks/success criteria
     (deploy, release gate, precommit, health check, runbook authoring).
   - **Runbook/reference** — deep but on-demand: architecture, caching/warmers,
     Celery topology, scheduling, env catalogs, incident history, observability.
   - **Delete** — narrative, redundant, obvious, stale, or discoverable from
     code/scripts.
3. Rewrite `CLAUDE.md` to the target shape below.
4. Move removed content into new/updated skills (`.claude/skills/<name>/SKILL.md`)
   and runbooks (`agents/runbooks/`); register new runbooks in
   `agents/doc_registry.json`.
5. Replace explanations with pointers: `See agents/runbooks/<file>.md`.

## Target shape (~500–1,500 words)

```
# CLAUDE.md
## Repo            one-line description + main directories
## Autonomy        may-do-without-asking / ask-first
## Core commands   dev · tests · deploy · release (one representative each)
## Required reading doctrine + key skill/runbook pointers
## Invariants      3–7 bullets only
```

## Compression heuristics

- Bullets over prose; rules over rationale; file paths over architecture
  paragraphs.
- One representative command, not a catalog of variants.
- Replace any catalog with a pointer to the file that owns it.
- Keep only information that changes a decision.

Bad: cache strategy / queue topology / env flags inlined in base context.
Better: `Caching: agents/runbooks/<ops-caching>.md`.

## Maintenance rules (also enforced by doctrine-precommit)

- No environment-variable catalogs in `CLAUDE.md`.
- Deep subsystem/ops/architecture detail lives in `agents/runbooks/`, not base
  context.
- Recurring workflows become `.claude/skills/`, not inline procedures.
- `CLAUDE.md` holds repo-global defaults and pointers only.
- Prefer a file-path pointer over an embedded explanation whenever one exists.

## Enforcement (pre-commit hook)

`scripts/check_claude_md.sh` runs from `.githooks/pre-commit` and fails any commit
that stages a `CLAUDE.md` which exceeds the line cap (`CLAUDE_MD_LINE_MAX`, default
200) or carries too many env-var-catalog bullets (`CLAUDE_MD_ENV_BULLET_MAX`,
default 8). It is a no-op when `CLAUDE.md` is not staged.

- Enable in a fresh clone (hooks aren't versioned by default): `git config core.hooksPath .githooks`
- Run manually against the working tree: `scripts/check_claude_md.sh --all`
- Emergency bypass: `git commit --no-verify` (then fix forward).

## Maintenance checklist (per CLAUDE.md edit)

- Does this belong on most tasks? If not → out.
- Is it a rule, or just background knowledge? Background → runbook.
- Could it live closer to its code/workflow? → skill or code-local doc.
- Can a path pointer replace it? → use the pointer.
- Will it drift from source quickly? → keep it out.

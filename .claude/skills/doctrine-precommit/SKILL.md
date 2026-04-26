---
name: doctrine-precommit
description: Run the battlestats agentic-team-doctrine pre-commit checklist against the current diff before committing. Use when the user says "ready to commit", "before I commit", "doctrine check", "precommit check", or asks whether a change is ready to land. Checks documentation reconciliation, test coverage, runbook archiving, runbook/spec reconciliation, and contract safety against the actual changed files. Only applies inside the battlestats repo.
---

# doctrine-precommit

Enforces the pre-commit requirements from `agents/knowledge/agentic-team-doctrine.json` against the current pending diff. The doctrine is the project's authoritative source for what must be true before a commit lands; this skill walks the checklist mechanically so nothing is skipped.

## When to invoke

- User says "ready to commit", "doctrine check", "precommit check", "is this ready to land"
- After finishing a multi-step change, before running `git commit`
- Whenever the user explicitly asks whether the doctrine has been satisfied

Do **not** invoke for trivial single-file fixes the user has already approved for direct commit, or outside the battlestats repo (`/home/august/code/battlestats`).

## Procedure

### 1. Load doctrine and diff

Read these in parallel:

- `agents/knowledge/agentic-team-doctrine.json` — load `pre_commit_requirements` and `decision_rules` (these are the source of truth; if they have changed, the new wording wins over what's in this skill)
- `git status --short` and `git diff --stat HEAD` — what is staged + unstaged
- `git diff HEAD` (or per-file diffs if it's large) — actual changes

### 2. Classify the changeset

For each changed file, tag what kind of change it is. This drives which checks matter:

- **Code paths touched** — `server/warships/**`, `client/app/**`, etc.
- **Contract surfaces** — DRF views (`server/warships/views.py`, `urls.py`, serializers), API rewrites in `client/next.config.*`, anything under `agents/contracts/`
- **Docs** — `CLAUDE.md`, `agents/runbooks/**`, `agents/knowledge/**`, `agents/doc_registry.json`, top-level `*.md`
- **Tests** — `server/warships/tests/**`, `client/**/__tests__/**`
- **Runbooks/specs being implemented** — if the change traces back to a runbook (commit message, conversation context, or filename match), note which one
- **Ops/infra** — `server/deploy/**`, `client/deploy/**`, `scripts/**`, `gunicorn.conf.py`, systemd units

### 3. Walk the checklist

For each item in `pre_commit_requirements`, report **PASS / FAIL / N/A** with a one-line reason and a concrete pointer (file path) when failing.

The five current requirements (verify against the JSON; the JSON wins if it has drifted):

1. **Documentation review** — If code paths or contracts changed, are the durable docs that describe that behavior updated? Check `CLAUDE.md` sections relevant to the touched area, and any runbook in `agents/runbooks/` whose topic matches. FAIL if behavior changed but the matching doc still describes the old behavior.

2. **Doc-vs-code reconciliation** — If you noticed any doc that *might* be stale relative to the change (e.g. CLAUDE.md describes a TTL, env var, or flow that the diff alters), explicitly verify the doc against the new code. Surface uncertainties rather than burying them.

3. **Test coverage** — For every behavior-bearing change, is there a test that would have failed before the change and passes after? Check `git diff` against `server/warships/tests/` and `client/**/__tests__/`. FAIL if production code changed without a test touching the same behavior, unless the change is provably non-behavioral (pure rename, comment, dependency bump).

4. **Runbook archiving** — Does this change supersede any active runbook in `agents/runbooks/`? Check filenames and titles for matches against the topic. If a runbook is now historical, it must move to `agents/runbooks/archive/`. FAIL with the specific runbook path.

5. **Runbook/spec reconciliation** — If the change implements something described in an active runbook or `spec-*.md`, that runbook must be updated in the same commit with implementation status, fixes applied, and validation results. FAIL with the specific runbook path.

Additionally, check the contract-safety decision rule: if a payload shape, endpoint, or query parameter changed, contract docs **and** API-facing tests must change in the same tranche. Surface as a FAIL if not.

### 4. Report

Output exactly this shape:

```
Doctrine pre-commit check — <N> changed files

[1] Documentation review:        PASS|FAIL|N/A — <reason>
    └─ <pointer if FAIL>
[2] Doc-vs-code reconciliation:  PASS|FAIL|N/A — <reason>
[3] Test coverage:               PASS|FAIL|N/A — <reason>
[4] Runbook archiving:           PASS|FAIL|N/A — <reason>
[5] Runbook/spec reconciliation: PASS|FAIL|N/A — <reason>
[+] Contract safety:             PASS|FAIL|N/A — <reason>

Verdict: READY TO COMMIT | NOT READY — <one-line summary>
```

If `NOT READY`, list the concrete next actions (file to update, test to add, runbook to archive). Do not offer to perform them automatically — the user decides whether to fix-and-recheck or override.

## Scope and limits

- This skill **reads and reports**. It does not stage, commit, edit docs, or move runbook files unless the user explicitly asks for the fix afterward.
- If the doctrine JSON has changed since this skill was written, follow the JSON. Note the drift in the report so the skill can be updated.
- If the working tree is clean, say so and exit — there is nothing to check.
- Do not run the test suite as part of this check; that is the `release-gate` skill's job. This skill verifies that tests *exist* for the change, not that they pass.

# Runbook: Claude Code Skills Rollout

_Created: 2026-04-26_
_Context: Encode recurring battlestats workflows (precommit checks, release gate, runbook authoring/archiving, deploys, ops checks) as Claude Code skills under `.claude/skills/` so future sessions don't have to rediscover them._
_Status: All six planned skills shipped 2026-04-26. Pending real-world validation._

## Context

Claude Code "skills" are reusable, model-invoked workflows defined as a `SKILL.md` file with YAML frontmatter (`name`, `description`) and a Markdown body of instructions. They live in one of:

- `<repo>/.claude/skills/<name>/SKILL.md` — project-scoped, checked in
- `~/.claude/skills/<name>/SKILL.md` — user-global
- Plugin marketplaces under `~/.claude/plugins/marketplaces/`

Battlestats has heavy operational discipline (release gate, multi-mode deploys, runbook culture, recurring incident response) that benefits from skill-level encoding rather than depending on Claude rediscovering the workflow each session. The first skill (`doctrine-precommit`) is in place; this runbook captures the queue for the rest.

## Skills shipped (2026-04-26)

All six skills live in `.claude/skills/<name>/SKILL.md` (project-scoped, checked in).

| Skill | Type | Purpose |
|---|---|---|
| `doctrine-precommit` | read-only | Walks the pre-commit checklist from `agents/knowledge/agentic-team-doctrine.json` against the current diff. |
| `release-gate` | read-only | Runs the curated lean release gate (4 backend pytest files + `npm test`) in parallel and reports pass/fail. |
| `runbook-author` | side-effecting (stages) | Creates a new runbook with project naming + structural conventions; pre-populates from conversation context. |
| `runbook-archive` | side-effecting (stages) | `git mv`'s a superseded runbook to `agents/runbooks/archive/` and updates `doc_registry.json` if registered. |
| `deploy-droplet` | side-effecting (prod) | Runs the deploy script for frontend/backend (with optional `DEPLOY_AGENTIC_RUNTIME=1`), then post-deploy verify + healthcheck. |
| `enrichment-status` | read-only | Runs `check_enrichment_crawler.sh` and interprets output against the enrichment + celery-zombie runbooks. |

## Implementation notes (resolved during build)

- **Runbook structural pattern**: dominant convention is italicized inline metadata (`_Created: YYYY-MM-DD_`, `_Context: …_`, `_QA: …_`) followed by `## Purpose`, not bold-key blocks. The original draft of this runbook used the wrong format and has been corrected. `runbook-author` enforces the correct pattern.
- **`doc_registry.json` archival**: archived entries change their key path to include `archive/` AND set `status: "archived"`. Other fields (`lifecycle`, `tags`, `owner`) are preserved. `runbook-archive` reflects this exactly.
- **Registry coverage**: ~50 of ~94 runbooks are registered. `runbook-author` and `runbook-archive` both treat the registry update as optional based on whether an entry exists.
- **Deploy CI gate**: `server/deploy/deploy_to_droplet.sh` runs `scripts/check_ci_status.sh` as a hard gate at the top. `deploy-droplet` skill explicitly does not bypass.
- **Skill location decision**: started with user-global (`~/.claude/skills/`), moved all to project-scoped (`<repo>/.claude/skills/`) so they're shared with the team via git and only active inside this repo.

## Original priority order (now historical — kept for context)

### 1. `release-gate`

**Purpose:** Run the curated lean release gate exactly as documented in `CLAUDE.md`, surface a clear pass/fail with the failing tests listed.

**Trigger phrases:** "run the release gate", "release gate", "before I cut a release", "ready to release"

**Procedure outline:**
- Detect target (backend, frontend, or both) from context or ask
- Backend: `cd server && python -m pytest warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py -x --tb=short`
- Frontend: `cd client && npm test`
- Report pass count, failures by file, and a one-line verdict
- Do **not** bump VERSION or invoke `scripts/release.sh` — that is `release-cut`'s job

**Open questions before drafting:**
- Should the skill run backend + frontend in parallel by default, or sequentially?
- Should it auto-stash uncommitted changes, or refuse if the working tree is dirty?

**Estimated effort:** 30 min draft + smoke test.

---

### 2. `runbook-author`

**Purpose:** Create a new runbook with the project's naming convention and structure.

**Trigger phrases:** "write a runbook for X", "document this incident", "create a runbook"

**Procedure outline:**
- Filename pattern: `agents/runbooks/runbook-<topic-kebab>-YYYY-MM-DD.md` (date-stamped only when the runbook is incident- or event-tied; topic-only for evergreen runbooks like `runbook-cache-audit.md`)
- Pull the date from the current system clock; never invent
- Use the structural pattern observed in existing runbooks: title, **Created** date, **Status**, **Context**, decisions/changes, **Validation**, **Follow-ups**
- Surface registry update prompt: ask whether to add an entry to `agents/doc_registry.json`
- Do **not** auto-archive anything; that is `runbook-archive`'s job

**Open questions before drafting:**
- Should the skill scan recent commits / conversation history to pre-populate context, or always start blank?
- What's the canonical section ordering? Spot-check 5–10 active runbooks for the dominant pattern before locking it in.

**Estimated effort:** 45 min draft + iteration on section template.

---

### 3. `runbook-archive`

**Purpose:** Move a superseded runbook into `agents/runbooks/archive/` and reconcile `agents/doc_registry.json`.

**Trigger phrases:** "archive this runbook", "this runbook is superseded", "move to archive"

**Procedure outline:**
- Confirm target runbook with user before moving
- `git mv agents/runbooks/<name>.md agents/runbooks/archive/<name>.md` (preserves history)
- Update `agents/doc_registry.json` if the runbook is registered there (status → `archived`, or remove, depending on registry schema — verify before drafting)
- Append a brief "Superseded by …" header to the archived runbook if a successor exists
- Stage but do **not** commit; the user owns the commit

**Open questions before drafting:**
- Does `doc_registry.json` have an `archived` status, or do entries get removed entirely? Read the registry schema before writing the skill.
- Should the skill detect candidates automatically (runbooks not modified in N months, runbooks marked superseded in their body), or only act on explicit targets?

**Estimated effort:** 30 min draft. Depends on registry schema clarity.

---

### 4. `deploy-droplet`

**Purpose:** Wrap the deploy scripts with correct flags and post-deploy checks.

**Trigger phrases:** "deploy frontend", "deploy backend", "deploy to droplet", "ship to prod"

**Procedure outline:**
- Detect target: frontend (`client/deploy/deploy_to_droplet.sh battlestats.online`), backend (`server/deploy/deploy_to_droplet.sh battlestats.online`), or both
- For backend, ask whether to set `DEPLOY_AGENTIC_RUNTIME=1` (default no, per CLAUDE.md)
- Run `scripts/post_deploy_operations.sh` after deploy completes
- Run `scripts/healthcheck.sh` and report status
- Surface the deployed VERSION (`cat VERSION`) for confirmation

**Open questions before drafting:**
- Should the skill refuse to deploy if `release-gate` has not been run in the current session? (Probably no — too rigid; `doctrine-precommit` and operator judgment cover this.)
- Should it offer to tail the droplet logs after deploy, or stop at healthcheck?
- Is there a known failure-mode list (OOM during build, lock contention, etc.) it should pattern-match in the deploy output?

**Estimated effort:** 1h draft + a real deploy to validate.

**Risk:** This skill performs a destructive-ish action (modifies prod). Per the CLAUDE.md autonomy rules deploys are autonomous, but the skill should still print a one-line "deploying X to battlestats.online" before running and respect a user `n` to abort.

---

### 5. `enrichment-status`

**Purpose:** Run the enrichment crawler health check and interpret it against the runbook.

**Trigger phrases:** "how's enrichment", "check the crawler", "enrichment status", "is the crawler healthy"

**Procedure outline:**
- Run `./server/scripts/check_enrichment_crawler.sh battlestats.online`
- Parse the output sections (worker health, Redis lock, batch history/throughput/ETA, errors, live progress, clan crawl interference, periodic task state)
- Cross-reference any anomalies against `agents/runbooks/runbook-enrichment-crawler-2026-04-03.md` and `agents/runbooks/runbook-incident-celery-zombie-worker-2026-04-12.md`
- Recommend an action when one is warranted (e.g., "consumers=0 → zombie worker pattern, restart `battlestats-celery-background`")
- Do **not** restart services automatically; the recommendation goes to the user

**Open questions before drafting:**
- Are there other operational scripts that should be bundled into a broader `ops-status` skill instead (`healthcheck.sh`, queue depth, disk usage)? Decide whether enrichment is its own skill or one mode of a wider skill.
- What does "healthy" actually mean? Pin numeric thresholds (ETA < X hours, 0 SIGKILL events in last hour, etc.) before drafting so the skill's verdict is reproducible.

**Estimated effort:** 1h draft (most of it is parsing the script's output format and pattern-matching the runbook).

---

## Cross-cutting decisions to make before drafting

1. **Naming convention** — All five queued skills use kebab-case, action-or-noun form. Confirm this is what we want before locking in (alternative: verb-only like `cut-release`).
2. **Trigger discipline** — Skill descriptions are matched on keywords. Each draft must include a concrete trigger-phrase list in the frontmatter `description` so the model invokes it reliably without false positives.
3. **Read-only vs side-effecting** — `doctrine-precommit`, `release-gate`, `enrichment-status` are read-only. `runbook-author`, `runbook-archive`, `deploy-droplet` mutate state. Side-effecting skills should print a one-line action summary before acting.
4. **Skill-creator usage** — The `skill-creator` plugin (`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/`) provides a draft → eval → iterate loop. Worth running through it for at least one skill to learn the workflow, then template the rest.
5. **Discoverability** — Once a skill ships, add a brief mention to `CLAUDE.md` under a new "## Skills" section so future Claude sessions know they exist without having to grep `.claude/skills/`.

## Validation plan (per skill, when implemented)

- Smoke test: invoke with a representative trigger phrase in a fresh session, verify the right skill loads and runs end-to-end.
- Negative test: invoke with an adjacent-but-wrong phrase, verify the skill does **not** spuriously trigger.
- Idempotency: for read-only skills, run twice and confirm no side effects. For side-effecting skills, document the recovery path if the skill is interrupted mid-action.

## Follow-ups

- [x] All six skills implemented (2026-04-26)
- [x] CLAUDE.md updated with a "## Claude Code Skills" section pointing to `.claude/skills/`
- [ ] Smoke-test each skill in a fresh session with its trigger phrases — verify both positive triggering and absence of false positives
- [ ] Iterate on skill descriptions if real usage shows mis-triggers (the `description` frontmatter is what the model matches against)
- [ ] Revisit whether `enrichment-status` should fold into a broader `ops-status` skill once we have a real felt need
- [ ] Consider a `slm-session-init` skill if the SuperLocalMemory `session_init` MCP becomes a recurring per-session pattern (currently the SessionStart hook handles it)
- [ ] Consider a `release-cut` skill that chains `release-gate` → `scripts/release.sh <level>` → `deploy-droplet` once the constituent skills have been validated independently

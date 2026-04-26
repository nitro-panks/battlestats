---
name: deploy-droplet
description: Deploy battlestats backend or frontend to the production droplet (battlestats.online), then run post-deploy verification and healthcheck. Use when the user says "deploy frontend", "deploy backend", "deploy to droplet", "ship to prod", "push to prod", or asks to release a built version. Mutates production — always announces target before running and respects abort.
---

# deploy-droplet

Wraps the deploy scripts in `client/deploy/` and `server/deploy/` with the right flags, then runs post-deploy verification and a healthcheck. CLAUDE.md authorizes deploys to run autonomously, but this skill always prints a one-line "deploying X" before acting and surfaces the deployed VERSION for confirmation.

## When to invoke

- "deploy frontend", "deploy backend", "deploy to droplet", "ship to prod", "push to prod", "release to prod"
- After a release tag is cut and the user wants the new version live
- After a hotfix lands on `main` and needs to go out

Do **not** invoke for: dev-server starts (`npm run dev`), local-only changes, or rollbacks (those need explicit user direction and are not handled here).

## Procedure

### 1. Determine target

Parse the user's phrasing:
- "frontend", "client", "react", "next" → **frontend** deploy
- "backend", "server", "django", "api" → **backend** deploy
- "both", "full deploy", "everything" → backend then frontend (sequential, backend first)

If ambiguous ("deploy"), ask which target.

### 2. Decide on agentic runtime (backend only)

CLAUDE.md default is `DEPLOY_AGENTIC_RUNTIME=0` — the production droplet keeps LangGraph/CrewAI runtime opt-in. Only set to `1` if:
- The user explicitly asks ("deploy with agentic", "include agentic runtime")
- The deploy follows a change that touched `server/warships/agentic/` or `server/requirements-agentic.txt`

When unsure, ask. Don't ship agentic extras silently.

### 3. Pre-deploy announce

Print a single line before running anything:

```
Deploying <frontend|backend><[+ agentic]> to battlestats.online — VERSION=<cat VERSION>
```

Wait one beat. If the user interjects, abort.

### 4. Run the deploy

**Frontend:**
```bash
./client/deploy/deploy_to_droplet.sh battlestats.online
```

**Backend:**
```bash
./server/deploy/deploy_to_droplet.sh battlestats.online
```

**Backend with agentic extras:**
```bash
DEPLOY_AGENTIC_RUNTIME=1 ./server/deploy/deploy_to_droplet.sh battlestats.online
```

The backend script runs `scripts/check_ci_status.sh` first as a hard gate — if CI is red, the deploy refuses. That is intentional; do not bypass.

Run in foreground (deploys take 1–5 min and stream meaningful progress). Watch for these known failure patterns:
- **OOM during build** — see `runbook-deploy-oom-startup-warmers.md` (archived). Has been hardened; if it recurs, surface the runbook.
- **Stdin truncation** — fixed in commit `4662cb2`. If you see truncated env-var output, regression.
- **Lock contention on landing best-snapshot materialize** — fixed in `73b8002`. Same — flag if regression.

### 5. Post-deploy verification

Run the post-deploy verify subcommand for the matching target(s):

```bash
./scripts/post_deploy_operations.sh battlestats.online verify
```

Add `--realm na --realm eu` if the user wants per-realm verification (default covers both). For frontend-only deploys, add `--skip-backend`; for backend-only, `--skip-client`.

### 6. Healthcheck

```bash
./scripts/healthcheck.sh
```

This hits every public endpoint and validates HTTP 200 + meaningful body. Surface any failures verbatim.

### 7. Report

```
Deploy: <frontend|backend><[+ agentic]> → battlestats.online
Version: <VERSION>
Deploy:        OK | FAILED — <reason>
Post-deploy:   OK | FAILED — <verify summary>
Healthcheck:   OK | FAILED — <failing endpoints>

Verdict: SHIPPED | INVESTIGATE — <one-line summary>
```

If FAILED at any stage, **do not proceed to the next stage**. Surface the error and let the user decide whether to retry, roll back, or investigate.

## Scope and limits

- Deploys to `battlestats.online` only. For staging or other hosts, the user should invoke the script directly.
- Does not roll back. Rollback is a manual operation requiring user direction (release directories are kept under `/opt/battlestats-server/releases/` per `KEEP_RELEASES`).
- Does not bump VERSION or run `release.sh` — that is a separate workflow.
- Does not silently retry on failure. One attempt; surface the error.
- Do not deploy past a CI red. The backend script enforces this; respect it.

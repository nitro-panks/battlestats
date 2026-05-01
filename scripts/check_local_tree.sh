#!/usr/bin/env bash
#
# Hard gate: refuse to deploy from a local working tree that is behind
# `origin/main`. Without this check, a deploy run from a stale feature
# branch (or a worktree where someone forgot to `git pull --ff-only`)
# rsync's older code on top of production and silently regresses
# already-shipped fixes.
#
# Real incident: 2026-04-30 ~20:07 UTC, release 20260430200704 shipped a
# views.py older than commits 7a3b7b0 + 31d7cc5 (clan-members ordering
# rewrite) — clobbered the live ordering for ~24h before being noticed.
# The check below would have caught that deploy at the gate.
#
# Usage:
#   ./scripts/check_local_tree.sh              # exits non-zero if HEAD is behind origin/main
#   SKIP_TREE_CHECK=1 ./scripts/...            # bypass entirely (use only for explicit hotfix branches)
#
# Called by both deploy scripts as a pre-deploy check, after the CI gate.

set -euo pipefail

if [[ "${SKIP_TREE_CHECK:-}" == "1" ]]; then
  echo "⚠  SKIP_TREE_CHECK=1 — bypassing local-tree freshness gate"
  exit 0
fi

# Fetch origin/main without modifying the working tree. -q for quiet output;
# any network failure is fatal because we can't validate freshness without it.
if ! git fetch -q origin main 2>/dev/null; then
  echo "FATAL: 'git fetch origin main' failed — cannot verify local tree freshness." >&2
  echo "       Set SKIP_TREE_CHECK=1 to bypass if you know what you're doing." >&2
  exit 1
fi

# Walk back from HEAD looking for origin/main as an ancestor:
#   - HEAD == origin/main  → 0 (ok, in sync)
#   - HEAD ahead           → 0 (ok, local has more commits)
#   - HEAD behind          → 1 (BLOCK — missing commits from main)
#   - HEAD diverged        → 1 (BLOCK — not an ancestor)
if git merge-base --is-ancestor origin/main HEAD; then
  echo "✓ local tree is at or ahead of origin/main"
  exit 0
fi

LOCAL_HEAD="$(git rev-parse --short HEAD)"
ORIGIN_HEAD="$(git rev-parse --short origin/main)"
BEHIND_COUNT="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"

cat >&2 <<MSG

FATAL: local tree is BEHIND origin/main — refusing to deploy.

  HEAD          ${LOCAL_HEAD}
  origin/main   ${ORIGIN_HEAD}
  commits behind  ${BEHIND_COUNT}

Deploying now would rsync stale code over production and could silently
revert fixes that have already shipped to main (see incident 2026-04-30
in the dead-code/clan-crawl runbook chain).

Fix: in the working tree you're deploying from, run
    git pull --ff-only origin main
and re-run the deploy.

If this branch is a deliberate hotfix that diverges from main and you
understand the risk, bypass with:
    SKIP_TREE_CHECK=1 ./<deploy script>
MSG
exit 1

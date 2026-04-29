# Runbook: Dead-code & refactor cleanup pass

_Created: 2026-04-28_
_Context: Targeted survey across server + client for code that is unreferenced, documentation that has drifted, and a small number of bounded refactor candidates. Plan is split into independent tranches so each can pass the lean release gate and ship without coupling._
_Status: planned (QA-revised 2026-04-28)_

## QA pass — corrections applied

A QA pass against `origin/main` (HEAD `a4927a4`) before any tranche executes flagged four issues with the original audit. They are corrected in the body below; recording them here so the executor can reproduce the verifications.

| Original audit claim | QA finding | Action |
|---|---|---|
| `TraceDashboard.tsx` (462 LOC) is dead code; delete it. | The component talks to a *live* backend endpoint (`server/warships/views.py:1485` `agentic_trace_dashboard`) and CLAUDE.md:114 documents `/trace` as a gated route under `ENABLE_AGENTIC_RUNTIME=1`. The frontend `page.tsx` is missing — this is a half-shipped feature, not dead code. | Removed from Tranche A. Moved to **Out of scope** as a completion candidate (write the missing `client/app/trace/page.tsx`). |
| "Remove the empty `client/app/trace/` directory" step. | Directory does not exist on `origin/main` (only as a local artifact in some checkouts). Git does not track empty directories, so there is nothing to remove from the tracked tree. | Step removed from Tranche A. |
| `doc_registry.json:794` references `archive/runbook-enrichment-crawler-2026-04-02.md` — drift, prod doc is v2026-04-03. | Both files are correctly registered: line 794 marks the archived v1 as `status: archived`, line 1518 marks the live v2 as `status: active`. No drift. | Removed from Tranche B. |
| Tranche A step 3 says "remove the `WRDistributionDesign1SVG` half of the `export { ... }` statement (line 31)". | Both `Design1SVG` and `Design2SVG` named exports at `WRDistributionSVG.tsx:31` have zero importers; only the default export of the parent is consumed (by `PlayerDetailInsightsTabs.tsx:86,370`). Keeping a half-named export is incomplete. | Tranche A step rewritten to drop the entire `export { ... }` line and the `Design1SVG` import. |

Verifications executed during QA (re-runnable):

```bash
# Confirm zero external importers of ActivitySVG / TraceDashboard / WRDistributionDesign1SVG
grep -rn "ActivitySVG\b" client/app/ | grep -v components/ActivitySVG.tsx
grep -rn "TraceDashboard\b" client/app/ | grep -v components/TraceDashboard.tsx
grep -rn "WRDistributionSVG\|WRDistributionDesign[12]SVG" client/app/ | grep -v /components/WRDistribution

# Confirm preload tasks have zero callers
grep -rn "preload_battles_json\|preload_activity_data" server/ --include="*.py" | grep -v "tasks.py:5\|data.py:58"

# Confirm CLAUDE.md citations point to archived runbooks
grep -n "runbook-deploy-oom-startup-warmers\|runbook-player-page-load-priority" CLAUDE.md

# Confirm doc_registry has no drift on enrichment-crawler
grep -A3 "runbook-enrichment-crawler" agents/doc_registry.json
```

## Purpose

The codebase has accreted dead exports, dormant Celery tasks, undocumented env vars, and stale doc references over the last month of rapid feature delivery (battle-history rollout, dependabot cleanup, deploy worktree gymnastics). This runbook catalogs concrete findings with file:line evidence and groups them into tranches, ordered by risk and value.

**Out of scope (deferred to dedicated tranches):**
- Splitting `server/warships/data.py` (5 867 lines) — too large to bundle here; needs its own design.
- Splitting `server/warships/landing.py` (2 362 lines) — same.
- Frontend chart-fetch hook extraction (`useChartFetch()`) — touches 8 chart components; merits its own design + visual regression check.

## Findings, verified

All findings below were grep-verified after the audit. File:line references reflect `origin/main` at `a4927a4`.

### Tranche A — dead code removal (pure deletion, no behavior change)

| Item | Evidence |
|---|---|
| **`preload_battles_json_task`** at `server/warships/tasks.py:571` | No `.delay()` / `.apply_async()` callers anywhere. Not in `signals.py` Beat schedule. Function body is a no-op log + a call to a `preload_battles_json()` helper. Decorator-only orphan. |
| **`preload_activity_data_task`** at `server/warships/tasks.py:578` | Same shape as above. Zero callers, no Beat registration. |
| **`client/app/components/ActivitySVG.tsx`** (255 LOC) | `grep -rn "ActivitySVG\b" client/app/` returns only self-references. Not imported by `PlayerDetail`, `PlayerDetailInsightsTabs`, or any test. Standalone D3 component never wired up. |
| **`client/app/components/WRDistributionDesign1SVG.tsx`** | `WRDistributionSVG.tsx` imports both designs but only renders `Design2SVG` at line 21. The named re-exports at `WRDistributionSVG.tsx:31` (`Design1SVG`, `Design2SVG`) have zero external importers; only the default `WRDistributionSVG` export is consumed by `PlayerDetailInsightsTabs.tsx:86,370`. Both the `Design1SVG` import and the entire named-export line are dead. |

**Risk:** very low. Pure deletion. The Celery decorators don't even define helper functions used elsewhere. The frontend components are leaf nodes.

**Why ship as a single tranche:** these are independent removals that all pass the same release gate. Separate commits inside one PR is fine; separate PRs is overhead.

### Tranche B — documentation reconciliation

| Item | Evidence |
|---|---|
| **CLAUDE.md:152** cites `runbook-deploy-oom-startup-warmers.md` | File is at `agents/runbooks/archive/runbook-deploy-oom-startup-warmers.md` — it was archived but the citation wasn't updated. Either drop the line or repoint to the archive path. |
| **CLAUDE.md:184** cites `runbook-player-page-load-priority.md` | Same situation — archived at `agents/runbooks/archive/runbook-player-page-load-priority.md`. |
| **5 undocumented enrichment env vars in `tasks.py`** | `ENRICH_PAUSE_BETWEEN_BATCHES` (`tasks.py:1251`), `ENRICH_MIN_PVP_BATTLES` (`:1243,1313`), `ENRICH_MIN_WR` (`:1244,1314`), `ENRICH_BATCH_SIZE` (`:1307`), `ENRICH_DELAY` (`:1315`). All actively read; none documented in CLAUDE.md "Server runtime env" (lines ~276–303). |
| **3 undocumented player-refresh tier env vars in `tasks.py`** | `PLAYER_REFRESH_HOT_STALE_HOURS` (`:1168`), `PLAYER_REFRESH_ACTIVE_STALE_HOURS` (`:1170`), `PLAYER_REFRESH_WARM_STALE_HOURS` (`:1172`). Govern the graduated tier cadence. Defaults `12 / 24 / 72` hours respectively. Undocumented in CLAUDE.md. |
| **`.gitignore:36`** ignores `updated-landing` | Path no longer exists anywhere in the repo (verified `find . -name "updated-landing*"` → empty). Entry is leftover from a one-time landing migration. Safe to remove. |

**Risk:** zero — docs only.

### Tranche C — runbook archiving

Four runbooks have completed lifecycles (>26 days old, status reflects shipped work, no active rollout). Move to `agents/runbooks/archive/` and reconcile `agents/doc_registry.json`. Use the existing `runbook-archive` skill for consistency.

| Runbook | Reason |
|---|---|
| `runbook-abs-deprecation-2026-04-07.md` | Status: "Implementation complete on local branches"; ABS sort was removed and the cleanup landed in commit f4d2a5a (verified earlier this session). |
| `runbook-clan-tier-distribution-recovery-2026-04-02.md` | Recovery doc for an incident closed >26 days ago. No subsequent edits. |
| `runbook-efficiency-rank-qa-2026-04-02.md` | QA tranche tagged `feature-stable` in registry. |
| `runbook-droplet-memory-tuning-2026-04-02.md` | Memory tuning landed; no subsequent tunings since. |

**Risk:** zero — `git mv` only. The skill stages but does not commit.

### Tranche D — small refactor (optional, separate PR)

The two test-helper extractions below would meaningfully cut boilerplate, but they touch many test files at once. Listed for completeness; defer if Tranches A–C are enough scope for one cleanup pass.

| Item | Evidence |
|---|---|
| **Shared `tests/helpers.py`** for `create_test_player` / `create_test_clan` / `cache.clear()` setUp | `cache.clear()` in `setUp` repeats across 5+ test files (`test_views.py:17`, `test_landing.py`, `test_realm_isolation.py:74,119`, `test_data_product_contracts.py`, `test_incremental_battles.py`). Player.objects.create with similar kwargs is in dozens of cases. ~200 lines reducible. |
| **`assert_json_field(response, path, expected)`** helper for nested dict assertions | `test_views.py` has 709 `assertEqual(response.json()[...])` calls; many traverse nested keys verbatim. Helper would shorten and centralize the failure messages. |

**Risk:** medium — touches every test file. Must run the full lean release gate before/after to prove identical assertion semantics. Recommend its own PR with no other changes.

## Plan

Three independent tranches; ship in order, each a separate commit on `main`. Tranche D is optional and explicitly deferred.

### Tranche A — dead code (single commit)

1. Delete `preload_battles_json_task` and `preload_activity_data_task` from `server/warships/tasks.py:571,578`. Also delete the now-orphaned helpers `preload_battles_json()` (`server/warships/data.py:5847`) and `preload_activity_data()` (`server/warships/data.py:5857`) — confirmed no other callers via `grep -rn "preload_battles_json\|preload_activity_data" server/ --include="*.py" | grep -v "tasks.py:5\|data.py:58"` (zero results).
2. Delete `client/app/components/ActivitySVG.tsx` and `client/app/components/WRDistributionDesign1SVG.tsx`.
3. In `client/app/components/WRDistributionSVG.tsx`: drop the `import WRDistributionDesign1SVG from './WRDistributionDesign1SVG';` line (line 2) and the entire `export { WRDistributionDesign1SVG, WRDistributionDesign2SVG };` line (line 31). Both named exports have zero consumers; the only consumer is the default export. Leave the `import WRDistributionDesign2SVG` line and the JSX render at line 21 untouched.
4. Run lean release gate (backend pytest 4-file subset + frontend `npm run lint && npm test && npm run build`). Expect green.
5. Commit `refactor: drop unused preload tasks + ActivitySVG / WRDistributionDesign1SVG`. Push, frontend deploy optional (bundle should shrink slightly).

### Tranche B — docs (single commit)

1. Edit `CLAUDE.md:152` (`See \`runbook-deploy-oom-startup-warmers.md\``) → repoint to `archive/runbook-deploy-oom-startup-warmers.md`.
2. Edit `CLAUDE.md:184` (`See \`runbook-player-page-load-priority.md\``) → repoint to `archive/runbook-player-page-load-priority.md`.
3. Add the 8 missing env vars to CLAUDE.md "Server runtime env" with one-line descriptions each (5 enrichment vars + 3 player-refresh tier vars).
4. Remove the `updated-landing` line from `.gitignore:36`.
5. No release gate needed (docs only). Commit `docs: reconcile CLAUDE.md citations and .gitignore drift`. Push.

### Tranche C — archive (single commit per runbook, via skill)

For each of the four runbooks listed in Tranche C, invoke the `runbook-archive` skill with the runbook name. The skill `git mv`s into `archive/` and reconciles `doc_registry.json`. Stage all four into one commit (`docs: archive 4 closed runbooks`) for cleanliness. Push.

### Tranche D (deferred)

Open as a separate task. Expected scope: 1 helper module (`server/warships/tests/helpers.py`), ~200 lines removed from existing tests, full lean gate must pass.

## Verification

After Tranches A + B + C land:

1. Lean release gate: `cd server && python -m pytest --nomigrations warships/tests/test_views.py warships/tests/test_landing.py warships/tests/test_realm_isolation.py warships/tests/test_data_product_contracts.py -x --tb=short` → green.
2. Frontend gate: `cd client && npm run lint && npm test && npm run build` → green; bundle should be marginally smaller.
3. Doc-vs-code reconciliation:
   ```bash
   grep -E "runbook-deploy-oom-startup-warmers|runbook-player-page-load-priority" CLAUDE.md
   # expected: paths now contain "archive/"
   grep ENRICH CLAUDE.md | wc -l
   # expected: ≥ 5 new lines for the env vars
   ```
4. Confirm no regressions in production: `./server/scripts/check_enrichment_crawler.sh battlestats.online` → healthy. Healthcheck cron stays quiet.

## Doctrine pre-commit checklist (per `agents/knowledge/agentic-team-doctrine.json`)

- **Documentation review:** Tranche B is the doc tranche.
- **Doc-vs-code reconciliation:** Tranche B addresses the specific drift found.
- **Test coverage:** No new behavior; existing tests cover the deletions in Tranche A by virtue of the deleted code having no callers (and therefore no test coverage to remove).
- **Runbook archiving:** Tranche C is the archive tranche.
- **Contract safety:** No API or payload changes.
- **Runbook reconciliation:** Update the **Status** field of this runbook between tranches: `planned` → `tranche-a-shipped` → `tranche-b-shipped` → `tranche-c-shipped` → `resolved`. Archive this runbook itself once all three are done.

## Out of scope

- **`TraceDashboard.tsx` — completion candidate, not deletion.** The component talks to a working backend endpoint (`server/warships/views.py:1485` `agentic_trace_dashboard`, registered in CLAUDE.md:114 as the `/trace` route under `ENABLE_AGENTIC_RUNTIME=1`). Missing piece is `client/app/trace/page.tsx`. To finish: write the page that renders `<TraceDashboard />`. To retreat: separately decide to delete the component, the backend endpoint, and the CLAUDE.md reference together. Either way it's a deliberate scope call, not silent dead-code removal.
- **`data.py` and `landing.py` refactors.** Both are >2 KLOC and would benefit from being split, but the design is non-trivial and risks bundling subtle behavior changes into a "cleanup" pass. File a dedicated runbook when there's appetite.
- **Frontend `useChartFetch()` hook.** 8 chart components share fetch boilerplate but the consolidation needs a visual-regression smoke test that doesn't exist yet. Worth doing, but separate.
- **`selectColorByWR` consolidation across 3 chart files.** Same reasoning — touches rendering, needs visual check.
- **Test-helpers extraction (Tranche D).** Listed but explicitly deferred.

## References

- `client/app/components/WRDistributionSVG.tsx:2,21,31` — only Design2 actually renders; Design1 import + named-export line are dead.
- `server/warships/tasks.py:571,578` — orphan Celery tasks.
- `server/warships/data.py:5847,5857` — orphan helpers used only by the dead tasks above.
- `server/warships/tasks.py:1168-1172,1243-1244,1251,1307,1313-1315` — undocumented env vars.
- `agents/runbooks/archive/runbook-deploy-oom-startup-warmers.md`, `agents/runbooks/archive/runbook-player-page-load-priority.md` — archived but cited as live in CLAUDE.md.
- `.gitignore:36` — `updated-landing` orphan.
- `client/app/components/TraceDashboard.tsx`, `server/warships/views.py:1485` (`agentic_trace_dashboard`) — half-shipped feature; flagged as completion candidate in Out of Scope.

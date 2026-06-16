# Scope: per-table autovacuum tuning + weekly/monthly/yearly rollup keep-or-kill (2026-06-15)

_Created: 2026-06-15_
_Author role: DBA / backend_
_Status: **SCOPING ONLY — no code written.** Concern F of the DB-ops followup pass._
_Parent: `agents/runbooks/runbook-db-growth-analysis-2026-06-15.md` steps 1 (autovacuum) + 2 (rollup decision)._
_Sibling: `agents/work-items/scope-prod-nginx-timeouts-2026-06-15.md` (concern G)._

## TL;DR

- **Two independent, low-code DB-ops followups** carved out of the growth-analysis runbook. They share nothing but the DBA hat — scope them as separate slices.
- **Autovacuum tuning (step 1): genuinely outstanding, nothing shipped.** Repo grep finds **zero** per-table `autovacuum_*` reloptions anywhere (no migration, no SQL). All three target tables run on PG18 **global defaults** (`autovacuum_vacuum_scale_factor=0.2`, `autovacuum_vacuum_threshold=50`). The runbook's "candidates for per-table tuning" line is accurate and un-actioned. Smallest-safe-slice = one idempotent data migration (or a documented one-shot `ALTER TABLE` op) setting tighter scale-factors on the three churn tables. **Ready-to-implement** once the user picks the values.
- **Rollup keep-or-kill (step 2): DECISION-NEEDED — do NOT pick for the user.** Verified live state that sharpens the tradeoff: the period writer is **OFF in prod** (`BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED` is set nowhere in deploy → code default `"0"` → never runs), so the ~1.18 GB of `weekly/monthly/yearly` rows are **frozen/stale**, not live. They are read **only** by the legacy `?period=` escape hatch (`views.py:1450-1465`) that the frontend no longer calls. "Keep" therefore costs *engineering* (fix the yearly-YTD OOM first), not just disk; "kill" reclaims ~1.18 GB and closes a stale code path. Tradeoff laid out below — the user decides.
- **One stale runbook claim corrected:** the 2026-05-26 parent runbook's "nightly-rollup OOM follow-up" is real and **still open** — the period tier is gated OFF precisely because the yearly-YTD full-scan blew the 540s soft limit (`tasks.py:2128-2196`). Any "keep" path must fix that first.

## Verify-before-scope findings (what's already shipped)

| Claim under test | Verdict | Evidence |
|---|---|---|
| Per-table autovacuum tuning already applied | **NO — nothing shipped** | `grep -rn "autovacuum_vacuum_scale_factor\|autovacuum_vacuum_threshold\|SET (autovacuum\|ALTER TABLE.*autovacuum" server/` → empty. No migration carries reloptions. |
| Period rollup writer runs in prod | **NO — OFF in prod** | `BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED` appears in **no** deploy script / bootstrap / .env block; code default is `"0"` (`tasks.py:2181`). The nightly rollup task runs the daily layer only and **skips** the period rebuild. |
| Period tables are read by the app | **NO (frontend) / YES (legacy hatch only)** | Live battle-history endpoint serves `?window=day\|week\|month\|year`, **all of which map to `period:"daily"`** (`views.py:609-614`). Period tables are reached only via the legacy `?period=weekly\|monthly\|yearly` param (`views.py:1450-1465` → `_battle_history_period_table` 702-714), which the frontend "has stopped passing" (comment `views.py:1438-1440`). |
| Nightly-rollup OOM is fixed | **NO — still the reason the tier is gated off** | `tasks.py:2128-2131`: "the yearly-YTD aggregate is the long pole that blew the 540s soft time limit." `incremental_battles.py:1712`: "yearly is a full-YTD DB scan." |

(Autovacuum *live* reloptions could not be read this pass — a direct prod-DB query was correctly denied for a read-only code-scoping task. "Current = global defaults" is asserted from the repo-grep negative + PG18 defaults; confirm live before executing with the supplied query below.)

---

## STEP 1 — Per-table autovacuum tuning (ready-to-implement)

### Why

Three churn tables carry chronic dead-tuple loads (from the growth runbook's live read):

| Table | Live | Dead | Dead % | Churn shape |
|---|---|---|---|---|
| `warships_playerdailyshipstats` | 3.27M | **410K** | **~12%** | append + per-day upsert |
| `warships_player` | 1.06M | 174K | ~16% | 12.5M lifetime `n_tup_upd` (snapshot engine UPDATEs ~200K rows/day, `core_only`) |
| `warships_snapshot` | 2.52M | 97K | ~4% | +20× daily append since 2026-06-09 |

Autovacuum is *keeping pace* (dead counts are stable, not unbounded), so this is **planner-stats + IO/WAL hygiene + holding reusable space**, not a disk-fatal fix. On the global default `scale_factor=0.2`, a vacuum on `playerdailyshipstats` only triggers after ~650K dead tuples accumulate — too slack for a 3.3M-row table under steady upsert churn, which is why ~410K dead sits resident.

### Current vs proposed (per table)

**Current (all three): PG18 globals** — `autovacuum_vacuum_scale_factor = 0.2`, `autovacuum_vacuum_threshold = 50`, `autovacuum_analyze_scale_factor = 0.1`.

Proposed (starting point — tune to the user's tolerance; lower scale-factor = more frequent, smaller vacuums):

| Table | `autovacuum_vacuum_scale_factor` | `autovacuum_vacuum_threshold` | `autovacuum_analyze_scale_factor` | Rationale |
|---|---|---|---|---|
| `warships_playerdailyshipstats` | **0.02** | 5000 | 0.01 | Heaviest dead %; vacuum at ~70K dead not ~650K. |
| `warships_player` | **0.02** | 5000 | 0.01 | 200K UPDATEs/day; keep HOT-update reusable space tight, helps TOAST. |
| `warships_snapshot` | **0.05** | 5000 | 0.02 | Append-mostly; less urgent but the 200K/day step warrants a tighter trigger than 0.2. |

### Smallest-safe slice

A single **idempotent Django data migration** that issues `ALTER TABLE … SET (autovacuum_vacuum_scale_factor = …, …)` per table (reloptions are catalog-only; the `ALTER` is a fast metadata lock, no table rewrite, no `VACUUM` triggered). Reversible via a `reverse_code` that `RESET`s the options. This is the project convention (migrations are the durable home for schema-adjacent state) and keeps the change replayable on any environment.

- **Alternative (lighter, less durable):** a documented one-shot `ALTER TABLE` op run against prod from the growth runbook's connect recipe. Rejected as the primary because it leaves no replayable artifact and drifts from local/test — but acceptable if the user wants zero migration churn.

### Test-coverage plan

- Migration smoke test: a `pytest` that runs migrations forward on sqlite/Postgres and asserts the migration is a no-op-safe state migration (no model change → `makemigrations --check` stays clean). On Postgres, an optional assertion that `pg_class.reloptions` contains the expected keys for the three tables.
- No behavioral surface changes — no view/serializer/contract tests touched.

### Verification (when executing)

- Live current state (run BEFORE, with user authorization for a prod read):
  `SELECT relname, reloptions FROM pg_class WHERE relname IN ('warships_playerdailyshipstats','warships_player','warships_snapshot');` (expect `reloptions = NULL` for all three today).
- After: re-run the same query; confirm the keys are set. Then watch `n_dead_tup` for the three tables over ~48h via `pg_stat_user_tables` — dead % should trend down and plateau lower.
- Always `SET statement_timeout` before any prod query (killing `psql` does not cancel the backend — `pg_cancel_backend(pid)`).

---

## STEP 2 — weekly/monthly/yearly rollup: keep-or-kill (DECISION-NEEDED)

**This slice is a decision, not an implementation. The two paths below are presented so the user can choose — this doc deliberately does not pick.**

### What writes them

`rebuild_period_rollups_for_window(dates)` (`incremental_battles.py:1706-1747`), called from the nightly rollup task **only when `BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED=1`** (`tasks.py:2181-2196`). That env is unset in prod → the writer has **never run in prod** → the ~1.18 GB of period rows are a frozen backfill artifact, not a live-growing vector. The daily layer (`rebuild_daily_*`, same task) runs fine and finishes in seconds.

### Who reads them

- **Frontend: nobody.** All four UI windows (`day/week/month/year`) resolve to `period:"daily"` aggregations (`views.py:609-614`). `BattleHistoryCard.tsx` types a `'weekly'|'monthly'|'yearly'` Period union but the live API never serves those tiers to it.
- **Legacy API only:** an external caller passing `?period=weekly|monthly|yearly&windows=N` would read the period tables (`views.py:1450-1465`). The frontend stopped passing this (per the in-code comment). So the read path exists but is app-dead.

### The blocker on "keep"

The period tier is gated OFF *because it is broken*: the yearly-YTD aggregate is a full-table scan that **blew the 540s soft time limit** (`tasks.py:2128-2131`, `incremental_battles.py:1712`). This is the "known nightly-rollup OOM/timeout follow-up" the parent runbook flags — **still open.** "Keep" is not "flip the env on" — it is "fix the yearly-YTD scan (windowed/incremental rebuild, or move yearly to a separate long-soft-limit task) **then** flip it on."

### Tradeoff

| | **KEEP** (re-activate writer) | **KILL** (drop tables + data) |
|---|---|---|
| Disk | +~200 MB/day of new derived rows once live; ~1.18 GB stays | **Reclaims ~1.18 GB** (after a drop + `VACUUM`) |
| Engineering cost | **Must first fix the yearly-YTD OOM** (real work) before enabling | Migration to drop 3 tables; remove the `?period=` legacy reader + writer code; ~1 slice |
| Product value | Pre-aggregated weekly/monthly/yearly tiers *if* the UI ever surfaces them (today it doesn't) | Loses the pre-aggregated tiers; daily layer still supports any `?window=` the UI uses |
| Contract risk | None new | Removing `?period=weekly\|monthly\|yearly` is a (legacy, frontend-unused) **API contract removal** — needs a contract-test + `data_product_contracts` update; theoretically breaks an external caller |
| Reversibility | Trivial (env flip) | Tables/data gone; re-derivable from `BattleEvent`/daily via a backfill if ever needed |

### Open questions for the user (Step 2)

1. **Will the UI ever surface weekly/monthly/yearly tiers?** If "no / not on the roadmap," kill is clean. If "maybe," keep-but-fixed preserves the option at the cost of the OOM fix.
2. **Is the `?period=` legacy API a supported external contract** or purely internal vestige? (Determines whether kill is a breaking change or a dead-code removal.)
3. If **keep**: is fixing the yearly-YTD full-scan in-scope for this followup, or a separate item? (It is non-trivial — windowed rebuild or a dedicated long-soft-limit task.)

---

## Risks & cross-subsystem interactions

- **Autovacuum (step 1):** reloptions are catalog metadata — the `ALTER` itself takes a brief lock and triggers **no** table rewrite or vacuum. Lower scale-factors mean **more frequent** autovacuum runs → more background IO on the 2-vCPU managed PG. The growth runbook already flags `system_load15≈3` (saturated) at measurement time, so pick scale-factors conservatively and watch `system_load15` after rollout; back off if autovacuum contention shows. No Beat/kill-switch interaction.
- **Rollup kill path:** the period writer shares the nightly rollup task with the **daily** layer (which IS live and load-bearing for the battle-history endpoint). A kill must touch *only* the period branch (`tasks.py:2181-2196`) and the period reader — **do not** disturb `rebuild_daily_*`. The daily rollup task already has the OOM history; don't widen its soft-limit handling while removing the period tier.
- **Shared file `incremental_battles.py`** hosts both daily and period rebuild functions — edits for the kill path must be surgically scoped to the period helpers (`rebuild_period_rollups_*`, `_aggregate_into_period_table`, `_battle_history_period_table`).
- **Migrations** for both steps land in the same app; sequence them so the autovacuum migration (additive, safe) is independent of the rollup decision.

## Out of scope

- The `Snapshot` retention/downsampling policy (the runbook's biggest lever, step 4 there) — a separate product decision, not this followup.
- `battles_json` TOAST compaction / `PlayerSerializer` wire-trim / inactive-prune re-run (other deferred May Tier-1 items).
- `VACUUM FULL` on any table (a windowed maintenance op, not a code change).
- Storage autoscale / disk-alert enablement (an ops/DO-console action, runbook step 1).
- Any change to the daily rollup layer or the `?window=` (daily) battle-history contract.

## Open questions for the user (Step 1)

1. **Migration vs one-shot `ALTER`?** (durable/replayable vs zero migration churn — recommend migration.)
2. **How aggressive on scale-factor?** The 0.02/0.05 proposal is a conservative starting point; comfortable going tighter (0.01) on `playerdailyshipstats` if the extra autovacuum IO is acceptable on the 2-vCPU box?

## Related

- `agents/runbooks/runbook-db-growth-analysis-2026-06-15.md` — parent (steps 1, 2; Q2 bullets on dead-tuple hygiene and the rollups).
- `agents/runbooks/runbook-db-size-optimization-2026-05-26.md` — the per-table sizing query + the nightly-rollup OOM follow-up origin.
- `agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md` — the rollup engine + the 540s period-tier timeout history.
- Memory `project_rollup_period_timeout` — the period-tier 540s OOM diagnosis.
</content>
</invoke>

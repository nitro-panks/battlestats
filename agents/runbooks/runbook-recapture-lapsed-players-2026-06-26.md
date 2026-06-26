# Runbook — Recapture lapsed (returning) players (2026-06-26)

## Problem

The observation floor only looks at **active-7d** players (`Player.last_battle_date >= today - 7`). A player who goes quiet for longer drops out of floor scope, their stored `last_battle_date` freezes and only ages, and **nothing passively re-checks them**. So when a long-dormant player quietly comes back, their new battles sit uncaptured until an *event* forces a refresh: a profile view (`enrich_player_on_view`) or a clan crawl reaching their clan (`_classify_player_yield` → `reactivated`). A clanless, unviewed returner can play for days fully invisible to battle capture — the "click a player we think has been gone 100+ days and find new battles waiting" symptom.

The clan crawl is the *only* existing path that re-detects dormant→active, and only for members of clans it happens to be mid-crawl on. Everything else needs a human to click.

## Why it's cheap to fix

The economics are asymmetric:
- **Detection** needs only WG `account/info`, which is **bulk** (100 account_ids/call, `_bulk_fetch_account_info`) and returns `last_battle_time`. Re-checking the whole dormant pool is a few thousand WG calls.
- **Capture** (the expensive `ships/stats`, single-account-only, ~0.5–1.3s each) is only ever paid for players who *actually* returned.

Dormant-pool sizes measured 2026-06-26 (`is_hidden=False`, by `last_battle_date` band):

| realm | active ≤7d | 8–30d | 31–90d | 91–180d | 181–365d | 365d+ |
|---|--:|--:|--:|--:|--:|--:|
| asia | 65,072 | 21,632 | 26,994 | 25,710 | 27,616 | 86,464 |
| eu | 88,162 | 41,685 | 49,027 | 50,636 | 50,559 | 192,900 |
| na | 51,752 | 19,969 | 25,767 | 29,789 | 29,545 | 125,450 |

The recoverable **8–365d** band is ~102k/192k/105k per realm (~400k total). A full weekly sweep is ~4,000 WG calls/week ≈ 570/day — negligible against the ~9 req/s budget. The 365d+ tail (~400k more) is huge and low-yield, so v1 bounds the band to 365d.

## Design — "let the floor catch it" (detect-only re-entry)

Detection and capture are **decoupled**. The sweep only needs to *correct* `last_battle_date` for a returner: the moment that date moves back inside the 7-day window, the player automatically becomes a floor candidate (the floor filters `last_battle_date >= cutoff` and is recency-first with a stale-observation gate), so they sort to the top and get `ships/stats`-harvested on the next floor cycle. **No new harvest path.** This was an explicit product choice (vs. dispatching the expensive call directly): zero extra expensive calls beyond what the floor already does, and self-throttling.

Like `refresh_clan_member_idle_task`, the promote writes **only** `last_battle_date` + `days_since_last_battle`, **never `last_fetch`** — bumping `last_fetch` would suppress the floor's real per-player full refresh (the one that rebuilds `battles_json`) for ~23h.

### LRU rotation (the production knob)

A single recency-first pass would re-check the just-lapsed end forever and never reach the deep >90d tail — exactly the "gone 100+ days" case. So:

- New column `Player.last_idle_check_at` (migration `0078`). The candidate query orders `last_idle_check_at ASC NULLS FIRST` (never-checked first), recency as tiebreak.
- In apply mode the sweep stamps `last_idle_check_at = now` on **every checked row** (not just returners). Each run takes the least-recently-checked `--limit` dormant rows, so the cursor walks the whole pool and then maintains it.
- `RECAPTURE_LAPSED_LIMIT` (30000) is sized so the largest realm's band (EU ~192k) rotates fully in ~a week of daily runs.
- Transient WG batch failures **don't** stamp the cursor (the rows retry next run rather than rotate past unchecked).

No new index for v1: the candidate query is a seq-scan + top-N sort run once/realm/day (the same shape `snapshot_active_players` already runs); add `(realm, last_idle_check_at)` only if it shows up as DB cost.

## Components

- Command: `server/warships/management/commands/recapture_lapsed_players.py` — the sweep + a yield readout bucketed into *reactivated INTO active_7d* (floor harvests free) vs *advanced but still lapsed* (out of floor scope), each split clanned/clanless. Clanless-into-7d is the marginal value nothing else recovers.
- Task: `recapture_lapsed_players_task` (`tasks.py`, `background` queue) — env-gated, lock-wrapped thin wrapper around the command (mirrors `snapshot_active_players_task`).
- Beat: `recapture-lapsed-players-{realm}` in `signals.py`, per-realm striped daily ~10:10/10:30/10:50 UTC (clear of the realm-hour analytical-warmer burst and the 08:x drift reclassify), registered `enabled` only when `RECAPTURE_LAPSED_ENABLED=1`.
- Tests: `warships/tests/test_recapture_lapsed_players.py` (detect-only writes nothing; apply promotes a returner + stamps the cursor without touching `last_fetch`; still-dormant is stamped but not promoted; band excludes active + >max tail; LRU cursor picks never-checked first; task gate skips when disabled).

## Env knobs

See `ops-env-reference.md` for the full list. Summary: `RECAPTURE_LAPSED_ENABLED` (master gate, default 0), `RECAPTURE_LAPSED_APPLY` (writes gate, default 0 = detect-only), `RECAPTURE_LAPSED_{MIN,MAX}_DAYS` (8/365), `RECAPTURE_LAPSED_LIMIT` (30000), `RECAPTURE_LAPSED_DELAY` (0.2).

## Rollout (measure-then-trust)

1. **Deploy** with `RECAPTURE_LAPSED_ENABLED=0` (the schedule registers disabled; zero behavior change). Apply migration `0078`.
2. **Measure yield in prod, safely.** WG calls must share prod's Redis token bucket, so run on the droplet (locally there's no `REDIS_URL` → LocMemCache → the limiter wouldn't coordinate and could push WG over budget). Either flip `RECAPTURE_LAPSED_ENABLED=1` + `RECAPTURE_LAPSED_APPLY=0` for a day (the task logs the reactivation yield, writes nothing), or run the command directly: `python manage.py recapture_lapsed_players --realm eu --limit 5000` (detect-only by default). Read the `advanced(returned)` / `reactivated INTO active_7d` lines — especially CLANLESS, the players nothing else recovers.
3. **If yield earns it**, set `RECAPTURE_LAPSED_APPLY=1`. The sweep starts promoting returners (floor harvests them) and stamping the rotation cursor. Watch the first week: the cursor should walk the band (a realm's oldest `last_idle_check_at` advances), and `/observation` distinct_productive should pick up a small bump from the recovered returners entering floor scope.
4. **Guardrails:** WG limiter waits / 0 `hit 407` (`journalctl -u battlestats-celery-background -g "WG rate limiter"`); managed-PG `load15` (the cursor-stamp UPDATEs are light but watch the daily striped window doesn't stack with warmers). The whole family is reversible via `RECAPTURE_LAPSED_ENABLED=0`.

## Decisions & provenance

- **Provenance:** a prior session left a detect-only measurement prototype (`recapture_lapsed_players.py`, uncommitted in the worktree) plus the empty `feat/recapture-lapsed-players` branch. 2026-06-26 productionized it: added the rotation cursor + stamp, the Celery task + Beat family, the two-flag gating, tests, and docs.
- **Decision 1 — re-entry path: "let the floor catch it" (chosen) vs. direct dispatch.** The sweep only corrects `last_battle_date`; the recency-first floor harvests the returner next cycle. Picked for zero extra expensive (`ships/stats`) calls beyond what the floor already does, and because it is self-throttling. The alternative (dispatch a `ships/stats` observation immediately per confirmed mover) buys instant harvest at the cost of a per-returner expensive call and more code; rejected for v1. If returner latency-to-harvest turns out to matter, that is the upgrade.
- **Decision 2 — coverage cadence: full pool ~weekly (chosen) vs. daily vs. banded.** `LIMIT=30000` daily rotates the largest realm (EU ~192k in-band) in ~a week; catches a returner within ~7 days of their first battle back. Cost is not the constraint (WG is trivial; the binding cost is light cursor-stamp UPDATEs), so weekly is the gentle default. Tighten to daily, or weight recent-dormant over the deep tail, only if measured yield says it pays.
- **Why a model field, not a Redis cursor / id-modulo:** the field filters *in* the candidate SQL (the cursor advances with the query) and survives restarts; it also gives an observable "when last checked" per row. No new index for v1 (seq-scan + top-N, once/realm/day, same shape as `snapshot_active_players`).
- **Safety finding (why yield must be measured on the droplet):** the WG token-bucket limiter lives in Redis. Locally there is no `REDIS_URL`, so the cache falls back to `LocMemCache` and the limiter would not coordinate with prod; a laptop run's ~5 req/s could push the global WG budget over and 407 live users. Run any WG-calling measurement on the droplet, where it shares the real bucket.

## Test-harness gotcha (for whoever runs the suite next)

Run backend tests on sqlite: `DJANGO_SECRET_KEY=x DB_ENGINE=sqlite3 python -m pytest warships/tests/ --nomigrations -q`. Two traps:
- Settings reads `DJANGO_SECRET_KEY` (settings.py:13), but the env file defines `SECRET_KEY`; without `DJANGO_SECRET_KEY` set, the cookie-signer view tests fail `ImproperlyConfigured: SECRET_KEY ... empty` (~194 failures). Not a code regression.
- A linked worktree has **no `.env`/`.env.secrets`** (gitignored, only in the main checkout); copy them in (`cp server/.env server/.env.secrets <worktree>/server/`) or the suite can't load settings. The copies stay gitignored.

Clean run with both fixes: **805 passed, 2 skipped**.

## Status

Built 2026-06-26 on `feat/recapture-lapsed-players` (worktree `.claude/worktrees/battlestats-wt-recapture-lapsed`). **Not committed; not deployed; not measured in prod.** Defaults ship it inert (`ENABLED=0`). Yield measurement (Rollout step 2) is the gate on whether to enable writes.

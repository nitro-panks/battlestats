# Crawler & Update-Process Cost Map — Where We Spend Time, Compute & Bandwidth

**Date:** 2026-06-06
**Author role:** data architect / systems engineer
**Goal of this doc:** Inventory every crawler and update process, when it runs, what fields it
writes, and how expensive it is — so we can re-aim resources at the one thing that matters most:
**capturing `battles_history` daily for our active players.**

> All cadences, limits and gating reflect the **production** config (`server/.env.cloud` +
> `server/deploy/deploy_to_droplet.sh`), verified 2026-06-06. Live volume numbers were sampled
> from the production DB the same day.

---

## 0. The cost metric

The binding constraint on this system is **not** CPU or disk — it is the **shared Wargaming (WG)
API budget**. One application_id, ~**10 requests/second** sustainable, shared across all four Celery
queues, all three realms, and every process below. There is **no global rate limiter** (the
token-bucket is design-only, `runbook-wg-rate-limiter-token-bucket-2026-06-05.md`), so when two
hot paths overlap they sum past the ceiling and WG returns `407 REQUEST_LIMIT_EXCEEDED`. We have
already been bitten by this twice (the crawl-lock-starves-floor incident 2026-06-05; the
seasonstats 407 fan-out).

So the primary cost unit is:

> **WCE/day — Wargaming-Call-Equivalents per day.** One outbound WG API request = 1 WCE,
> regardless of how many players it covers. A *bulk* call covering 100 players is **1 WCE**, not 100.

This single unit captures the scarce resource. Secondary axes (DB write volume, CPU/wall-clock)
are noted where they matter but are not the bottleneck today.

**Budget envelope:**

| Quantity | Value |
|---|---|
| Sustainable WG rate (1 app-id) | ~10 req/s |
| Theoretical daily ceiling | ~864,000 WCE/day |
| Practical ceiling (leave headroom for 407s + request-driven spikes) | ~**500,000–650,000 WCE/day** |

**Expense tiers used below:** 🔴 dominant consumer · 🟡 material · 🟢 cheap / free (0 WCE).

---

## 1. TL;DR — the findings that matter

1. **We capture real battle activity for ~26k players/day, and ~half our observation budget is
   spent re-confirming players who didn't play.** In the last 24h we observed **50,570 distinct
   players** but only **26,129** of those produced a real battle (`BattleEvent.battles_delta > 0`)
   — a **51.7% productive rate**. The true "daily active" denominator is circular (we only learn a
   player battled by observing them); proxies put it at ~46k (last battle ≤24h) to ~62k (≤48h),
   both undercounts. So we are catching *somewhere around half* of daily battle activity **and**
   burning the other half of our capture budget on zero-delta snapshots. Sparse capture also
   **mis-buckets** which day a battle is attributed to (the `detected_at` bucketing incident),
   so "daily" granularity genuinely needs near-daily observation of players who are battling.

2. **The clan crawl is the dominant WG consumer and serves none of this goal.** It runs
   continuously (`core_only=False` in prod), and **>90% of its calls are the per-member
   achievements + efficiency fetch** — `account/achievements/` is **un-batched (1 WCE/player)**.
   That spend competes directly with battle-history capture and has demonstrably starved it.

3. **Battle-history capture is being done the expensive way.** The observation floor captures at
   **2–3 WCE per player** (per-player `account/info` + `ships/stats`). Yet **enrichment already
   fetches the identical `ships/stats` data in bulk — 100 players per call (~0.02 WCE/player)** —
   verified at `enrich_player_data.py:131`. The floor simply doesn't use the bulk path.

4. **The arithmetic of the goal:**
   - Cover **all 254,908 active-7d** players daily, per-player floor style: 254,908 × 2 = **~510,000 WCE/day** → eats the entire practical budget.
   - Cover the same set daily, **bulk** style: 254,908 / 100 × 2 = **~5,100 WCE/day** → **~1% of the cost**.
   - **Batching the capture is a ~100× win and is the single highest-leverage change available.**

5. **Two flags are already off for good reason** and don't need touching: period rollup
   (`BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED=0`, times out at the 540s soft limit) and reconcile.

The rest of this doc is the evidence.

---

## 2. Production gating state (what is actually running)

| Flag | Prod value | Effect |
|---|---|---|
| `ENABLE_CRAWLER_SCHEDULES` | **1** | clan crawl, player refresh, ranked refresh, observation floor, watchdog all live |
| `BATTLE_HISTORY_CAPTURE_ENABLED` | **1** | `BattleObservation` + `BattleEvent` written on every refresh/floor pass |
| `BATTLE_HISTORY_ROLLUP_ENABLED` | **1** | nightly `PlayerDailyShipStats` rebuild runs (04:30 UTC) |
| `BATTLE_HISTORY_RANKED_CAPTURE_ENABLED` | **1** | ranked `ships/stats` captured too (adds 1 WCE/player on ranked paths) |
| `BATTLE_OBSERVATION_COMPACT_ENABLED` | **1** | stale observation JSON NULLed daily (disk reclaim) |
| `SHIP_BADGE_SNAPSHOT_ENABLED` | **1** | weekly ship-standings snapshot (self-gates on season boundary) |
| `BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED` | **0** (unset) | weekly/monthly/yearly tiers dormant — 540s timeout |
| `BATTLE_HISTORY_RECONCILE_ENABLED` | **0** (unset) | nightly audit dormant |
| `CLAN_CRAWL_CORE_ONLY` | **unset → False** | clan crawl does the **expensive per-member** efficiency + achievements fetch |

Live volume sampled 2026-06-06:

| Metric | Value |
|---|---|
| Total players | 1,035,814 |
| Active-7d players (battle-history target) | **254,908** |
| Active-30d players | 325,061 |
| Active-90d players | 460,333 |
| `BattleObservation` rows written, last 24h | 69,108 |
| **Distinct players observed, last 24h** | **50,585** |
| `BattleEvent` rows written, last 24h | 128,749 |
| `PlayerDailyShipStats` rows, prior day | 102,532 |

---

## 3. Master process inventory

WG-consuming processes are ordered by daily cost. "Serves battle-history?" flags whether the
process contributes to the active-player `battles_history` goal.

| # | Process | Trigger / cadence (prod) | WG calls per unit | Est. WCE/day | Tier | Serves battle-history? |
|---|---|---|---|---|---|---|
| 1 | **Clan crawl** `crawl_all_clans_task` | continuous; daily per-realm, 1 realm at a time, ~14-day pass | ~2/clan + members/100 (bulk) + **2/member (achievements+efficiency, mostly un-batched)** | **up to ~345k** (`-c1` worker ceiling at 0.25s/call; dominant) | 🔴 | **No** |
| 2 | **Incremental player refresh** `incremental_player_refresh_task` | every 180 min/realm (8×/day), ~1,200 players/cycle | 3–5/player; **observation written at 0 marginal cost (piggyback)** | ~115k | 🔴 | **Yes** (piggyback capture) |
| 3 | **Observation floor** `ensure_daily_battle_observations_task` | every 6 h/realm (4×/day), ≤3,000 players/cycle | **2 (random) / 3 (ranked)** per player | ~60k–90k | 🟡 | **Yes** (dedicated backstop) |
| 4 | **Ranked incremental** `incremental_ranked_data_task` | every 120 min/realm (12×/day), ≤150 players/cycle | 2 + N seasons (**`seasons/shipstats` un-batched, per-season**) | ~36k | 🟡 | Partial (ranked only) |
| 5 | **Player enrichment** `enrich_player_data_task` | continuous self-chain; 15-min Beat kickstart; 500/batch | **~0.02 bulk + 1/ranked-player** | ~10k–40k (backlog-driven) | 🟢/🟡 | **Yes** (bulk capture of backlog) |
| 6 | **Request-driven** player/clan page hydration | on user visit | 2–4/player, 2/clan | tiny (~200 clans + 1,819 players/wk viewed) | 🟢 | Yes (viewed players only) |
| 7 | **PoC poll** `poll_tracked_player_battles_task` | 60 s — **off** (`BATTLE_TRACKING_PLAYER_NAMES` empty) | 2/player | 0 | 🟢 | n/a |
| 8 | **Nightly daily rollup** `roll_up_player_daily_ship_stats_task` | 04:30 UTC | **0** (DB-only) | 0 | 🟢 (CPU long-pole) | Yes (materializes daily table) |
| 9 | **Period rollup** | 04:30 UTC — **off** | 0 | 0 | 🟢 | dormant |
| 10 | **Reconcile** | 05:00 UTC — **off** | 0 | 0 | 🟢 | dormant (audit) |
| 11 | **Warmers** (landing ×, distributions, correlations, hot-entity, bulk-loader, recently-viewed, clan-tier-dist) | 10 min – 12 h | **0** (cache rebuild from DB) | 0 | 🟢 | No |
| 12 | **Snapshots** (best-player materializer, ship-top-player) | daily / weekly | 0 (DB-only) | 0 | 🟢 | No |
| 13 | **Maintenance** (observation compaction, clan-crawl watchdog) | daily / 5 min | 0 | 0 | 🟢 | No |

> Daily WCE figures for the crawl, refresh and floor are **order-of-magnitude estimates** — actual
> volume depends on per-clan member counts and how many players fall stale each cycle. They are
> sized from cadence × per-cycle limit × per-unit cost. The relative ordering (crawl ≫ refresh >
> floor ≫ everything else) is robust.

**These consumers time-share — they do not all run at once.** Incremental refresh (#2) and ranked
(#4) **defer entirely while the clan crawl holds its lock**, and the observation floor (#3)
coexists at 2.7× slower pace (0.8s vs 0.3s/player). So in practice the clan crawl has **de facto
priority over battle-history capture**: when it runs, the player-side refresh pauses and the floor
throttles. That is backwards from the stated goal — the largest consumer, which serves no
battle-history need, currently pre-empts the ones that do. (The additive WCE/day figures above are
the *if-run-flat-out* sizing; the deferral logic is what keeps them from colliding into 407s today,
at the cost of starving capture during crawls.)

**Read this table as:** the three 🔴/🟡 WG consumers (#1 clan crawl, #2 refresh, #3 floor) plus
ranked (#4) account for essentially the entire WG budget — and **#1, the largest, contributes
nothing to the battle-history goal yet out-prioritizes the work that does.**

---

## 4. The battle-history pipeline (the part we care about)

Data flows in three stages. Only stage 1 spends WG budget.

```
 Stage 1: CAPTURE          Stage 2: DIFF              Stage 3: ROLL UP
 (WG calls)                (DB compute, 0 WG)         (DB compute, 0 WG)
 ┌────────────────┐        ┌──────────────┐           ┌──────────────────────┐
 │ BattleObservation│ ──▶  │  BattleEvent  │  ──nightly▶ │ PlayerDailyShipStats │
 │  (raw snapshot)  │ diff │ (per-battle Δ)│  04:30 UTC  │ (per-day per-ship)   │
 └────────────────┘        └──────────────┘           └──────────────────────┘
        ▲                                                        │ (period tiers OFF)
        │ written by:                                            ▼
        │ • incremental refresh (piggyback, 0 marginal WCE)   Weekly/Monthly/Yearly
        │ • observation floor (2–3 WCE/player)  ◀── the expensive path
        │ • ranked on-render (3 WCE/player)
        │ • request-driven / PoC poll
```

The **only** thing standing between us and "daily battle history for every active player" is
**Stage 1 capture coverage**. Stages 2 and 3 are free DB compute and already keep up
(128,749 events → 102,532 daily rows yesterday). The bottleneck is purely how many distinct active
players we can afford to *observe* per day under the WG budget — which is exactly why the
per-player-vs-bulk capture choice dominates everything.

### 4.1 Stage 1 — `BattleObservation` (raw capture)

`server/warships/models.py:468–509`. Every writer sets the same fields together.

| Field | Type | Source | Notes |
|---|---|---|---|
| `player` | FK → Player | — | cascade |
| `observed_at` | DateTimeField | `auto_now_add` | capture timestamp |
| `pvp_battles` | IntegerField | WG `account/info` | totals used to detect "did a battle happen" |
| `pvp_wins` | IntegerField | WG `account/info` | |
| `pvp_losses` | IntegerField | WG `account/info` | |
| `pvp_frags` | IntegerField | WG `account/info` | |
| `pvp_survived_battles` | IntegerField | WG `account/info` | |
| `last_battle_time` | DateTimeField | WG `account/info` | |
| `ships_stats_json` | JSONField | WG `ships/stats` | raw per-ship payload (random) — **bulk-capable endpoint** |
| `ranked_ships_stats_json` | JSONField | WG `seasons/shipstats` | only if ranked capture enabled; NULL ≠ [] is load-bearing |
| `source` | CharField | POLL\|MANUAL | |

**Writers & cost** (`incremental_battles.py`, `tasks.py`, `data.py`):

| Writer | Cadence | WG calls/player | Endpoints |
|---|---|---|---|
| `refresh_player_detail_payloads` (piggyback on incremental refresh) | 180 min/realm | **0 marginal** (reuses refresh's `ships/stats` fetch) | — |
| `ensure_daily_battle_observations_task` (floor) | 6 h/realm | **2 random / 3 ranked** | `account/info`, `ships/stats`, [`seasons/shipstats`] |
| `refresh_ranked_observation_task` (on profile render, 15-min dedup) | on-demand | 3 | `account/info`, `ships/stats`, `seasons/shipstats` |
| `poll_tracked_player_battles_task` (PoC, **off**) | 60 s | 2 | `account/info`, `ships/stats` |

### 4.2 Stage 2 — `BattleEvent` (per-battle deltas, **0 WG**)

`models.py:511–602`. Computed by diffing consecutive observations
(`record_observation_from_payloads`). Pure DB compute.

| Field group | Fields | Type |
|---|---|---|
| Identity | `player` (FK), `detected_at`, `mode` (random\|ranked), `season_id`, `ship_id`, `ship_name` | FK / datetime / char / int |
| Core deltas | `battles_delta`, `wins_delta`, `losses_delta`, `frags_delta`, `damage_delta`, `xp_delta`, `planes_killed_delta`, `survived` | IntegerField / Bool |
| **Phase 7 widening** (gunnery/torpedo/spotting/caps) | `main_shots_delta`, `main_hits_delta`, `main_frags_delta`, `secondary_shots_delta`, `secondary_hits_delta`, `secondary_frags_delta`, `torpedo_shots_delta`, `torpedo_hits_delta`, `torpedo_frags_delta`, `damage_scouting_delta`, `ships_spotted_delta`, `capture_points_delta`, `dropped_capture_points_delta`, `team_capture_points_delta` | IntegerField (default 0) |
| Provenance | `from_observation` (FK), `to_observation` (FK) | FK → BattleObservation |

Unique key: `(from_observation, to_observation, ship_id)` for random;
`(…, season_id)` for ranked.

### 4.3 Stage 3 — `PlayerDailyShipStats` (per-day per-ship, **0 WG**)

`models.py:604–686`. Rebuilt nightly by `roll_up_player_daily_ship_stats_task` (04:30 UTC) by
summing `BattleEvent` rows per `(date, player, ship_id, mode, season_id)`. Idempotent
(delete + rebuild for trailing `BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS=3`).

| Field group | Fields | Type |
|---|---|---|
| Identity | `player` (FK), `date`, `ship_id`, `ship_name`, `mode`, `season_id` | FK / date / int / char |
| Aggregates | `battles`, `wins`, `losses`, `frags`, `damage`, `xp`, `planes_killed`, `survived_battles` | Integer / BigInteger |
| Phase 7 | `main_shots`, `main_hits`, `main_frags`, `secondary_shots`, `secondary_hits`, `secondary_frags`, `torpedo_shots`, `torpedo_hits`, `torpedo_frags`, `damage_scouting`, `ships_spotted`, `capture_points`, `dropped_capture_points`, `team_capture_points` | IntegerField |
| Bookkeeping | `first_event_at`, `last_event_at`, `updated_at` | DateTime |

### 4.4 Period tiers — `PlayerWeekly/Monthly/YearlyShipStats` (dormant)

`models.py:776–860`. Rebuilt from `PlayerDailyShipStats` **only when
`BATTLE_HISTORY_PERIOD_ROLLUP_ENABLED=1`** — currently **off** because the yearly-YTD rebuild
exceeds the 540s soft limit. Randoms-only; **not** Phase-7-widened yet. Fields: `period_start`,
`ship_id`, `ship_name`, `battles`, `wins`, `losses`, `frags`, `damage`, `xp`, `planes_killed`,
`survived_battles`, `first_event_at`, `last_event_at`, `updated_at`.

---

## 5. Player / clan refresh field maps (the WG-spending writers)

These are the functions that actually consume WG budget on the player side. Each table:
**field → type → source/WG endpoint.**

### 5.1 `update_player_data(player)` — core refresh · `data.py:4642`
Freshness gate: skips if `last_fetch` < 1,400 min old. **WG: 2–3** (`account/info` +
optional `clans/accountinfo` + `ships/badges` via efficiency).

| Field | Type | Source |
|---|---|---|
| `name`, `player_id`, `clan`, `creation_date` | char / int / FK / datetime | `account/info` |
| `last_battle_date`, `days_since_last_battle` | datetime / int | `account/info` |
| `is_hidden` | bool | `account/info` |
| `total_battles`, `pvp_battles`, `pvp_wins`, `pvp_losses`, `pvp_frags`, `pvp_survived_battles` | int | `account/info` (nulled if hidden) |
| `pvp_deaths`, `actual_kdr`, `pvp_ratio`, `pvp_survival_rate`, `wins_survival_rate` | float | derived |
| `verdict` | char | derived |
| `last_fetch` | datetime | clock |

Transitively calls `update_player_efficiency_data`, `refresh_player_explorer_summary`.

### 5.2 `update_battle_data(player_id)` — random battles · `data.py:2178`
Freshness gate: 15 min. **WG: 1–2** (`ships/stats` + optional `seasons/shipstats`). **This is the
fetch the observation piggyback reuses for free.**

| Field | Type | Source |
|---|---|---|
| `battles_json`, `battles_updated_at` | JSON / datetime | `ships/stats` |
| `tiers_json`, `tiers_updated_at` | JSON / datetime | derived from `ships/stats` |
| `type_json`, `type_updated_at` | JSON / datetime | derived |
| `randoms_json`, `randoms_updated_at` | JSON / datetime | derived |

### 5.3 `update_player_efficiency_data` · `data.py:393` — **WG: 1** (`ships/badges`), gate 24 h
| Field | Type | Source |
|---|---|---|
| `efficiency_json`, `efficiency_updated_at` | JSON / datetime | `ships/badges` |

### 5.4 `update_achievements_data` · `data.py:508` — **WG: 1, UN-BATCHED** (`account/achievements`), gate 24 h
| Field | Type | Source |
|---|---|---|
| `achievements_json`, `achievements_updated_at` | JSON / datetime | `account/achievements` |
| `PlayerAchievementStat` rows: `achievement_code/slug/label/category/count/source_kind/refreshed_at` | (model rows) | derived |

> This is the call the **clan crawl makes for every member of every clan** — un-batched, 1 WCE
> each. Across ~120k clans it is the bulk of the entire WG budget and serves no battle-history need.

### 5.5 `incremental_ranked_data` · `data.py:4179` — **WG: 2 + N seasons**, gate 24 h
`seasons/shipstats` is **per-season, un-batched** — the most expensive per-player shape we run.

| Field | Type | Source |
|---|---|---|
| `ranked_json`, `ranked_updated_at` | JSON / datetime | `seasons/accountinfo` + `seasons/shipstats` |

### 5.6 `enrich_player_data` — **the bulk-optimized template** · `enrich_player_data.py`
Candidate filter: `enrichment_status=PENDING`, `pvp_battles ≥ 500`, `pvp_ratio ≥ 48%`,
last battle ≤ 365 d. Batch 500. **WG per 500-batch: ~10 bulk** (5× `ships/stats` + 5×
`seasons/accountinfo`, 100 ids each) **+ 1× `seasons/shipstats` per ranked player.**

| Field | Type | Source |
|---|---|---|
| `battles_json`, `tiers_json`, `type_json`, `randoms_json` (+ `_updated_at`) | JSON / datetime | **bulk** `ships/stats` |
| `ranked_json`, `ranked_updated_at` | JSON / datetime | **bulk** `seasons/accountinfo` + per-player `seasons/shipstats` |
| `enrichment_status` | char | state machine |
| `PlayerExplorerSummary.*` (via `refresh_player_explorer_summary`) | — | derived, 0 WG |

### 5.7 `PlayerExplorerSummary` · `models.py:181` — derived rollup, **0 WG**
Populated by `refresh_player_explorer_summary` (battles/wins last-29d, activity trend, player_score,
ship/tier spread, badge counts, ranked summary, `kill_ratio`),
`recompute_efficiency_rank_snapshot` (SQL window functions → percentile/tier/icon), and
`fetch_player_clan_battle_seasons` (CB fields). No WG calls — reads already-fetched JSON.

---

## 6. The coverage gap, quantified

| Metric | Value | Note |
|---|---|---|
| Active-7d players (broad target pool) | **254,908** | "played in last 7 days" — *not* "needs an observation today" |
| Distinct players observed, last 24h | **50,570** | every capture costs WG budget |
| Distinct players with a **real** battle captured, last 24h | **26,129** | `BattleEvent.battles_delta > 0` |
| **Productive capture rate** | **51.7%** | ~half our observations caught no battle |
| Daily-active denominator (proxy) | ~46k (≤24h) – ~62k (≤48h) | circular & undercounted — only known via observation |
| WCE/day to observe a ~50k daily-active set **per-player** (floor style, ×2) | **~100,000** | scales toward ~510k if we chase the full 255k pool |
| WCE/day to observe the same set **bulk** (enrichment style, ÷100 ×2) | **~1,000–5,100** | **~100× cheaper** |
| Current dominant consumer serving **none** of this | clan crawl per-member deep-fetch | up to ~345k WCE/day, and it pre-empts capture |

Two problems compound: (a) the clan crawl spends the largest share of the budget on per-member
achievements nobody is waiting on, *and* defers the capture work while it runs; (b) when we do
capture, we do it one player at a time, so ~half the budget is spent re-confirming players who
didn't battle. A bulk capture path — which **already exists** in enrichment — makes the
productive/unproductive ratio irrelevant: observing everyone becomes cheap enough that wasted
zero-delta snapshots no longer cost anything worth optimizing.

---

## 7. Optimization recommendations (ranked by leverage)

### R1 — Bulk-batch the observation capture *(highest leverage; ~100× WCE reduction)*
Replace the floor's per-player `record_observation_and_diff` (2–3 WCE/player) with a bulk capture
that mirrors enrichment: `_bulk_fetch_ship_stats` + bulk `account/info` (both already exist and run
in prod at 100 ids/call), then feed the payloads into the existing
`record_observation_from_payloads` diff. **Result:** covering all 254,908 active-7d players daily
drops from ~510k WCE/day to ~5k WCE/day. This alone turns the goal from "unaffordable" to "trivially
affordable," and it makes the 51.7% productive rate stop mattering (zero-delta snapshots cost
~nothing in bulk). It also tightens **daily** attribution — sparse capture mis-buckets which day a
battle lands on (`detected_at` incident), and near-daily observation is the fix. **Caveats:** (1)
the diff stays per-player and DB-bound — the win is purely on the WG axis (the binding one);
(2) **payload-parity check required before swapping** — the floor stores the raw `ships_stats_json`
that the Phase-7 deltas read, so confirm the bulk `ships/stats` response at 100 ids carries the
same per-ship gunnery/torpedo/spotting block (neither path sets a `fields` filter, so they should
match) and that the 100-id response size is acceptable. Ranked `seasons/shipstats` stays per-player
(not bulk-capable) — capture it separately and only for known-ranked players. Frame R1 as
"bulk-capable, pending payload-parity validation," not a free drop-in.

### R2 — Run the clan crawl in `core_only=True` *(frees the dominant consumer)*
The per-member `account/achievements` (un-batched) + efficiency fetch is >90% of clan-crawl WG
spend and is redundant with the player crawlers. `core_only=True` preserves discovery, rosters, and
the best-clans leaderboard (its aggregates come from the `account/info` batch, not
efficiency/achievements). Deep per-member stats then flow from enrichment/refresh on a priority
basis. **Result:** frees a large fraction of the budget for R1, and removes the crawl-lock-starves-
floor failure mode. (See the prior clan-crawler analysis for the full safety argument.)

### R3 — Raise the floor limit once capture is bulk
`BATTLE_OBSERVATION_FLOOR_LIMIT=3000`/cycle exists because per-player capture is expensive. After
R1, the per-cycle WCE cost of the floor collapses, so the limit can rise toward the full active-7d
set (or the floor can simply sweep all stale active-7d players each cycle).

### R4 — Land the global WG token-bucket
With R1+R2 freeing headroom, implement the designed Redis token-bucket
(`runbook-wg-rate-limiter-token-bucket-2026-06-05.md`) at `api/client.py` so capture, crawl and
request-driven traffic share one ceiling deterministically instead of colliding into 407s.

### R5 — Leave dormant the things that should stay dormant
Period rollup (540s timeout) and reconcile stay off until the period rebuild is moved DB-side.
No action — listed so it isn't mistaken for a gap.

---

## 8. Sources

Production config: `server/.env.cloud`, `server/deploy/deploy_to_droplet.sh`,
`server/deploy/bootstrap_droplet.sh`. Code: `server/warships/{signals,tasks,data,clan_crawl,
incremental_battles,models}.py`, `management/commands/{enrich_player_data,incremental_player_refresh,
incremental_ranked_data,ensure_daily_battle_observations}.py`. Live volume sampled from the
production DB 2026-06-06. Related: clan-crawler core_only analysis (this session),
`runbook-wg-rate-limiter-token-bucket-2026-06-05.md`,
`runbook-battle-history-rollup-durability-2026-06-06.md`.

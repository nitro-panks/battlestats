# Runbook — Hot-Players Engagement Capture Queue (2026-06-10)

**Status:** PLAN / design proposal. No code written yet. This runbook is the spec; a
follow-up implementation tranche builds it.

## Why

We guarantee fresh, granular per-day battle history for players we judge *important*
by their own play activity and skill — `ensure_daily_battle_observations_task` (the
observation floor) and `snapshot_active_players_task` both select on **active-7d**
(`last_battle_date` within 7 days), and the enrichment pool adds a **skill** gate
(`pvp_ratio >= ~48%`, `pvp_battles >= 500`). None of those gates know anything about
who the **audience** actually visits.

The ask: let *durable visitor interest* — not the player's own activity or skill — also
qualify a player for guaranteed daily capture. The motivating case is a 40%-WR player
that a fan (or the player themselves) visits repeatedly: we want a gap-free day-over-day
record for them, **including the days they don't log in**, so when they do come back the
history is continuous and the re-engagement is caught immediately.

We already record the raw signal — `EntityVisitEvent` / `EntityVisitDaily`
(`server/warships/visit_analytics.py`, `models.py:302-382`) — per player, per realm,
per day, bot-filtered at ingest, with a 30-min dedupe window. We just don't act on it
for capture. This builds the loop: **engagement → hot queue → daily capture → eviction
when interest fades.**

## The insight that makes this cheap

**The observation floor already polls every active-7d player within
`BATTLE_OBSERVATION_FLOOR_HOURS` (8h).** So the *marginal* work of a hot queue is **only
the hot players who have dropped out of the active set** — the inactive ones the floor
no longer touches. For hot players who are *also* active-7d, the floor already captures
them; the hot sweep must **skip-if-fresh** (reuse the floor's staleness gate) and do
zero redundant WG work.

Marginal cost ≈ `|hot players who are not active-7d|` × (1–2 WG calls/day). With a hot
set capped at a few hundred, that is a few hundred WG calls/day — negligible against the
~1.2K/day the snapshot engine already spends. This is also the load-bearing reason to
**keep the hot sweep a separate task, not prepend IDs into the floor** (see Design
decision 1).

## Pin the artifact: what "daily battle history" means here

"Granular daily battle history" spans two independent tables, and *both* gate on
active-7d today, so the hot queue must override both:

| Artifact | Written by | Has content on a no-play day? | Gates on active-7d today? |
|----------|-----------|-------------------------------|---------------------------|
| `BattleObservation` (+ `BattleEvent` deltas) — granular per-battle capture | `record_observation_and_diff(player_id, realm)` (`incremental_battles.py:1794`) | No new `BattleEvent`s (no deltas), but the observation row keeps the diff baseline live so the *next* play session is caught in full | Yes (floor) |
| `Snapshot` — daily cumulative summary (powers day-over-day charts) | `snapshot_active_players` cmd → `update_snapshot_data(refresh_player=False)` | A "no-change" row keeps the day-over-day series **gap-free** | Yes (snapshot engine) |

**Verified:** `record_observation_and_diff` writes a `BattleObservation` only — it does
**not** write a `Snapshot` row. So "catch the days they don't come" decomposes into:

1. **Keep them in the polled set** so re-engagement is captured the moment they return →
   needs the **observation** path.
2. **Keep the summary series gap-free** on no-play days → needs the **snapshot** path.

**The hot sweep therefore runs both paths per hot player**: `record_observation_and_diff`
(observation/baseline) **and** `update_snapshot_data(realm=realm, refresh_player=False)`
(gap-free daily summary row). Both already exist and are in production use; the hot sweep
just calls them for a candidate set chosen by engagement instead of activity.

## Differentiating passing vs sustained interest (the heuristic)

This is the analytical core. We must separate a one-time spike (a Reddit/Discord link
that sends a crowd once) from genuine return interest.

**Primary discriminator = recurrence across days, NOT total views.** A single viral day
and a player visited a little on five separate days can have identical summed view
counts — but only the second is *sustained*. The cheap, correct query is a
`GROUP BY entity_id` over `EntityVisitDaily` counting **distinct days with at least one
deduped view** in a trailing window:

```
active_days   = COUNT(DISTINCT date) WHERE views_deduped >= 1   over trailing W days
recency_days  = today - MAX(date with a view)
sessions      = SUM(unique_sessions) over the window   (breadth/realness tiebreak)
views_deduped = SUM(views_deduped)   over the window   (intensity tiebreak)
```

> **Do not use `get_top_entities()` for promotion.** It ranks by *summed* views and
> scores one spike identically to sustained return-visiting — the exact line we're trying
> to draw. It stays the right tool for cache warming; promotion uses the active-days
> `GROUP BY` above.

**Do NOT gate on visitor breadth.** The user's own example is *one* returning person.
The visitor cookie is a 1-year cookie, so a single devoted fan is `unique_visitors = 1`
but `active_days` = many and `unique_sessions` = many. Gating on `unique_visitors >= 2`
would reject exactly the case we care about. Floor on **active_days + recency +
unique_sessions**; bots are already UA-filtered at ingest so we don't re-filter here.

**Promotion rule (enter hot queue):**
`active_days >= HOT_PROMOTE_MIN_ACTIVE_DAYS` (default 3 over W=14)
AND `recency_days <= HOT_PROMOTE_MAX_RECENCY_DAYS` (default 3)
AND `unique_sessions >= HOT_PROMOTE_MIN_SESSIONS` (default 2, anti-single-reload).

**Eviction rule (leave hot queue) — hysteresis to prevent flapping:**
evict when `recency_days > HOT_EVICT_INACTIVITY_DAYS` (default 14, no views at all)
OR `active_days < HOT_EVICT_MIN_ACTIVE_DAYS` (default 2 over W) for a hot member.
Promote at ≥3, evict below 2 → a player hovering at 2–3 active-days/14 stays put instead
of churning in and out daily.

**Cap:** keep at most `HOT_PLAYERS_MAX` (default 500) per realm, ranked by a hotness
score (`active_days` primary, `unique_sessions` then `views_deduped` as tiebreaks) so the
set is bounded and the marginal WG cost stays predictable (doctrine: no unbounded
fan-out).

**Honest gap — "clicks on insight tabs" is not captured today.** The visit pipeline emits
**one** event per detail-page load (`trackEntityDetailView` in
`client/app/lib/visitAnalytics.ts`); chart/insight-tab fetches and autocomplete hits emit
**nothing**. So "are they clicking the insight tabs?" can't be answered with current data.
v1 uses page-load recurrence (active-days), which is sufficient. A **Phase 2** depth
signal is cheap and additive — see Phasing.

## Design

### Data model — `HotPlayer` (new table)

A durable membership + audit table (not Redis, not an env var) so promotion/eviction is
queryable, explainable, and survives restarts.

| Field | Purpose |
|-------|---------|
| `player` (FK Player) / `realm` | membership key |
| `promoted_at` | when it entered the queue |
| `last_engaged_at` | most-recent view (drives eviction) |
| `active_days_window` | active-days at last evaluation (audit/score) |
| `unique_sessions_window`, `views_deduped_window` | tiebreak/audit |
| `hot_score` | ranking value used for the `HOT_PLAYERS_MAX` cap |
| `source` | `engagement` (auto) vs `pinned` (manual override, see below) |
| `last_observed_at`, `last_snapshotted_at` | capture bookkeeping for skip-if-fresh |

Unique on `(player, realm)`. This is **distinct from** the existing `HOT_ENTITY_*` /
`_get_hot_player_ids()` path (`data.py:4818`), which ranks hot entities to warm **read
caches** — that's *serving*, this is *capture*. Naming it `HotPlayer` / "engagement
capture queue" keeps the two from colliding conceptually. (A manual `source='pinned'`
row also gives us a durable replacement for `HOT_ENTITY_PINNED_PLAYER_NAMES` / the old
`BATTLE_TRACKING_PLAYER_NAMES` PoC.)

### Task 1 — `maintain_hot_players_task` (DB-only, daily, the brain)

Mirrors `enrichment_pool_maintenance_task`: pure DB, no WG calls, coexists with crawls.

- Per realm, compute the active-days `GROUP BY` over `EntityVisitDaily` for the trailing
  window.
- **Promote** new players clearing the promotion rule; **evict** members hitting the
  eviction rule; **re-score** survivors and trim to `HOT_PLAYERS_MAX`.
- Log every promotion/eviction with the deciding numbers (audit trail).
- Command: `manage.py maintain_hot_players --realm na [--dry-run]` (dry-run sizes the
  promote/evict deltas without writing — same ergonomics as `snapshot_active_players
  --dry-run`).
- Kill switch `HOT_PLAYERS_ENABLED` (task no-ops at 0).

### Task 2 — `capture_hot_player_observations_task` (the hands)

Sweeps the `HotPlayer` set and guarantees the two daily artifacts, reusing proven
entrypoints (same pattern as `dispatch_tracked_player_polls_task` and the floor):

```
for player in HotPlayer(realm):
    if last_observed_at fresh within HOT_OBSERVE_FLOOR_HOURS:   # skip-if-fresh
        skip                                                    # floor already got them
    else:
        record_observation_and_diff(player_id, realm)           # baseline + deltas
    if no Snapshot row for today:
        update_snapshot_data(player_id, realm, refresh_player=False)   # gap-free summary
```

- **Skip-if-fresh** against `BattleObservation.observed_at` (reuse the floor's staleness
  notion) so active hot players already covered by the floor cost nothing.
- Bounded by `HOT_PLAYERS_MAX`; routes to the **`background`** queue (matches snapshot /
  enrichment / floor). Single-flight per realm. **Coexists with crawls** (no deferral) —
  the whole point is guaranteed coverage.
- Hidden accounts (`is_hidden`) return nothing from WG → the entrypoint already
  short-circuits `skipped/wg-fetch-failed-or-hidden`; we record the skip and move on (no
  retry storm).

### Scheduling — per-realm striped, `signals.py`

Register both tasks in the `@receiver(post_migrate)` block using
`_realm_crontab_for_cycle(realm, cycle_minutes, base_minute=...)` exactly like
`observation-floor-{realm}` / `snapshot-active-players-{realm}`:

- `hot-players-maintain-{realm}` — once daily, DB-only, striped after the visit-daily
  rollup has settled (pick a base_minute in the 08:00–09:00 UTC maintenance band already
  used by enrichment pool maintenance so the analytical load clusters).
- `hot-players-capture-{realm}` — daily (or twice daily) on the `background` queue,
  striped via `REALM_INTERVAL_OFFSETS` so realms don't overlap.

`enabled` gating: capture is a crawler-class WG consumer → gate on
`ENABLE_CRAWLER_SCHEDULES` like the floor. Maintenance is DB-only → always enabled like
the snapshot/enrichment-maintenance families (still respects `HOT_PLAYERS_ENABLED`).

## Design decisions (explicitly weighing the alternatives the user raised)

1. **Separate capture task vs prepend hot IDs into the observation floor / snapshot
   engine.** → **Separate.** Two reasons: (a) the candidate sets differ — the floor's
   whole selection *is* the activity gate we're overriding, so hot IDs can't ride its
   query; and (b) the floor runs under a per-run `limit` (default 3000) and prepending
   risks hot players being truncated on a busy realm. A separate, capped, skip-if-fresh
   sweep is both safer and — thanks to skip-if-fresh — barely more expensive than
   prepending would have been.
2. **`HotPlayer` table vs Redis set vs env var.** → **Table.** Durable across restarts,
   queryable for the ops health check, carries the audit fields that make
   promotion/eviction explainable, and gives manual `pinned` overrides a home.
3. **Recurrence (active-days) vs intensity (summed views) as the promotion metric.** →
   **Recurrence.** It's the only signal that separates passing from sustained, and it's a
   one-line `GROUP BY`. Intensity is a tiebreak, not a gate.
4. **No visitor-breadth gate.** A single returning human must qualify (the user's example).

## Env knobs (proposed)

| Var | Default | Meaning |
|-----|---------|---------|
| `HOT_PLAYERS_ENABLED` | `1` | Master kill switch (both tasks no-op at 0). |
| `HOT_PLAYERS_WINDOW_DAYS` | `14` | Trailing engagement window `W`. |
| `HOT_PROMOTE_MIN_ACTIVE_DAYS` | `3` | Distinct deduped-view days in `W` to promote. |
| `HOT_PROMOTE_MAX_RECENCY_DAYS` | `3` | Must have been viewed within N days to promote. |
| `HOT_PROMOTE_MIN_SESSIONS` | `2` | Min `unique_sessions` over `W` (anti single-reload). |
| `HOT_EVICT_INACTIVITY_DAYS` | `14` | No views for N days → evict. |
| `HOT_EVICT_MIN_ACTIVE_DAYS` | `2` | Active-days below this (in `W`) → evict (hysteresis). |
| `HOT_PLAYERS_MAX` | `500` | Per-realm cap on hot-set size (bounds WG cost). |
| `HOT_OBSERVE_FLOOR_HOURS` | `20` | Skip-if-fresh: skip observation if one is newer than this. |
| `HOT_PLAYERS_CAPTURE_DELAY` | `0.5` | WG pacing between hot captures (crawl-coexist value). |

## Observability

- A health command in the `check_*`/`enrichment-status` family —
  `manage.py hot_players_status` (or a skill) — reporting per realm: hot-set size, today's
  promotions/evictions, oldest `last_engaged_at`, count of hot players who are *not*
  active-7d (the marginal-cost set), and capture coverage today (how many got an
  observation + a snapshot).
- Structured promote/evict logs with the deciding `active_days`/`recency`/`sessions`.

## Phasing

- **v1 (this tranche):** `HotPlayer` model + migration, the two tasks + their commands,
  signals registration, env knobs, the status command, tests. Uses page-load recurrence
  only — no client changes.
- **Phase 2 (optional depth signal):** add an `event_type` (`detail_view` |
  `insight_tab` | `chart_open`) to the existing `/api/analytics/entity-view` ingest +
  `EntityVisitEvent`, and fire it from the tab/chart components. Lets the heuristic weight
  *depth* of engagement ("did they open the insights?") on top of recurrence. Additive;
  not required for v1 and explicitly out of scope here.

## Rollback

`HOT_PLAYERS_ENABLED=0` (both tasks no-op immediately) or disable the
`hot-players-{maintain,capture}-{realm}` periodic tasks. The `HotPlayer` table is additive
and harmless if left in place; capture reuses existing write paths so there is nothing
bespoke to unwind. Dropping the table (if ever desired) is the only schema action and is
reversible by re-migrating.

## Open questions for implementation

- **Window vs cap interaction:** if more than `HOT_PLAYERS_MAX` players clear the
  promotion floor on a big realm, confirm the score ranking degrades gracefully (it does —
  trim by `hot_score`) and log how many qualified-but-trimmed.
- **Re-promotion cooldown:** should an evicted player face a short cooldown before
  re-promotion to damp edge flapping beyond the active-days hysteresis? Probably not
  needed given hysteresis; revisit if logs show churn.
- **Cross-realm identity:** `EntityVisitDaily` is realm-scoped and so is `Player`; keep
  the whole pipeline per-realm (no cross-realm merge) — consistent with every other
  per-realm family.

## Pre-implementation checklist (doctrine)

- [ ] Migration for `HotPlayer`; update the Data-models section of `CLAUDE.md` and
  `models.py` docstrings.
- [ ] Tests: promotion/eviction heuristic (spike vs sustained vs single-fan fixtures),
  hysteresis no-flap, skip-if-fresh, cap/trim, kill switch, per-realm isolation.
- [ ] `ops-env-reference.md` — add the `HOT_PLAYERS_*` knobs.
- [ ] Mention the new tasks under "Celery queues" / scheduling in `CLAUDE.md` (slim).
- [ ] `doc_registry.json` entry for this runbook; archive on completion if it converts to
  a one-shot rollout note.

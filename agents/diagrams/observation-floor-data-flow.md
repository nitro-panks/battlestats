# Observation-Floor & Daily-Active Capture Data Flow

How the **battle-observation floor** and the **daily-active `Snapshot` engine** keep
battle history flowing for active players. They are two *parallel coverage guarantees*
with **distinct write targets** that run on **different workers** (current 2026-06-20):

- **Observation floor** — runs on the dedicated **`floor`** queue / `battlestats-celery-floor`
  worker (`-c 1`; per `settings.CELERY_TASK_ROUTES`; **not** `default` or `background`). No active
  player goes longer than `BATTLE_OBSERVATION_FLOOR_HOURS` (live: 8) without a fresh
  `BattleObservation`. Writes `BattleObservation` (+ a change-gated `BattleEvent`), advances
  `Player.last_battle_date`, and — since 2026-06-14 — can also rebuild `battles_json` /
  `battles_updated_at` for the captured player from the same `ships/stats` response
  (`FLOOR_REFRESH_BATTLES_JSON_ENABLED`) — **currently deferred / off (`=0`)** during the backlog
  catch-up phase. It does **not** write `Snapshot`.
- **Daily-active snapshot engine** — runs on the **`background`** (`-c 3`) worker. Every
  active, visible player gets one gap-free daily `Snapshot` row. Writes `Snapshot` (the
  day-over-day series). This engine exists *because* the floor never writes `Snapshot`.

This doc is narrower and deeper than `queue-data-flow.md` — see that for the full
five-queue layout. **The two engines run on separate workers** (floor on the dedicated `floor`
worker, snapshot on `background`), so they do **not** contend for the same pool — an earlier
version of this doc wrongly placed the floor on `background`, then on `default`; the floor now
has its own `floor` worker, and Level 3 below is corrected.

Sources: `runbook-bulk-battle-observation-capture-2026-06-06.md`,
`runbook-daily-active-snapshots-2026-06-09.md`,
`runbook-floor-throughput-tuning-2026-06-13.md`, and `signals.py` / `tasks.py` /
`incremental_battles.py` / `ensure_daily_battle_observations.py`.

---

## Level 1 — Two parallel guarantees (overview)

Beat fires both task families per realm (striped, so at most one realm is mid-cycle).
The floor selects active-7d players whose last `BattleObservation` is past the freshness
floor; the snapshot engine selects active-7d players who lack today's `Snapshot`. Both
feed the **zero-WG persistence core** (`record_observation_from_payloads` /
`update_snapshot_data`) after fetching from Wargaming.

```mermaid
flowchart LR
    WG["Wargaming API (upstream)"]
    BEAT["Celery Beat (per-realm striped)"]

    subgraph FW["floor worker (-c 1): battlestats-celery-floor — recency-first, random-only, Beat-only"]
        FLOOR["ensure_daily_battle_observations_task<br/>(per-realm, 'observation-floor-&lt;realm&gt;')"]
    end
    subgraph BG["background worker (-c 3): snapshot + enrichment/hot/warmers"]
        SNAP["snapshot_active_players_task<br/>(per-realm, 'snapshot-active-players-&lt;realm&gt;')"]
    end

    subgraph SEL["Candidate selection (Postgres, indexed)"]
        FC["FLOOR _candidates:<br/>active-7d ∧ is_hidden=False ∧<br/>latest BattleObservation.observed_at NULL or &gt; floor"]
        SC["SNAPSHOT candidates:<br/>active-7d ∧ visible ∧<br/>NO Snapshot row for today (UTC)"]
    end

    CORE["record_observation_from_payloads<br/>(ZERO-WG persistence + pure-diff)"]
    USD["update_snapshot_data(refresh_player=False)<br/>(pure-DB)"]

    PG[("PostgreSQL")]
    OBS[("BattleObservation (raw ships/stats JSON)")]
    EVT[("BattleEvent (gated on battles_delta&gt;0)")]
    SNAPROW[("Snapshot (one gap-free row / player / UTC day)")]
    LBD["Player.last_battle_date (freshness side-effect)"]

    BEAT -->|"periodic enqueue"| FLOOR
    BEAT -->|"periodic enqueue"| SNAP

    FLOOR --> FC
    SNAP --> SC

    FC -->|"bulk account/info + per-player ships/stats"| WG
    SC -->|"bulk account/info (100 ids/call)"| WG

    FC --> CORE
    SC --> USD

    CORE -->|"write raw observation"| OBS
    CORE -->|"compute_battle_events (if battles_delta&gt;0)"| EVT
    CORE -->|"advance"| LBD
    USD --> SNAPROW

    OBS --> PG
    EVT --> PG
    SNAPROW --> PG
    LBD --> PG

    classDef ext fill:#e8f0fe,stroke:#4285f4,color:#202124;
    classDef store fill:#fff3e0,stroke:#f9a825,color:#202124;
    class WG ext;
    class PG,OBS,EVT,SNAPROW store;
```

**The freshness clock the floor selects on** is the latest `BattleObservation.observed_at`
(the `_candidates` `stale_hours` test) — *not* `battles_updated_at`. `battles_updated_at`
tracks the displayed `battles_json` chart refresh; since 2026-06-14 the floor **can also**
advance it for active players from the same `ships/stats` response
(`FLOOR_REFRESH_BATTLES_JSON_ENABLED`) alongside incremental refresh and lazy-refresh-on-view —
but that rebuild is **currently deferred / off (`=0`)** during the backlog catch-up phase.
(The hot-player Tier-3 freshness sweep that previously advanced it was retired 2026-06-15.)

### The reuse seam

`record_observation_from_payloads(player, *, player_data=None, ship_data,
ranked_ship_data=None, source=None)` (`incremental_battles.py:669`) makes **zero WG
calls**: it coerces the pre-fetched payloads into a snapshot, writes the
`BattleObservation`, finds the prior observation, computes `BattleEvent`s via the pure
`compute_battle_events` / `compute_ranked_battle_events` diff, updates
`PlayerDailyShipStats`, and invalidates caches inside its own `transaction.atomic` +
`on_commit`. Every fetch path — legacy per-player, the bulk floor, hot-player capture —
funnels through this one core, which is why they are parity-by-construction.

---

## Level 2 — Per-player capture decision (two distinct gates)

A player is processed in two stages with **two separate gates**. The *pre-fetch
change-gate* (`_gate_needs_ships`) decides whether to pay for the per-player
`ships/stats` call at all; the *post-fetch event gate* (`battles_delta>0`) decides
whether the resulting observation produces a `BattleEvent` or is a baseline-only
observation. Confusing the two is the easy mistake — they are different decisions at
different stages.

```mermaid
stateDiagram-v2
    [*] --> Candidate

    Candidate --> SkipFresh: latest observed_at within floor
    SkipFresh --> [*]: skip-if-fresh (no WG, no write)

    Candidate --> BulkAcct: stale (past floor)
    note right of BulkAcct
      Bulk account/info, 100 ids/call.
      Carries pvp.battles + last_battle_time.
    end note

    BulkAcct --> ChangeGate: _gate_needs_ships(acct, prior)

    state ChangeGate {
        [*] --> RandomKey: random sweep
        [*] --> RankedKey: ranked sweep
        RandomKey: key on pvp.battles delta
        RankedKey: key on last_battle_time advance
    }

    ChangeGate --> GatedSkip: has prior, no new battles since last obs
    GatedSkip --> [*]: gated_skipped (NO observation this tick)

    ChangeGate --> MissingOrHidden: account absent / hidden / no pvp
    MissingOrHidden --> [*]: skipped_missing (no obs)

    ChangeGate --> FetchShips: mover OR no prior (baseline)
    note right of FetchShips
      Per-player ships/stats — 1 call/player.
      WG ships/stats is single-account-only:
      it CANNOT bulk (n>=2 -> INVALID_ACCOUNT_ID).
    end note

    FetchShips --> Record: record_observation_from_payloads
    Record --> EventGate: prior obs exists?

    state EventGate {
        [*] --> HasDelta: battles_delta > 0
        [*] --> NoDelta: battles_delta == 0 / no prior
        HasDelta: write BattleObservation + BattleEvent
        NoDelta: write BattleObservation only (baseline)
    }

    EventGate --> [*]: advance Player.last_battle_date
```

### Why the two gates differ

- **Pre-fetch change-gate (`_gate_needs_ships`, `incremental_battles.py:940`)** — the
  cost-saver. `account/info` bulks cheaply (100 ids/call) and carries each player's
  `statistics.pvp.battles` + `last_battle_time`, so the floor can decide *before* paying
  for the expensive per-player `ships/stats` call whether anything moved:
  - **Random sweep keys on `pvp.battles`** vs the latest `BattleObservation.pvp_battles`.
  - **Ranked sweep keys on `last_battle_time`** — ranked-known players play randoms *and*
    ranked, and `last_battle_time` advances on *any* battle, so a `pvp.battles`-only check
    would miss ranked-only activity.
  - A non-mover is `gated_skipped` and gets **no observation this tick**. Measured ~51%
    of stale candidates skip here — the gate cuts floor `ships/stats` load roughly in half
    (the ~37% WG-load cut reported on rollout).
- **Post-fetch event gate (`battles_delta>0`)** — the correctness gate *inside*
  `record_observation_from_payloads`. Every reached player still gets a
  `BattleObservation` written, but a `BattleEvent` (the per-event delta row) is only
  produced when `compute_battle_events` finds `battles_delta>0`. A first-seen player or a
  no-play tick writes a **baseline observation** with no event.

### The asymmetric "bulk" fetch (refuted ~100× premise)

The fetch is **not** symmetric bulk. WG `ships/stats/` is **single-account-only** — it
rejects `n>=2` `account_id` values with `INVALID_ACCOUNT_ID` (confirmed by raw `curl`:
even the same valid id twice fails). So the path is:

- **bulk `account/info`** — 100 ids/call (~0.01 WG/player), the change-gate signal source.
- **per-player `ships/stats`** — 1 call/player, *unavoidable*, only for gate movers.

The original "~100× cheaper / daily-every-active-player" justification was **refuted on
prod**: enrichment's `_bulk_fetch_ship_stats` had always been silently falling back to
per-player. The real R1 saving is **~2× → ~1×** (drops the per-player `account/info`
call), and the goal is reachable not because of bulk ships but because the active-7d
population was a 3× overcount (~84k, not 255k). The change-gate then removes the wasted
~half of `ships/stats` calls on non-movers.

---

## Level 3 — Scheduling, throughput & contention

Both task families are **per-realm striped** via `REALM_INTERVAL_OFFSETS = {'na': 0,
'eu': 1, 'asia': 2}` so at most one realm is mid-cycle at a time, computed by
`_realm_crontab_for_cycle(realm, cycle_minutes, base_minute=...)` in `signals.py`. The
floor runs on a rolling `BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES` cycle (live: **180** →
8 slots/realm/day) and the freshness *guarantee* is `BATTLE_OBSERVATION_FLOOR_HOURS`
(live: 8). The snapshot engine runs every `SNAPSHOT_ACTIVE_INTERVAL_MINUTES` (default 30,
48 idempotent runs/day → convergence).

**Current 2026-06-20.** The floor runs on its **own dedicated `floor` worker**
(`battlestats-celery-floor`, `-c 1`); the snapshot engine, enrichment, hot-player capture, and
warmers run on the **separate `background`** worker, and dispatchers / lazy-refresh / watchdogs on
`default` — so the floor is **not** contended by any of those tenants (earlier versions wrongly placed
it on `background`, then `default`). They still share the 2-vCPU PG and the global WG budget, just not
a worker pool. The **binding constraint is that shared 2-vCPU managed PG** — and within it the
analytical **warmers** (best-clans / distributions / correlations) + large-row `warships_player`
updates, **not** the floor's own observation/event INSERTs (a minor cost) and **not** WG (the floor
draws only ~1.5–2.4 of the 9 req/s bucket). A standing managed-PG load monitor watches for sustained
saturation (`load15 > 2.3`).

```mermaid
sequenceDiagram
    participant BEAT as Celery Beat
    participant POOL as floor worker (-c 1) — battlestats-celery-floor
    participant LOCK as Redis (per-realm single-flight lock)
    participant WG as Wargaming API (global token-bucket ~2-3 of 10 req/s)
    participant PG as PostgreSQL (2-vCPU managed)

    Note over BEAT: per-realm striped — na/eu/asia never mid-cycle together

    BEAT->>POOL: observation-floor-na (every CYCLE_MINUTES)
    POOL->>LOCK: acquire daily_observation_floor:na:lock (3h TTL)
    Note over POOL: select _candidates up to BATTLE_OBSERVATION_FLOOR_LIMIT<br/>(live 12000) — bounds work per cycle (asia is pinned here)
    POOL->>WG: bulk account/info (gate) + per-player ships/stats (movers)
    alt 407 REQUEST_LIMIT_EXCEEDED
        WG-->>POOL: rate-limited
        Note over POOL: ABORT sweep, persist partial
    else ok
        POOL->>PG: BattleObservation (+ gated BattleEvent) + last_battle_date + battles_json
    end
    POOL->>LOCK: release lock
    Note over POOL: SELF_CHAIN_ENABLED=0 — self-chain is OFF (gated on DB headroom; the 2-vCPU PG is<br/>warmer-saturated). The floor is Beat-only: each realm fires once per CYCLE_MINUTES, no re-dispatch.

    Note over POOL,PG: separate `background` worker runs snapshot/enrichment/hot/warmers; `default` runs<br/>dispatchers/lazy-refresh/watchdogs — all share the 2-vCPU PG (warmers are the main hog) + WG budget,<br/>NOT the floor's worker pool.
```

### What actually bounds throughput (current 2026-06-20)

The binding constraint is the shared **2-vCPU managed PG**, and within it the analytical **warmers**
(best-clans / distributions / correlations) + large-row `warships_player` updates — **not** WG and
**not** the floor's own writes. Phase-0a instrumentation showed the floor healthy
(11 clean cycles/12h, `aborted=False`) but:

- **asia is permanently `FLOOR_LIMIT`-bound** (~12,000 candidates/cycle), ~90–95% `gated_skipped`
  non-movers. The change-gate skips a non-mover **without writing an observation**, so it stays
  observation-stale and `_candidates()` re-selects it every cycle — a permanent "non-mover wall"
  (≈ the 181k `stale_over_24h`). When the stale pool exceeds the cap, recent movers in the overflow
  wait a full cycle. **This corrects the earlier "NOT FLOOR_LIMIT" claim** — the cap *is* binding on
  asia (the prior reading conflated workers).
- **battles_json rebuild** was ~16–48% of cycle wall-time — now **deferred / off (`=0`)** during the
  backlog catch-up phase, so the per-mover `ships/stats` serial fetch is the remaining per-mover cost.
- **Not WG** (the floor draws only ~1.5–2.4 of the 9 req/s bucket), and **not worker-pool contention**
  (the floor has its own dedicated `floor` worker).

**The current mode (recency-first + dedicated worker, `-c 1`, Beat-only, 2026-06-20).**
The floor is **under-capacity**, not mis-tuned: the per-realm active pools are large (na 50k / eu 88k /
asia 61k active-7d; only ~10–23% fresh<8h). It runs on its **own `floor` worker (`-c 1`)** with
**recency-first** ordering (capacity to the likeliest movers) and is **Beat-only**
(`SELF_CHAIN_ENABLED=0`): each realm fires once per `CYCLE_MINUTES`, no continuous re-dispatch. A
`-c 2` + self-chain experiment was tried (2026-06-20) and **reverted** — it sustained-saturated the
shared 2-vCPU PG when an analytical-warmer cycle overlapped (`load15 > 2.3`, peaked `load1` 6.86).
The binding constraint is the shared 2-vCPU PG, **saturated by the analytical warmers** (the floor's
own writes are a minor cost); higher floor concurrency / self-chain are **gated on DB headroom**
(warmer optimization or a 2→4 vCPU resize). See `runbook-floor-throughput-tuning-2026-06-13.md`
(CURRENT STATE section).

### Crawl-coexist (no deferral)

Both the floor and the snapshot engine **coexist with the multi-day clan crawl** — they
do **not** defer (guaranteed coverage is the whole point). When the clan-crawl lock is
held, the floor task detects it (`cache.get(_clan_crawl_lock_key(realm))`) and *gentles
its pacing* instead of skipping: it swaps in `BATTLE_OBSERVATION_FLOOR_CRAWL_DELAY` (0.8)
/ `BATTLE_OBSERVATION_FLOOR_BULK_CRAWL_CHUNK_DELAY` (1.0) and an optional
`BATTLE_OBSERVATION_FLOOR_CRAWL_LIMIT`, so it stays under the shared ~10 req/s budget the
crawl is already drawing on. The crawl is *secondary contention*, never a hard skip.

### Interaction with the hot-player capture queue

`capture_hot_player_observations_task` is **skip-if-fresh against the floor** (its
`HOT_OBSERVE_FLOOR_HOURS` check), so hot players who are *also* active-7d are already
covered by the floor and cost nothing extra — the hot sweep's marginal work is only the
hot-but-inactive set the floor wouldn't reach. (Full hot-player loop: see the hot-players
drill-down in `queue-data-flow.md`.)

---

## Gating & flags (live vs default)

The bulk + change-gate + random-first path is **live in prod for all realms** and
**persisted in `deploy_to_droplet.sh`**, with the legacy per-player path kept intact
behind flags for instant rollback. Defaults below are the *code* defaults (legacy path);
the live prod values differ where noted.

| Flag / knob | Code default | Live prod | Role |
|---|---|---|---|
| `SNAPSHOT_ACTIVE_PLAYERS_ENABLED` | `1` | `1` | Master kill switch for the daily-snapshot engine |
| `BATTLE_OBSERVATION_FLOOR_LIMIT` | `3000` | `12000` | Candidates per floor cycle (bounds work/cycle) |
| `BATTLE_OBSERVATION_FLOOR_CYCLE_MINUTES` | `360` | `180` | Floor cycle (8 slots/realm/day) |
| `BATTLE_OBSERVATION_FLOOR_HOURS` | `8` | `8` | Freshness *guarantee* (max age of latest observation) |
| `BATTLE_OBSERVATION_FLOOR_BULK_ENABLED` | `0` | `1` | Bulk `account/info` path vs legacy per-player |
| `BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED` | `0` | `1` | Pre-fetch random change-gate (skip non-movers) |
| `BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED` | `0` | `1` | Pre-fetch ranked change-gate (`last_battle_time`) |
| `BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED` | `0` | `1` | Route current-season ranked only; Random > Ranked |
| `BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED` | `0` | `1` | Heavy ranked sweep once/day (primary slot) vs every slot — random-only otherwise |
| `BATTLE_OBSERVATION_FLOOR_SELF_CHAIN_ENABLED` | `0` | `0` | OFF (Beat-only). Continuous re-dispatch + `-c 2` was tried and reverted (sustained-saturated the 2-vCPU PG); gated on DB headroom |
| `FLOOR_REFRESH_BATTLES_JSON_ENABLED` | `1` | `0` (deferred) | Per-mover `battles_json` rebuild from the same `ships/stats` — deferred during backlog catch-up |
| `CELERY_FLOOR_CONCURRENCY` | — | `1` | Dedicated `floor` worker (`battlestats-celery-floor`) concurrency — `-c 2` reverted (warmer-saturated PG); gated on DB headroom |

### Notes

- The snapshot engine is **DB-light**: bulk `account/info` (~1 WG call / 100 players,
  ~1.2K calls/day for ~120K active) → `save_player(core_only=True)` →
  `update_snapshot_data(refresh_player=False)`. It deliberately does **not** rebuild
  `battles_json`.
- A daily drop in coverage (`cov/7d`) is usually **maturation, not regression** — the
  bulk floor front-loads never-observed players, then the change-gate makes the sweep
  *more selective* as coverage matures (`productive_rate` rises). Only call a regression
  when sustained ≥2-3 clean snapshots with `distinct_productive` down and `active_7d`
  flat. (`/observation` skill + bulk-capture runbook Benchmarks.)
- `cov/7d` is capped at the daily-active fraction (~40%, declining), not "toward 100%" —
  the `active_7d` denominator plateau is the forecast linchpin.

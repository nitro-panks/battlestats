# crawl

Reads the durable benchmark snapshots written nightly by the droplet cron
(`/opt/battlestats-server/shared/bin/snapshot_crawl_productivity.sh`, 04:35 UTC →
`/opt/battlestats-server/shared/benchmarks/crawl-productivity/YYYY-MM-DD_HHMMZ.json`)
and renders a **day-over-day productivity readout** of the multi-day **clan
crawl**: catalog-refresh coverage, implied full-pass cadence, net discovery, and
liveness.

**Scope — read this before interpreting anything.** These snapshots measure the
**clan crawl** only: `crawl_all_clans_task` → `warships.clan_crawl`, the
single-slot (`crawls` queue, `-c 1`) sweep that walks every clan per realm, fetches
its members, and writes `Clan` / `Player` discovery rows + cached clan aggregates.
Post-R2 the crawl is `core_only` (discovery + clan aggregates; the expensive
per-player enrichment was gutted as redundant). This benchmark is therefore
**blind to the enrichment pipeline** (who gets `battles_json` built — gated by
`ENRICH_*`) and to the **battle-observation floor** (`BattleObservation` capture).
For those, use `enrichment-status` (live crawler health) or `/observation`
(observation-floor coverage). Don't cross-attribute movement between them.

## When to invoke

- "/crawl", "crawl status", "crawl readout", "crawl productivity"
- "how's the clan crawl doing", "how productive have the crawlers been", "are we
  refreshing the clan catalog", "clan-crawl benchmark"
- After a crawl-config change (e.g. `CLAN_CRAWL_CORE_ONLY`, `ENABLE_CRAWLER_SCHEDULES`,
  crawl delay) to confirm coverage moved.

Do **not** invoke for: enrichment-pool / `battles_json` progress (`enrichment-status`),
battle-observation coverage (`/observation`), or live worker/queue health
(`enrichment-status` / `healthcheck.sh`). This skill reads *yesterday's snapshot*,
not live state.

## Procedure

### 1. Pull recent snapshots

One SSH call; the files are ~1 KB each, so pull the last two weeks:

```bash
ssh root@battlestats.online '
DIR=/opt/battlestats-server/shared/benchmarks/crawl-productivity
echo "AVAILABLE=$(ls -1 "$DIR"/*.json 2>/dev/null | wc -l)"
for f in $(ls -1t "$DIR"/*.json 2>/dev/null | head -14); do
  echo "===== $(basename "$f") ====="
  cat "$f"
done
'
```

If SSH fails or `AVAILABLE=0`, surface the error verbatim and stop. If
`AVAILABLE=1`, report the latest snapshot but say plainly there is no comparison
point yet. (The cron was installed 2026-06-14, so early on expect a short history.)

If the user wants **right now** instead of yesterday's snapshot, run the command
live on the droplet — it is read-only:
`/opt/battlestats-server/shared/bin/snapshot_crawl_productivity.sh` (writes a
snapshot), or `cd /opt/battlestats-server/current/server && ../../venv/bin/python
manage.py benchmark_crawl_productivity` for the human-readable table without
writing a file.

### 2. Select comparison points BY `captured_at`, never by file order

The cron fires daily at 04:35 UTC, but off-cycle manual runs can exist. The
coverage metric is over a **trailing 24h window**, so two snapshots only a few
hours apart share most of the same window — diffing them is noise. Parse
`captured_at` and pick:

- **L** = latest snapshot ("now").
- **D-1** = the snapshot whose `captured_at` is closest to `L − 24h` (accept
  ~20–28h back; prefer the 04:35Z daily). Day-over-day baseline.
- **D-7** = the snapshot closest to `L − 7d`, if one exists, for the weekly trend.

The net-discovery numbers (`Δ players_total`, `Δ clans_total`) require a **clean
~24h gap** — only compute them against a D-1 that is genuinely ~1 day back.

### 3. Compute and interpret

For **totals** and **each realm** (na / eu / asia), report L and Δ vs D-1:

| field | meaning |
|---|---|
| `clans_fetched_24h` | **headline numerator** — clans whose `last_fetch` landed in the window (crawl-attributable) |
| `clans_total` | clan catalog size (coverage denominator; Δ vs D-1 = *net* new clans) |
| `clan_coverage_pct` | **headline** = `clans_fetched_24h / clans_total` — share of the catalog refreshed in 24h |
| `implied_full_pass_days` | `clans_total / clans_fetched_24h` (×window/24) — projected full-catalog cadence |
| `clans_never_fetched` | clans discovered but never yet crawled (first-crawl backlog; should trend down) |
| `players_total` | cumulative player rows (Δ vs D-1 = *net* new players — discovery proxy) |
| `liveness` | point-in-time: `crawl_lock_held`, `heartbeat_age_s`, `pass_marker_age_s`, `pending` |
| `realms_crawling` | (totals) how many realms held the crawl lock at capture |

**Attribution honesty — label every metric by what it proves:**
- `clans_fetched_24h` / `clan_coverage_pct` are crawl-attributable (the crawl
  walks the whole catalog; clan-page refreshes touch only a handful). This is the
  real productivity signal.
- `Δ players_total` / `Δ clans_total` are **net** discovery (created − GDPR-deleted
  − other create paths). Call them "net new rows," not "discovered by the crawl."
  The schema has **no per-pass discovery timestamp**, so true discovery volume is
  not measurable here — say so if asked.
- Player `last_fetch` is deliberately **not** in the snapshot: it is written by
  enrichment/floor/visits too, so it is not crawl-specific. Don't reintroduce it
  as a crawl number.

**Liveness disambiguation — this is the regression killer.** Unlike a continuous
sweep, the clan crawl is **lumpy**: a full pass takes ~12–18h on the single-slot
`crawls` worker, is regularly interrupted by deploys (SIGTERM) and the soft time
limit, and resumes run-scoped. A low-coverage day is therefore usually a *pause or
inter-pass gap*, not a regression — and you can tell which:
- `crawl_lock_held=true` (fresh `heartbeat_age_s`) → actively crawling now.
- `crawl_lock_held=false` + a `pass_marker_age_s` present → mid-pass but idle
  between task invocations (normal for `-c 1`).
- coverage ~0 **and** no lock **and** no/old pass marker → crawl is paused/stalled.
  Cross-check `ENABLE_CRAWLER_SCHEDULES` and the `crawls` worker before calling it
  a regression. (History: a restart loop once held the lock 24/7 and starved the
  floor — see `project_crawl_lock_starves_floor`.)

### 4. Report

```
Clan-crawl productivity — battlestats.online
Latest: <L captured_at>   vs   <D-1 captured_at> (Δ24h)   [trend vs <D-7> over 7d]
Config: CORE_ONLY=<…> SCHEDULES=<…>

            clans_total   fetched24h    coverage   passETA   neverFetched   netΔplayers   liveness
  na        …             … (Δ…)        …%         …d        …              +…            …
  eu        …             … (Δ…)        …%         …d        …              +…            …
  asia      …             … (Δ…)        …%         …d        …              +…            …
  TOTAL     …             … (Δ…)        …%         …d        …              +…            <N> crawling

Read: <one line — coverage move + WHY (real throughput vs lumpy pause, per liveness), and net discovery>
```

**Verdict discipline.** Day-to-day coverage varies widely at fixed config (pass
boundaries, deploy interrupts, per-realm `crawls`-queue serialization mean only one
realm advances at a time). A single low day is almost always the lumpy schedule,
**not** a regression — confirm with liveness first. Only call a real regression
when coverage is depressed across **≥2–3 clean daily snapshots** with the crawl
*live* (lock cycling, schedules on) and `clans_never_fetched` rising. When within
the noise band, say "within noise — need N more clean days." Frame deliberate
config changes as expected transitions to re-baseline against.

## Scope and limits

- **Read-only.** SSHes, cats JSON, interprets. Never writes the DB, never restarts
  services. (Running the snapshot script live writes a JSON file only — no DB writes.)
- Reports the most recent **nightly snapshot**, not live state, unless you
  explicitly run the command live per step 1.
- **Clan crawl only.** Not enrichment (`enrichment-status`), not the
  observation floor (`/observation`).
- Background: management command `warships/management/commands/benchmark_crawl_productivity.py`
  (docstring documents every metric + attribution caveat).

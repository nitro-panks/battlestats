# Work item: table-shape audit at terminal state — old assumptions vs what we built

_Created: 2026-09-20_
_Author role: DBA / data architecture_
_Context: the product has reached its intended end state — a 90-day rolling window on both surfaces, 105-day retention, no further window moves planned. The F-series (`runbook-db-table-audit-2026-07-19.md`) and G-series (`data-capture-utility-audit-2026-08-05.md`) audited a system still in motion. This H-series audits the settled one, and its specific brief is to test the **design assumptions** each large table was built on against the **measured shape** of the data it now holds._
_Objective function: battlestats is art, not a commercial product; the job is to run it efficiently and as inexpensively as possible, on a DB volume that will never autoscale. Findings are ranked by bytes and writes removed, not by whether they move a projected date._
_Method: read-only throughout. Every query ran under `statement_timeout='55s'` and `default_transaction_read_only=on`; heavy attribution by `TABLESAMPLE`, never a full scan. Three queries timed out and were re-asked more cheaply rather than given a longer budget; two of those timeouts are themselves findings._

## Evidence classes

| Tag | Meaning |
|---|---|
| **M** | Measured directly today (catalog stats, `pg_stat_*`, or a query) |
| **S** | Sampled and extrapolated; sample size stated |
| **D** | Derived from measurements |
| **C** | Established by reading the code, with `file:line` |

## The tables in scope (M, 2026-09-20)

| Table | Total | Heap | Index | TOAST | Rows | Heap B/row |
|---|---|---|---|---|---|---|
| `warships_battleobservation` | **25.79 GB** | 0.80 | 0.72 | **24.27** | 4.09 M | 195 |
| `warships_player` | **12.35 GB** | 1.76 | 1.21 | 9.38 | 1.12 M | **1,576** |
| `warships_playerdailyshipstats` | 8.47 GB | 4.49 | 3.98 | 0 | 20.95 M | 215 |
| `warships_battleevent` | 7.68 GB | 4.51 | 3.17 | 0 | 22.65 M | 200 |
| `warships_snapshot` | 2.28 GB | 1.28 | 0.99 | 0 | 13.17 M | 98 |
| `warships_playerachievementstat` | 1.43 GB | 0.61 | 0.82 | 0 | 5.36 M | — see H6 |
| `warships_playerexplorersummary` | 0.33 GB | 0.23 | 0.11 | 0 | 0.83 M | 277 |

These seven are 99% of the 59.2 GB database. Volume: `disk_used_percent` **78.57%** (65.89 of 83.87 GB), autoscale off permanently by decision.

## TL;DR — six assumptions, tested

| # | The assumption the design rests on | What the data says | Verdict |
|---|---|---|---|
| H1 | The biggest table is 25.8 GB of data | **~9 GB is data; ~15 GB is air** left by the `keep=1` fix, and it will never refill | Assumption false — **largest single reclaim in the system** |
| H2 | `BattleEvent` is the raw layer; `PlayerDailyShipStats` is its *rollup* (G10: "correct design") | **1.081 events per rollup row.** The rollup compresses by 7.5% | Assumption false — two 8 GB tables hold nearly the same rows |
| H3 | The per-player baseline blob and `battles_json` are different things (G10) | **96.9%** of players holding `battles_json` also hold a live observation payload built from the *same WG response* in the *same function* | Duplicated; and 41% of `battles_json` is `Ship`-table metadata repeated per player |
| H4 | A `Player` row is a row of scalars with some JSON attached | **~73% of the hot row is cold JSON** carried along on every one of 31.5 M updates, 9.1% of them HOT | Row shape is the write-amplification G5 found |
| H5 | Twice-monthly pruning holds the window at 105 days | It holds it at **105-120**. File size is set by the peak, and the first peak has not happened yet | **Time-sensitive**: fixable before ~2026-10-02, not after |
| H6 | Stopping the achievements *table* left one useful copy (this session, Step 5) | `achievements_json` is in the serializer's **`exclude`** list. The remaining copy has no reader either | My error this morning; the whole stream serves nobody |

**If H1, H5, H6 and the index drops in H7 are done, the volume goes from 78.6% to roughly 60% with no change to any feature and no spend.** That retires the resize question (capacity runbook Step 3) outright.

## H1 — `battleobservation`: two thirds of the largest table is air

**Assumption.** The table is 25.79 GB, so that is what the diff baseline costs.

**Measured.** After the 2026-09-20 `keep=1` fix, 1% `TABLESAMPLE` (41,875 rows): **14.7%** of rows carry `ships_stats_json` (was 28.8% the day before), at **15 kB stored** each (73 kB as text, 163 ships). Live payload ≈ 601 K × 15 kB ≈ **9.0 GB** (S) inside a **24.27 GB** TOAST relation (M). Roughly **15 GB is reusable free space** (D).

**Why it never refills.** At `keep=1` the steady state is one payload per observed player plus about a day of intake before the 12:30 UTC compaction — call it 10.5 GB. The file was inflated to 24 GB by six weeks of running at keep=3. Reusable space only helps if something grows into it; nothing will. **~13 GB is stranded permanently** unless the relation is rewritten.

**The tool.** `pg_repack` **1.5.2 is available** on the cluster (M, `pg_available_extensions`; not installed). It rewrites online, holding `ACCESS EXCLUSIVE` only briefly at start and end — which is the entire difference from the 2026-07-21 incident, a `VACUUM FULL` that held that lock on `warships_player` for 24 minutes. This table is also not the first hop of every request the way `warships_player` is.

**Cost and order.** It needs room for a second copy of the *live* data (~10 GB heap+TOAST+indexes) plus WAL for the same; free space today is 17.97 GB, so it is feasible but not roomy. **Do H6's truncate and H7's index drops first** (+2.2 GB of headroom), run it supervised in a quiet hour, never scheduled.

**Encoding, measured and mostly declined.** G10 said "store less per row is not available". Half right: the payload is already projected to 23 integer keys per ship (C, `incremental_battles.py:560-584`), so there is no unread field to drop. But it is stored as keyed objects, and on 60 real payloads a positional int array is **0.19×** the raw size and **0.61×** compressed (zlib as a stand-in; the columns use `lz4`, the cluster default, M). That is ~3.5 GB of the live 9 GB. Real, but it is a format migration on the diff baseline for a table H1's repack already cuts by 13 GB. **Not recommended now**; re-examine only if this table matters again after the repack.

**Right place?** This is the one store in the system with a genuine case for leaving the database: a key-value blob, written once, read once at the next observation, never queried relationally. It sits on the most constrained storage we own while the droplet has **57 GB of idle local disk** (M, `df`: 30 of 87 GB used). Moving it would also delete the compaction job, since overwrite-in-place is keep=1 by construction. Against that: the floor's writers would need atomic file handling, the droplet disk has no managed backup, and a lost baseline costs each affected player one interval of battle history. **Verdict: a sound idea, not justified at terminal state** once the repack lands — the DB copy then costs ~10 GB and is already built, tested and compacted correctly. Recorded so it is not re-derived later.

## H2 — the rollup does not roll up

**Assumption.** G10: `BattleEvent` "overlaps PDSS only as a raw layer overlaps its rollup, which is correct design." The design presumes many events per (player, ship, day).

**Measured.** Over the identical window (both floors 2026-06-13): **22,646,388** events against **20,951,980** rollup rows (M, `n_live_tup`) — **1.081 events per rollup row**. One sampled day agrees from the other side: an event already averages **1.89 battles**, and 67% are a single battle (M). The observation floor sees a player about every 8 hours and people play a given ship in one sitting, so an "event" is already nearly a player-ship-day. PDSS is BattleEvent minus 7.5% of its rows, with the same 22 counters (C, `models.py`).

**What each is for, today.** PDSS serves every UI window. `BattleEvent` has exactly two kinds of reader (C): five window aggregations by ship or by (ship, player) — `data.py:6159, 6613, 7066, 7089, 7162` — and the rollup's own rebuild and reconcile (`incremental_battles.py:1508, 2036`). None of the five needs intra-day time; all are expressible on PDSS, and one already made the move (the ship list, v5.3.9, payload-verified). F9.3/G8b asked for the rest on CPU grounds. The reconcile audits 30 days.

**Recommendation.** Move the four remaining aggregations to PDSS (proving payload equivalence per reader, as v5.3.9 did), then cut `BattleEvent` retention from 105 to **35 days** — what rebuild and reconcile actually need. Steady state falls 7.68 → ~2.6 GB: **~5 GB**, plus the insert and index cost of 70 days of rows nobody reads. This also **supersedes G3**: the 14 unread Phase-7 columns cost 56 B × 22.65 M = 1.27 GB today (G3's projection landed to the megabyte), but on a 35-day table that is 0.42 GB and no longer worth a write-path change.

The A1 probe is worth a footnote: `count(*)` over **one day** of `BattleEvent` did not finish in 55 s, twice. The BRIN index finds the blocks; fetching them at this cluster's ~11 ms random-read latency is what runs out the clock. Every reader moved off this table stops paying that.

## H3 — `battles_json` and the observation payload are one WG response stored twice

**Assumption.** G10: `battles_json` is "the *only* career-scope per-ship store".

**Measured.** 1% of players (10,838): of 4,521 holding `battles_json`, **4,382 (96.9%)** also hold a live observation payload (S). Both are written from the same `ships/stats` response inside one function (C, `incremental_battles.py:740-791`; `data.py:2179`). `battles_json` is ≈ 466 K players × ~10 kB ≈ **4.7 GB** of the player TOAST (S).

**Its anatomy** (60 players, avg 41.5 kB raw over 184 ships): **41% is `ship_name`, `ship_chart_name`, `ship_tier`, `ship_type`** — `Ship`-table columns copied into every player's blob, for every ship. Three more keys (`pve_battles`, `win_ratio`, `kdr`) are arithmetic on the others. `distance` has **no client reader** (C, grep of `client/app`).

**Recommendation — the modest one.** Slim `battles_json` to `ship_id` plus counters and join the metadata at serve time from the ~900-row `Ship` table, which is already what `views.py:1020` does for the timeline. Roughly **-40%, ~1.9 GB**, realized gradually as players refresh (no rewrite). The aggressive option — derive it wholly from the observation payload, -4.7 GB — would put the request path on the baseline store and needs two fields the payload lacks; **not recommended**.

## H4 — the `Player` row is mostly cold JSON, rewritten on every update

**Measured.** Heap is **1,576 B per row** (M). Summing `pg_stats` widths × non-null fractions for the JSON columns stored *inline* (`tiers` 624 B, `activity` 307, `type` 253, `achievements` 225, `efficiency` 124, `ranked` 108, `randoms` 50) gives **~1,140 B — about 73% of the row** (D). Lifetime: **31.5 M updates on 1.12 M rows, 9.1% HOT** (M), across 14 indexes. Under MVCC every update copies the whole tuple, so a `last_fetch` bump rewrites 1.1 kB of JSON that did not change. This is the mechanism behind G5's "1.32 TB dirtied in 27 days on a 10 GB table".

**Two levers, neither needing a rewrite.**
1. `ALTER TABLE warships_player SET (toast_tuple_target = 256)` pushes those JSON values out of line for *new* tuple versions. An update that does not touch a JSON column then reuses its TOAST pointer instead of copying the value; the hot tuple falls to ~400 B. Cost: one extra TOAST fetch when a payload is actually read — and those reads are Redis-first. **Measure on a copy before trusting it**, but it is a one-line, reversible, no-lock change.
2. ~~`player_last_fetch_idx` (234 MB, 516 lifetime scans) — candidate to drop.~~ **Retracted in QA, 2026-09-20.** This finding named `incremental_player_refresh` as the index's only consumer. It is not: the index backs the daily enrichment reclassify's `last_fetch >= now - H hours` filter, EXPLAIN-verified, taking that pass from ~36 min to 2.5-6 min per realm (`tasks.py:3594-3596`). It was dropped as unused once (`0034`) and deliberately re-created (`0067`). The grep behind the original claim looked for `last_fetch__lt` and missed `__gte`. **A low scan count is frequency, not value.** Keep the index.

## H5 — prune daily, and do it before the first peak

**Measured.** The archive timer fires on the **1st and 15th** (C, `deploy_to_droplet.sh:1174`). Retention is 105 days, so the two tables oscillate between 105 and ~120 days. They hold 99 days in 16.15 GB — **0.163 GB per day** (D). A delete-based prune returns nothing to the OS, so **the files stay at their high-water mark forever**: ~19.6 GB at a 120-day peak against ~17.3 GB if pruned daily.

**Why now.** The window only fills on 2026-09-26 and the first prune with candidates is 2026-10-01. **The peak has never happened.** Switch `OnCalendar` to daily before ~2026-10-02 and the files never grow past ~106 days — **~2.3 GB that is never allocated**, plus fifteen small WAL bursts instead of one 3-million-row delete. After the first peak the same change only stops the oscillation; the space is already spent. The archive step writes a dated directory per run, so daily runs need no code change (C; three runs on disk today, 239 MB).

`PlayerDailyShipStats` also shows **5.9 M deletes against 8.06 M inserts** (M) though it has never been pruned: the nightly sweeper rebuilds days by delete-and-reinsert. Worth a look at whether it rebuilds days that did not change.

## H6 — the achievements stream serves nobody (and a correction)

Step 5 of the capacity runbook stopped writing `PlayerAchievementStat` on the stated basis that `achievements_json` "is still in the player serializer". **That was wrong, and the error was mine**: `serializers.py:180` sits inside the serializer's **`exclude`** list. The code comment and the runbook are corrected in this commit.

The consequence is larger than the mistake. With the table gone, `achievements_json` is the only copy, and **it has no reader**: excluded from the serializer, absent from the client, and returned by `update_achievements_data` to three callers that all discard it (C). What remains is a WG API call per player refresh, ~660 MB of inline JSON (S) inflating the H4 row, and `achievements_updated_at`. G4 reached this verdict in August for the stream as a whole.

**Recommendation** (a product decision): stop calling `update_achievements_data` from the crawl and the refresh command, and NULL the column on refresh. That saves real WG request budget, which is the scarce resource the crawler is rationed on. The pending `0088` truncate is unaffected and still correct.

## H7 — indexes and columns that earn nothing

Same playbook as migration `0087`; counters have never been reset, so these are lifetime figures (M).

| Object | Size | Scans | Note |
|---|---|---|---|
| `battleevent_mode_983942c4` | 174 MB | 106 | two values over 22.6 M rows; identical to the PDSS one dropped today |
| `battleevent_player_id_1f7bf48a` | 202 MB | 2,518 | FK auto-index; `battle_event_player_time_idx` leads with `player` |
| `playerdailyshipstats_ship_id_16c96227` | 198 MB | 225 | the rollup scans by `date`, not by ship |
| `playerdailyshipstats_season_id_69e0cf16` | 197 MB | 2,043 | 94.1% NULL — make it partial (`WHERE season_id IS NOT NULL`), ~12 MB |
| `explorer_eff_rank_idx` | 38 MB | **0** | |
| **Total** | **~800 MB** | | **returned to the OS**, unlike row deletes |

> **Corrected 2026-09-20 after EXPLAIN on production: three of these five, ~437 MB.**
> `playerdailyshipstats_ship_id` is **kept permanently** — it carries the ship
> combat-profile population query, the legacy avg-damage scan and the rollup's
> trailing-days arm; "the rollup scans by `date`" was true of the rollup and this
> audit had checked nothing else. `battleevent_mode` is **held until H2's readers
> move**: the ranked treemap plans on it. `season_id` was dropped outright rather
> than made partial. Same error as the H4 retraction — a lifetime scan count read
> as a verdict. Migration `0089`; details in the remediation runbook, Step 3.

Dead or write-only **columns** (C+M): `Snapshot.survived_battles` is **0 in all 138,714 sampled rows** with no reader or writer in `data.py` (52 MB). PDSS `first_event_at`, `last_event_at` and `updated_at` are written and never read (24 B × 20.95 M = **~500 MB**). `ship_name` on both PDSS and `BattleEvent` (~435 MB together) duplicates `Ship.name`, and its one reader already falls back to the `Ship` table (`views.py:1020`). On a rolling table a `DROP COLUMN` needs no rewrite: new rows simply stop carrying it, and the saving arrives by itself over one 105-day window.

## What is right, and should be left alone

- **`Snapshot`** — 98 B rows, delta-gated, downsampled; G10's "cleanest success" still holds.
- **`PlayerExplorerSummary`**, **`ShipPopDailyAgg`**, **`ShipTopPlayerSnapshot`**, **`mv_player_distribution_stats`** — small, read constantly, correctly shaped.
- **TOAST compression** is already `lz4` cluster-wide (M).
- **Partitioning** the time-series tables would let retention be a `DROP PARTITION` that returns space to the OS. It is the textbook answer and **not worth it here**: with H5 the delete-and-reuse cycle is stable at terminal state, and the migration would be the riskiest change in this document.
- **The cold archive** stays `csv.gz` on the droplet. Parquet would be smaller and queryable, but costs a heavyweight dependency to improve files nothing reads; at ~10 GB a year against 57 GB free there is no pressure.

## Ranked recommendations

| # | Action | Return | Kind | Risk |
|---|---|---|---|---|
| 1 | **H5** — archive/prune timer to daily, **before ~2026-10-02** | ~2.3 GB never allocated | one `OnCalendar` line | very low |
| 2 | **H6/0088** — truncate `PlayerAchievementStat` (approved, pending) | 1.43 GB **to OS** | migration, written | low |
| 3 | **H7** — drop 4 indexes, make 1 partial | ~0.8 GB **to OS** + insert cost | one migration, `0087` pattern | low |
| 4 | **H1** — `pg_repack` `battleobservation`, supervised, after 2 and 3 | **~13 GB to OS** | one supervised operation | medium |
| 5 | **H6** — stop fetching achievements; NULL the column | ~0.66 GB + WG budget + row width | small code; product call | low |
| 6 | **H2** — move 4 aggregations to PDSS, then `BattleEvent` retention 105 → 35 d | ~5 GB steady state + daily CPU | moderate code, per-reader equivalence | medium |
| 7 | **H4** — `toast_tuple_target` (the index stays; see the H4 retraction) | the write-amplification centre | one-line, reversible; measure first | low-medium |
| 8 | **H3** — slim `battles_json` to counters; join `Ship` at serve time | ~1.9 GB, gradual | moderate code | medium |
| 9 | **H7** — drop the dead/write-only columns | ~1 GB over 105 days | migrations | low |

Items 1-4 alone take the volume from **78.6% to ~60%** (D: 65.89 − 1.43 − 0.8 − 13 ≈ 50.7 GB of 83.87).

## Related

- `agents/runbooks/runbook-db-capacity-remediation-2026-09-19.md` — the capacity plan this audit feeds; its Step 3 (resize) is retired if items 1-4 land.
- `agents/runbooks/runbook-db-table-audit-2026-07-19.md` (F-series) and `agents/work-items/data-capture-utility-audit-2026-08-05.md` (G-series) — the predecessors. H2 overturns G10's "correct design" verdict on the raw/rollup pair; H2 supersedes G3; H6 confirms G4.
- `agents/work-items/db-growth-capacity-2026-09-19.md` — the growth decomposition.

# Runbook: Best Clan CB Window Model

Created: 2026-04-04
Status: Implemented - validated against current code, live production, and WG season metadata on 2026-04-04

## Purpose

Capture two things in one place:

1. how the landing page Best -> CB sub-sort is derived today
2. the shipped replacement model: rank clans on the most recent 10 completed clan-battle seasons and score each clan by the average of its battle-weighted seasonal win rates, with skipped seasons counted as `0`

This runbook reflects the current implementation, including the bounded-shortlist operational guardrail used during landing cache builds.

## Validation Snapshot

This document was checked against:

- live production `GET /api/landing/clans/?mode=best&sort=cb&limit=10&realm=na`
- current backend ranking code in `server/warships/data.py`
- current durable clan-battle summary fields in `PlayerExplorerSummary`

Verified current-production facts:

- the Best -> CB list is backend-owned
- the landing payload exposes `avg_cb_battles`, `avg_cb_wr`, and `cb_recency_days`
- the current production ranking uses a completed-season window model for the returned Best -> CB list

Verified against WG `clans/season/` metadata:

- the recent cadence is roughly 5 seasons per year, but the exact count in a 2-year window depends on whether the rule is based on season `start_date`, `end_date`, and whether the current in-progress season is included

The original "roughly 5 per year" / "about 10 seasons in 2 years" framing was directionally right, but the exact window rule needs to be specified explicitly.

## Verified Season Timeline

Using WG `clans/season/` metadata as of 2026-04-04, the most recent seasons are:

| Season ID | Name         | Start      | End        |
| --------- | ------------ | ---------- | ---------- |
| 33        | Blue Marlin  | 2026-03-16 | 2026-05-18 |
| 32        | Pelican      | 2025-12-01 | 2026-02-09 |
| 31        | Mahi-Mahi    | 2025-09-08 | 2025-10-27 |
| 30        | Man o' War   | 2025-06-16 | 2025-08-04 |
| 29        | Sea Lion     | 2025-03-10 | 2025-05-12 |
| 28        | Orca         | 2024-12-02 | 2025-02-03 |
| 27        | Asp          | 2024-09-09 | 2024-10-28 |
| 26        | King Vulture | 2024-06-25 | 2024-08-05 |
| 25        | Polar Bear   | 2024-05-24 | 2024-06-10 |
| 24        | Sea Dragon   | 2024-02-12 | 2024-04-08 |
| 23        | Triton       | 2023-11-13 | 2024-01-08 |

Two-year cutoff used for validation: `2024-04-04`.

## What The 2-Year Window Actually Means

The exact season count changes depending on the rule:

### 1. Seasons whose `start_date >= 2024-04-04`

- count: `9`
- seasons: `25` through `33`

This excludes `Sea Dragon` because it started before the cutoff even though it ended after it.

### 2. Seasons whose `end_date >= 2024-04-04` including the current in-progress season

- count: `10`
- seasons: `24` through `33`

This is the only interpretation that matches the original intuition of "about 10 seasons in the last 2 years" as of 2026-04-04.

### 3. Completed seasons whose `end_date >= 2024-04-04`

- count: `9`
- seasons: `24` through `32`

This excludes the current in-progress season `33`.

### 4. Most recent 10 completed seasons

- count: `10` by definition
- seasons: `23` through `32`

This gives a stable fixed-count window, but it is no longer a strict 2-calendar-year slice because `Triton` ended on `2024-01-08`, which is older than the 2024-04-04 cutoff.

## Current Surface

The landing page Active Clans surface exposes:

- main mode buttons: `Best`, `Random`, `Recent`
- Best sub-sorts: `Overall`, `WR`, `CB`

The `CB` sub-sort is backend-owned. The client does not compute or re-order clan rankings locally. The landing page requests `/api/landing/clans/?mode=best&sort=cb` and renders the returned order directly.

## Replaced Aggregate Method

This is the aggregate CB method that existed before the 10-season window model replaced it.

### Eligibility gate before CB ranking

Clans must first pass the shared Best-clan hard filters:

- `members_count > 10`
- `tracked_count >= 5`
- `activity_ratio >= 0.40`
- `cached_total_battles >= 50_000`
- excluded clan IDs removed via `BEST_CLAN_EXCLUDED_IDS`

### Aggregate CB inputs

The current CB sub-sort uses these clan-level inputs derived from `PlayerExplorerSummary`:

- `avg_cb_battles`: average `clan_battle_total_battles` across tracked members
- `avg_cb_wr`: average `clan_battle_overall_win_rate` across tracked members
- `avg_member_score`: average `player_score` across tracked members
- `active_members`: `Clan.cached_active_member_count`
- `cb_recency_days`: derived from the newest `clan_battle_summary_updated_at`

### Aggregate CB formula

The current CB ranking logic is approximately:

```text
recency_factor = 1 / (1 + years_since_last_cb)
cb_support_factor = min(active_members / 25, 1) * min(avg_member_score / 5, 1)
cb_success_margin = max(avg_cb_wr - 50, 0)
cb_sort_score = avg_cb_battles * cb_success_margin * recency_factor * cb_support_factor
```

### What the aggregate method was trying to reward

- meaningful clan-battle volume
- clan-battle win rate above 50%
- recently refreshed clan-battle data
- enough active members to back the result
- enough average tracked-player quality to avoid rewarding pure grinders

### Why the aggregate method was replaced

The current method is not season-aware. It compresses clan-battle performance into a single blended aggregate over whatever history is present in `PlayerExplorerSummary` plus a recency factor derived from the last refresh timestamp.

That means it does not directly answer this question:

"Which clans have been the best competitive clans over a recent multi-season window?"

Instead, it answers a proxy question:

"Which clans show the strongest recent clan-battle aggregate when we combine volume, above-50 CB WR, roster support, and freshness?"

## Implemented Replacement Model

### High-level rule

Judge Best -> CB clans on the most recent 10 completed clan-battle seasons.

Validated planning options:

- if the product wants a strict 2-year slice and is willing to include the current season, the correct window today is seasons `24` through `33` (`10` seasons)
- if the product wants completed seasons only and a strict 2-year slice, the correct window today is seasons `24` through `32` (`9` seasons)
- the shipped rule is "most recent 10 completed seasons", which today is seasons `23` through `32`

### Score

For each clan:

1. identify the most recent 10 completed clan-battle seasons
2. compute a clan WR for each season in the window
3. if a clan skipped a season, record that season as `0`
4. weight each season's WR by how many clan-battle games the clan played that season
5. score the clan by the average of those battle-weighted seasonal values

Approximate formula:

```text
cb_window_score = (
	season_wr_1 * min(season_battles_1 / 30, 1)
	+ ...
	+ season_wr_n * min(season_battles_n / 30, 1)
) / n
```

Where:

- `season_wr_n` is the clan's WR for that season if present
- `season_battles_n` is the clan's derived roster clan-battle count for that season
- `season_wr_n = 0` if the clan did not participate in that season
- each season reaches full weight at `30` battles, so tiny same-WR samples do not score like full seasons
- `n` must be defined explicitly by product rule, not inferred loosely from the phrase "last 2 years"

### Window rule

The shipped rule is:

```text
rank by the average WR across the most recent 10 completed clan-battle seasons
```

Why this is the current contract:

- it avoids scoring against a partial in-progress season
- it always uses a fixed denominator
- it stays close to the original product intuition
- it avoids edge cases where a strict calendar cutoff yields 8, 9, or 10 seasons depending on the day

Tradeoff:

- it is not exactly the same as a strict rolling 2-year window
- on 2026-04-04 it reaches back to season `23`, which ended slightly more than 2 years before the validation date

### Behavioral intent of the model

This changes the ranking question from volume-weighted competitive activity to sustained recent competitive strength.

It would reward:

- clans that keep showing up across many recent seasons
- clans that post strong WRs repeatedly, not just in one hot streak
- clans with consistent recent CB participation

It would punish:

- clans that skip many seasons
- clans with old competitive glory but weak recent presence
- clans that inflate their current score mostly through aggregate battles rather than season-by-season success

## Why This Model Is Different

The proposed model removes several things the current formula uses directly:

- no direct battle-volume multiplier
- no direct member-score multiplier
- no direct active-member multiplier in the final score
- no recency decay term inside the score itself

Instead, the recency boundary is handled structurally by the season window.

The score becomes easier to explain:

"Best CB clan = average recent seasonal CB win rate, with each season weighted by how many CB games the clan actually played and skipped seasons counted as zero."

That is a much more legible product story than the current aggregate proxy.

## Data Model Implications

This proposal is not a small parameter tweak. The current CB sub-sort does not retain the per-season clan history required to score the window directly.

### What we have now

Current Best -> CB ranking depends on aggregated member-level fields already present in `PlayerExplorerSummary`:

- `clan_battle_seasons_participated`
- `clan_battle_total_battles`
- `clan_battle_overall_win_rate`
- `clan_battle_summary_updated_at`

Only `clan_battle_seasons_participated`, `clan_battle_total_battles`, `clan_battle_overall_win_rate`, and `clan_battle_summary_updated_at` are durably stored today. They are useful for lifetime or blended aggregate scoring, but they do not preserve a bounded per-season series for the last 2 years.

### What the model needs

To implement this method correctly, the backend needs a clan-season view over roughly the last 10 seasons for candidate clans.

That likely means one of these approaches:

1. persist clan-season aggregates directly in a dedicated table or cacheable materialized surface
2. derive clan-season WRs during ranking build time from fresh per-player season payloads and current clan membership data
3. expand the explorer-summary lane so clan-level seasonal WR history is stored explicitly per clan and season

The shipped implementation uses a bounded version of option 2. It avoids a full-population fan-out by first narrowing the field with the existing aggregate CB proxy and then computing the 10-season window score on that shortlist.

## Current Backend Method

Current source of truth: `score_best_clans(..., sort='cb')` in `server/warships/data.py`.

### Window scoring rule

The backend now:

1. selects the most recent 10 completed seasons from WG `clans/season/` metadata
2. builds a bounded shortlist from the old aggregate CB proxy so landing cache builds stay operationally bounded
3. refreshes or reuses clan season summaries for that shortlist
4. computes `cb_window_score = average(season WR × min(season battles / 30, 1) across the 10-slot window, missing seasons = 0)`
5. sorts Best -> CB by that score, then breaks ties with participation depth, battles in the window, member quality, clan WR, and overall score

### Operational guardrail

This bounded-shortlist step is intentional. Local data showed roughly 1.5k clans pass the shared Best-clan hard filters, and synchronously rebuilding a full season history for all of them during a landing cache build would create the wrong load profile.

The public ranking contract is the 10-season average on the returned Best -> CB list. The shortlist is an implementation guardrail, not a UI-visible rule.

### Important upstream constraint

The public WG API does not expose clan-level league/rating standings directly. Earlier review already established that clan-battle data is primarily accessible as player-level seasonal stats rather than a clean clan-season leaderboard endpoint.

So if this proposal is implemented, clan season WRs will likely still need to be derived from player-linked clan-battle season data rather than fetched as an authoritative clan-level season object.

That means the key production question is not just "what formula should we use?" but also "what durable clan-season dataset do we trust enough to rank against on every landing cache build?"

## Residual Product Questions

These are still worth monitoring after implementation:

1. Which window definition should become the public contract: strict 2-year by `end_date`, strict 2-year by `start_date`, or most recent 10 completed seasons?
2. If a season exists but only partial clan/member data is present in our DB, should missing data still count as `0` or should it be considered unknown?
3. Should all skipped seasons be hard zeroes, or should there be a participation minimum before a season counts as non-zero?
4. If a clan played only one or two battles in a season, should that season WR count fully?
5. Should the hard Best-clan eligibility filters remain identical for `sort=cb`, or should CB get a tighter participation-specific gate?
6. How should membership churn be handled when player-level season data is used to derive a clan-level season WR for historical seasons?

## Recommendation

Treat this as a genuine scoring-model replacement, not a tune-up of the old `cb_sort_score` coefficients.

If the product goal remains "best competitive clans over recent seasons", the current implementation matches that intent more closely than the old aggregate proxy. If the shortlist guardrail ever proves too lossy, the next step should be a durable clan-season store rather than another coefficient tweak.

## Related Docs

- `agents/runbooks/runbook-best-clan-eligibility.md`
- `agents/runbooks/spec-best-clan-subfilters.md`

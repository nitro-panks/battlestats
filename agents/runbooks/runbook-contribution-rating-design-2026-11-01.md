# Runbook: Contribution Rating Design (a battlestats-native Personal Rating)

_Created: 2026-11-01_
_Context: battlestats has no Personal Rating. The operator declined to adopt wows-numbers PR on two grounds: the algorithm and its expected-values dataset belong to wows-numbers, and the formula is reductive (damage and frags carry ~87% of the weight; spotting, caps, tanking and survival carry zero). This runbook records the design for our own rating, estimated from our own 90-day Random-battle population._
_QA: reviewed 2026-11-01; see QA Notes._

## QA Notes

_Reviewed 2026-11-01 against /home/august/code/battlestats (worktree docs+contribution-rating-design). 31 assertions checked, 7 corrected._

### Resolved
- **`PlayerDailyShipStats` is "Random mode only"** -> actual: the table carries both modes, `MODE_CHOICES = [(MODE_RANDOM, 'Random'), (MODE_RANKED, 'Ranked')]` (`server/warships/models.py:783`); only the Phase 7 columns are random-only because the ranked payload lacks them -> body now says every read filters `mode='random'`.
- **Per-(player, ship, window) aggregation lives in `compute_realm_top_ships` and "the nightly `ShipTopPlayerSnapshot` writer"** -> actual: the per-ship board writer is `compute_ship_top_player_snapshot` (`server/warships/data.py:6108`), dispatched nightly from `server/warships/tasks.py:1535`; `compute_realm_top_ships` (`data.py:6649`) is the realm treemap -> body names the correct function.
- **A career rating is computable for every player because the career pvp block "arrives with the `ships/stats` hydration"** -> actual: `_fetch_ship_stats_for_player` returns the full block (`server/warships/api/ships.py:255`), but the persisted `battles_json` row keeps only `battles / wins / losses / frags` per ship (`server/warships/data.py:2225`); the raw block is persisted only in `BattleObservation.ships_stats_json` (`server/warships/models.py:638`), which exists for observed players alone -> gap 4 and step 6 now state that the career tier needs the hydration persist widened; this is now Open Question 3.
- **"The device the Ships tab already uses for window deltas"** -> actual: the existing device is the win-rate delta pill in `client/app/components/RandomsSVG.tsx:317`, which compares each ship's trailing-30d WR against its career WR; no 30d-vs-90d comparison exists anywhere in the client -> decision 1 names the pill and states that the two-window delta is new.
- **Fit the regression per type "DD, cruiser, BB, CV"** -> actual: `ship_type` has five values, `'AirCarrier' 'Battleship' 'Cruiser' 'Destroyer' 'Submarine'` (`server/warships/data.py:2746`); the board code special-cases Submarine alongside CV as a small-roster class (`data.py:6157`, `6288`) -> decision 4 and the model now list five types.
- **"The percentile step is new"** -> actual: `PERCENT_RANK()` with the `1 - PERCENT_RANK()` orientation is already the efficiency-rank pattern (`server/warships/data.py:1175`, `1208`) -> body says the per-(ship, lane) application is new, the SQL pattern is not.
- **`rating(p,s)` is a percentile "within realm r"** -> defect: `score` comes from a per-type logistic fit, so scores from different types sit on different logit scales and are not comparable in one realm-wide ranking -> model corrected to percentile within (realm, ship type); the player roll-up then averages commensurable per-ship percentiles.
- **`MIN_POP_BATTLES` and `k` were unvalued** -> picked: reuse the ship-board triple, player floor 15, ship population guard 20, prior 50 (`server/warships/data.py:6177`, `SHIP_BADGE_PRIOR_BATTLES` at `6182`), env-tunable, subject to spike item 4. Reason: same table, same window, tuned against real NA data.
- **A ship with fewer qualifying players than the population guard** -> picked: fall back to the (realm, type, tier) pool for the lane percentiles, the same shape as the board's CV / Submarine small-roster handling (`data.py:6157`).
- **A type with too few rows to fit (CV, Submarine)** -> picked: serve the pooled all-type `β` and mark the stored row `pooled=true`. Reason: a missing fit must never block scoring (cache-first policy).
- **The 30d delta's baseline** -> picked: compute the 30d score with the same `β` and the same 90d per-(ship, lane) percentile baselines, rates summed over 30d. Reason: the delta must measure the player, not a shifted baseline.
- **Players with zero battles in the window** -> picked: no rating and no delta. A ship with battles but all-zero lane sums is a legitimate zero rate, not missing data.
- **Step 1 call sites were unlisted** -> the potential-damage widening touches the `ShipSnapshot` dataclass and `_coerce_ship_snapshot` (`server/warships/incremental_battles.py:116`), the `BattleEvent` delta writer, the `aggregates` dict in `rebuild_daily_ship_stats_for_date` (`incremental_battles.py:1451`, delete at `1518`), `_SHIP_COMBAT_SUM_FIELDS` (`data.py:7321`), and migrations on `BattleEvent` (`models.py:664`) and `PlayerDailyShipStats` (`models.py:782`) -> step 1 now lists them.
- **No tests were named** -> implementation plan must add `server/warships/tests/test_contribution_rating.py` (lane rates, percentile fallback, pooled `β`, zero-battle case, `pending` contract) and a client test beside `PlayerDetailLocale.test.tsx` for the header figure in all three locales -> added to Implementation shape.

### Unverified
- The wows-numbers formula, coefficients and floors: external site, not in the repo.
- `art_agro` / `torpedo_agro` present in the WG `ships/stats` pvp block: external API, no fixture in `server/`; spike item 5.
- API `xp` carries premium-account and flag multipliers: WG behaviour, not in the repo.
- Floor coverage "roughly 15 to 25% of actives": from the operator's memory notes, not the repo; the observation benchmark snapshots on the droplet (`/observation`) would confirm.

### Open Questions
1. **Name.** "Contribution rating" is a placeholder. Blocks client strings and the About paragraph (step 5).
2. **Version-one lane set.** Ship with the five lanes we hold, or hold the release until potential damage is captured (step 1). Shipping first means a published weight set changes when tanking lands.
3. **Career tier for unobserved players.** The persisted `battles_json` lacks the combat fields, so showing a career rating means widening what every hydration writes (one row per player, ~15 extra fields per ship) or reading nothing. Options: (a) widen the persist and show a career figure labelled as career; (b) show nothing for unobserved players; (c) show the figure only once the player has been observed. Blocks step 6 and the player-header layout in step 5.

## Purpose

Design record for a per-player, per-ship contribution rating computed from the battle-history pipeline (`BattleObservation` → `BattleEvent` → `PlayerDailyShipStats`). It states the decisions already taken, the data we hold and the gaps, the model, the implementation shape, and the spike that must run before any product code is written. A builder should be able to plan from this document; the design is not yet approved for implementation.

## Why not wows-numbers PR

The wows-numbers formula is public: `PR = 700·nDmg + 300·nFrags + 150·nWins`, each term a ratio of the player's per-ship average to a population expected value, floored at 0.4 / 0.1 / 0.7 respectively.

- Damage is 61% of the mass and frags 26%. Frags are largely a function of damage, so roughly 87% of PR is one signal counted twice. Wins are 13%.
- Spotting damage, capture points, potential damage, survival and plane kills have weight zero. A destroyer that spots a ship which an ally then kills receives nothing.
- Ratio-to-mean on a heavy-tailed quantity rewards variance; one 200k game moves the figure more than ten competent games.
- It is career-cumulative; battlestats treats career as context, not current form (the 30-day thesis, widened to 90 days for ship standings in 2026-09 and for the battle-history card in v5.12.0).

Ownership: the coefficients and floors are theirs by convention and the expected-values dataset is theirs by labour. A rating whose weights are estimated from our own population, over our own window, is derivative of neither.

## Data we hold

Per (player, ship, day) in `PlayerDailyShipStats` (`server/warships/models.py:782`; the table carries both `random` and `ranked` rows, so every read here filters `mode='random'`), rolled up from `BattleEvent` deltas (`server/warships/incremental_battles.py`, `_coerce_ship_snapshot`). Retention `BATTLE_HISTORY_ARCHIVE_RETENTION_DAYS=105` (prod pin in `server/deploy/deploy_to_droplet.sh`), sized for a 90-day read.

| Lane | Columns |
|---|---|
| Damage | `damage`, `frags`, `xp` |
| Spotting | `damage_scouting`, `ships_spotted` |
| Objectives | `capture_points`, `dropped_capture_points`, `team_capture_points` |
| Gunnery | `main_/secondary_/torpedo_` `shots`, `hits`, `frags` |
| Survival / AA | `survived_battles`, `planes_killed` |

Already in use: `compute_ship_combat_comparison` (`server/warships/data.py`) sums every one of these columns (`_SHIP_COMBAT_SUM_FIELDS`) per ship over the population, bracketed by account win rate (all / top50 / top25), under `SHIP_COMBAT_POP_STATEMENT_TIMEOUT_MS` on the `background` worker. That is an existing per-ship population baseline for every lane; it runs at `SHIP_COMBAT_WINDOW_DAYS = 30` today.

### Gaps

1. **Potential damage is not captured.** The WG `ships/stats` pvp block carries `art_agro` and `torpedo_agro` (potential damage received from artillery and torpedoes). `_coerce_ship_snapshot` does not read them; `BattleEvent` and `PlayerDailyShipStats` have no columns for them. Tanking is therefore invisible. Adding them is a parser change plus a schema widening (migration on both tables, plus the `BattleEvent` → `PlayerDailyShipStats` rebuild path). **Gate:** confirm the live payload carries the fields on all three realms before writing the migration; the grep over `server/` for `art_agro` returns nothing today, so no fixture proves it.
2. **`ShipPopDailyAgg` carries only damage and gunnery sums** (`battles, wins, frags, damage_sum, xp, main_/secondary_/torpedo_ shots+hits`). Spotting, caps and survival baselines must come from `PlayerDailyShipStats` directly (as the combat comparison does) or the aggregate must be widened.
3. **Ranked payloads lack the nested fields** (`RankedShipSeasonSnapshot` docstring). The rating is Randoms-only. This matches the standing priority (Random over Ranked).
4. **Coverage.** 90-day combat fields exist only for players the observation floor has observed (floor coverage roughly 15 to 25% of actives). Any player's career pvp block is fetched by the `ships/stats` hydration on page view (`api/ships.py:255`), but the persisted `battles_json` row keeps only battles, wins, losses and frags per ship (`data.py:2225`); the raw block is persisted only in `BattleObservation.ships_stats_json`, which observed players alone have. A career rating for an unobserved player therefore requires widening what hydration persists (QA Open Question 3). Two tiers, and the career tier has a cost.

## Decisions

Taken in conversation on 2026-11-01. Items marked _(builder's call)_ are defaults the operator has not ruled on.

1. **Window: 90 days of Randoms.** Both the population fit and the player's figure use the full 90-day read. Rationale: three times the 30-day sample for per-ship baselines and the per-type regression; thin ships (T7, superships, low-population premiums) clear the sample floor; most observed players have enough battles per ship at 90 days that the figure is data rather than prior. Current form is shown as a 30-day-vs-90-day delta beside the figure, styled like the win-rate delta pill on the Randoms chart (`RandomsSVG.tsx:317`, which compares trailing-30d WR to career WR per ship); the two-window comparison itself is new. No storage cost: retention is already sized for the 90-day read.
2. **Lanes, normalised as per-ship percentiles within realm, not ratios to a mean.** Version-one lanes: damage, spotting damage, capture points (held plus dropped), survival, planes killed. Potential damage joins once gap 1 is closed. Percentiles make a 90th-percentile spotter commensurable with a 90th-percentile damage dealer and are immune to the fat tail.
3. **Frags are dropped from the composite** and shown as a plain stat. They double-count damage. If kill-securing deserves credit it enters as a residual term only if the regression shows it adds predictive value beyond damage.
4. **Weights are estimated, not asserted, per ship type.** Battle-weighted logistic regression of the player's 90-day win rate on the lane percentiles, fitted separately for each of the five `ship_type` values (`Destroyer`, `Cruiser`, `Battleship`, `AirCarrier`, `Submarine`; CV and Submarine fall back to the pooled fit when thin), refitted on a nightly or weekly cadence from our own population. This answers "why is damage so important" with a measurement and gives the coefficients a provenance we can publish on the About page.
5. **Shrinkage and a floor.** Lane percentiles shrink toward 50 with weight `battles / (battles + k)`, reusing the empirical-Bayes pattern already in `data.py` (`EFFICIENCY_RANK_SHRINKAGE_K = 12.0` for the efficiency rank; `SHIP_BADGE_PRIOR_BATTLES` for the ship boards). Defaults (QA pick): player floor 15, ship population guard 20, `k` = 50, the ship-board triple at `data.py:6177`, env-tunable; spike item 4 may move them. A ship under the population guard takes its lane percentiles from the (realm, type, tier) pool. No rating below the floor; the label states the window and the count.
6. **Scale and colour.** 0 to 100, as a within-realm percentile of the composite. Colour bands reuse `client/app/lib/wrColor.ts` so the figure reads like the rest of the site. _(builder's call: the exact mapping from rating percentile onto the WR band thresholds.)_
7. **Win rate is displayed beside the rating, never inside it.** The composite is a behavioural score; the gap between rating and win rate is itself informative (a player rated well above their win rate is playing well on losing teams).
8. **XP is a validation target, not an input.** WG base XP already prices spotting, tanking and caps in their economy, but the API `xp` carries premium-account and flag multipliers. Use it to sanity-check the fitted weights (lanes the regression rewards should also correlate with XP per battle), not as a lane.
9. **Name.** Undecided. "Contribution rating" is the working title in this document.

## Model

For player _p_, ship _s_, realm _r_, window _W_ = 90d, lane _ℓ_ ∈ {damage, spotting, caps, survival, planes, (potential)}:

```
rate_ℓ(p,s)      = sum_W(lane column) / sum_W(battles)                 # per-battle rate
pct_ℓ(p,s)       = percentile of rate_ℓ(p,s) among all players with
                   battles_W(·,s) >= MIN_POP_BATTLES on ship s, realm r  # 0..1
shr_ℓ(p,s)       = (b · pct_ℓ + k · 0.5) / (b + k),  b = battles_W(p,s)  # shrink toward median
logit(WR(p,s))   ~ β0[type(s)] + Σ_ℓ β_ℓ[type(s)] · shr_ℓ(p,s)            # fitted, weighted by b
score(p,s)       = Σ_ℓ β_ℓ[type(s)] · shr_ℓ(p,s)                          # linear predictor, no intercept
rating(p,s)      = 100 · percentile of score(p,s) within (realm r, type(s))  # per-type: logit scales differ across fits
rating(p)        = battle-weighted mean of rating(p,s) over the player's ships in W
delta30(p)       = rating(p) recomputed with 30d rate sums, same β, same 90d percentile baselines
```

Edge cases (QA picks): zero battles in W gives no rating and no delta; battles with all-zero lane sums is a legitimate zero rate. Percentile ties follow the `1 - PERCENT_RANK()` convention already used at `data.py:1175`.

Notes for the builder:

- The per-(player, ship, window) aggregation already exists for the ship boards (`compute_ship_top_player_snapshot`, `data.py:6108`, dispatched nightly from `tasks.py:1535`); applying `PERCENT_RANK()` per (ship, lane) is new, the SQL pattern (`data.py:1175`) and the aggregation are not.
- Fit the regression on the population aggregate, not per request. Store `β` per (realm, ship_type, fit_date) in a small table with a `pooled` flag; a stale `β` is acceptable, a missing one serves the last published fit (cache-first policy, `spec-cache-first-lazy-refresh-policy-2026-03-19.md`), and a type with too few rows serves the pooled all-type fit.
- `rating(p)` must never be computed on the request thread. Compute it in the same nightly pass that writes the ship boards, or lazily on the `background` worker with a `:published` fallback and `pending: true` for the cold case. The `/api/fetch/*` rule applies.
- The CV lane set differs: CVs have no main-battery gunnery and their spotting is structural. Fit CV separately and expect the damage and spotting coefficients to differ markedly from surface ships.

## Spike (run before any product code)

Read-only regression over production `PlayerDailyShipStats`, last 90 days, Random mode, via `manage.py shell` over piped stdin under a `statement_timeout`, off-peak. Follow `feedback_prod_db_long_query_safety` and `feedback_separate_destructive_op_from_precheck`: no writes, no schema.

Outputs to capture in this runbook under Validation:

1. Per ship type, the fitted `β` with standard errors, and the share of the linear predictor each lane carries on the median player.
2. Pseudo-R² of the lane model against the win rate, per type, and the same for a damage-only model. The delta is the entire argument for the rating.
3. The correlation of `score` with `xp / battles` per type (decision 8).
4. The fraction of observed players who clear the minimum battle count at 90 days versus 30 days (decision 1 depends on this being large).
5. Whether `art_agro` / `torpedo_agro` are present in the live `ships/stats` payload on NA, EU and ASIA (gap 1).

Decision rule stated in advance: if the fitted weights reproduce ~60% damage for every type, the premise is wrong and the rating is not worth building. If spotting or caps carry DD wins, proceed to a written design and a plan.

## Implementation shape (sketch, not a plan)

1. Parser and schema widening for potential damage, if gap 1 clears. Touch points: the `ShipSnapshot` dataclass and `_coerce_ship_snapshot` (`incremental_battles.py:116`), the `BattleEvent` delta writer, the `aggregates` dict in `rebuild_daily_ship_stats_for_date` (`incremental_battles.py:1451`; delete-then-recreate at `1518`), `_SHIP_COMBAT_SUM_FIELDS` (`data.py:7321`), and migrations on `BattleEvent` (`models.py:664`) and `PlayerDailyShipStats` (`models.py:782`); defaults 0.
2. Nightly fit task on the `background` worker: per-(realm, type) regression, writes `β` rows.
3. Nightly score task, co-scheduled with the ship-board snapshot: per-(player, ship) lane percentiles, score, rating; per-player roll-up. Striped per realm like every other per-realm periodic task (`runbook-realm-schedule-striping-2026-08-15.md`).
4. API: rating in the player payload (per-player figure, per-ship figures on the Ships tab), `pending` contract honoured.
5. Client: figure plus 30d-vs-90d delta on the player header; per-ship rating column on the Ships tab; an About-page paragraph stating the lanes, the window and that the weights are fitted from our population. Locale strings for en/ko/ja; the Korean register rules in `runbook-korean-community-outreach.md` apply.
6. Career tier for unobserved players: requires widening the hydration persist (`battles_json` at `data.py:2225` keeps four fields per ship today), same lanes, population baselines from the 90-day fit. Labelled as career, not current form. Blocked on QA Open Question 3.
7. Tests: `server/warships/tests/test_contribution_rating.py` (lane rates, pool fallback, pooled `β`, zero-battle case, `pending` contract) and a client test beside `PlayerDetailLocale.test.tsx` covering the header figure in en/ko/ja.

## Validation

Spike results pending; they go here. The runbook QA pass is recorded in QA Notes above.

## Follow-ups

- Operator decisions outstanding: name; lane set for version one (wait for potential damage, or ship without it); whether unobserved players see a career rating or nothing.
- `ShipPopDailyAgg` widening to spotting / caps / survival, if the per-ship baseline is to come from the aggregate rather than from `PlayerDailyShipStats`.
- Once shipped: a Korean community post; the per-type weights are the headline.

## Related

- `runbook-ship-leaderboard-architecture-2026-06-18.md` (per-(player, ship, window) aggregation, shrinkage, nightly snapshot)
- `runbook-battle-history-rollout-2026-04-28.md` (Phase 8 first-party expected values, never built; this runbook supersedes that intent)
- `runbook-battle-history-data-operationalization-2026-06-16.md` (the untapped-fields finding)
- `spec-cache-first-lazy-refresh-policy-2026-03-19.md`
- `runbook-realm-schedule-striping-2026-08-15.md`

# Runbook: Clan Battle Badges

Created: 2026-04-04
Status: Implemented and validated with focused backend/frontend tests on 2026-04-04.

## Purpose

Add a clan-level Clan Battles activity badge to the landing page Active Clans surface without treating win rate as the signal.

The badge answers a narrower product question than the Best -> CB sort:

- Best -> CB asks which clans have been strongest across recent completed CB seasons.
- The CB badge asks which clans appear to be genuine, ongoing Clan Battles participants.

Tooltip copy for the badge is intentionally simple:

- `clan battle enjoyers`

## Design Goal

Do not badge a whole clan because one or two members played a tiny CB slice once.

The heuristic must reward:

- recent activity
- repeated activity across multiple completed seasons
- a meaningful participation share of the clan roster

The heuristic must ignore:

- CB win rate
- CB success margin
- member score

This badge is about activity, not performance.

## Chosen Window

Use completed clan-battle seasons whose `end_date` falls within the last 3 years.

Rationale:

- 1 year is too twitchy and can overreact to a single active patch.
- 2 years is better, but still drops too much context for clans that play on and off.
- 3 years preserves enough history to see whether a clan keeps coming back, while the weighting still makes fresh seasons matter most.

## Recency Weights

Each completed season in the 3-year window receives a recency weight based on age:

- ended within 1 year: `1.00`
- ended within 2 years: `0.60`
- ended within 3 years: `0.35`

This lets a clan keep some credit for older CB activity without allowing old seasons to carry the badge on their own.

## Per-Season Activity Gate

A season counts as an active CB season only if all of these are true:

- `roster_battles >= 20`
- `participants >= 4`
- `participants / max(current_members, participants, 1) >= 0.12`

Notes:

- the `20` battle floor filters out very small test samples
- the `4` participant floor keeps one-person or two-person seasons from badging the whole clan
- the `12%` roster-share floor handles large clans where `4` people would still be trivial

## Badge Heuristic

For each clan:

1. gather cached clan-battle season summaries for the current clan roster
2. keep only completed seasons inside the last 3 years
3. compute two weighted aggregates across the full window

Weighted active share:

```text
weighted_active_share = sum(weight for active seasons) / sum(weight for all window seasons)
```

Weighted participation share:

```text
weighted_participation_share = sum(weight * participation_share_per_season) / sum(weight for all window seasons)
```

The clan earns the badge only if all of these are true:

- at least `1` active season ended within the last year
- `weighted_active_share >= 0.25`
- `weighted_participation_share >= 0.05`

## Intended Behavior

This should badge clans that:

- show up across multiple recent CB seasons
- have more than a token handful of participants
- keep some recent CB pulse rather than relying on old history

This should not badge clans that:

- had one hot recent season and then disappeared
- had only 1-3 participating members
- had a tiny roster slice active in an otherwise inactive clan

## Data Source

The badge is server-owned and derived from the existing clan CB season cache in `server/warships/data.py`.

No new browser-side heuristic or WG fetch path is introduced.

Landing payload builders attach a boolean:

- `is_clan_battle_active`

The client only renders the badge when that backend flag is true.

## UI Contract

Surface:

- Active Clans tag grid in `client/app/components/PlayerSearch.tsx`

Rendering rule:

- show a neutral clan-battle shield icon beside the clan tag when `is_clan_battle_active` is true

Tooltip / label:

- `clan battle enjoyers`

## Validation

Focused coverage added for:

- summary-level badge heuristic behavior
- best-clan payload badge flagging
- landing UI rendering of the badge only when the backend flag is true

Recommended local validation commands:

```bash
cd /home/august/code/archive/battlestats
docker compose exec -T server python -m pytest --reuse-db warships/tests/test_landing.py -x --tb=short
cd /home/august/code/archive/battlestats/client
npm test -- --runInBand app/components/__tests__/PlayerSearch.test.tsx
```

## Follow-up Questions

If badge coverage feels too strict or too loose in live data, the first knobs to tune are:

- `weighted_active_share` threshold
- per-season participation share floor
- participant floor
- recency weight schedule

The first thing not to tune is win rate, because that would change the feature from activity signaling into performance signaling.

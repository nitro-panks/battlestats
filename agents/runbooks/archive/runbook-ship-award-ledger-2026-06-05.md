# Runbook: Durable ship-award ledger (Phase 1)

> **ARCHIVED 2026-06-14 — feature removed.** Ship Honors / the `ShipAward` ledger was
> removed entirely when ship badges moved to a nightly rolling recompute. Superseded by
> [runbook-ship-badges-rolling-2026-06-14.md](../runbook-ship-badges-rolling-2026-06-14.md).

_Created: 2026-06-05_
_Context: The ship-top-player feature (`runbook-ship-top-player-badges-2026-06-05.md`) computes a
fortnight leaderboard per ship per realm ~weekly and surfaces the **current** top-3 as profile
badges/banner cards. That standing is intentionally live — it lapses when a player stops playing. But
there is no durable layer underneath it: the `ShipTopPlayerSnapshot` table is overwritten each run and
pruned after 21 days, so a genuinely dominant player (e.g. the realm's #1 Shimakaze who vacations every
other month) accretes nothing — their record is a string of ephemeral badges that vanish during gaps.
Phase 1 adds an **append-only award ledger**: every snapshot run records each top-3 placement as a
durable fact, never pruned, and the profile shows a per-ship career summary (N-time #1, windows held,
current standing / last held). The live banner is unchanged; this is purely additive._
_Status: implemented (awaiting deploy) — 2026-06-05. `ShipAward` model + migration `0062`, ledger write in `compute_ship_top_player_snapshot`, `get_player_ship_awards`, `PlayerSerializer.ship_awards`, and the `ShipHonors` panel shipped. Durable metric = tenure/"weeks held" (`times_first` = snapshot runs held #1), confirmed with the user. Tests green (see Validation). Not yet on prod._

## Scope (Phase 1 only)

- **In:** append-only `ShipAward` ledger written by the existing snapshot task; a read helper that
  aggregates it per ship; a `ship_awards` payload field; a profile "Ship Honors" panel.
- **Out (Phase 2):** reigns / longest-streak, all-time-vs-last-12-months split, realm hall of fame,
  decay/prestige score, merging the live banner into the honors panel.

## Design decisions (resolved)

- **What accretes:** the top-`SHIP_BADGE_TOP_N` (3) placements — same set as the badges. `#1` is the
  headline; 2nd/3rd are recorded for breadth.
- **Counting unit (the cadence trap):** the ledger stores **one row per (realm, ship, rank,
  captured_on)** — i.e. one row per *snapshot run* a player held that rank, not per overlapping window.
  So `times_first = count(rank=1 rows)` = the number of weekly snapshots held at #1 ≈ **windows/weeks
  held #1**. This is cadence-stable; never count raw window overlaps.
- **Durability vs recency:** the ledger is **permanent** (never pruned). Recency is surfaced, not
  enforced: each ship shows `current_rank` (from the latest live snapshot, `None` if not currently
  top-3) and `last_on` (most recent placement date). A vacationing champion reads "7× #1 · last held
  Apr 12", not a vanished badge.
- **Current standing is still live.** `current_rank` comes from `get_player_ship_badges` (latest
  `captured_on`), NOT the ledger — the ledger is history, the snapshot is "now".

## Storage shape

### `ShipAward` (new model, migration `0062`)

| Field | Type | Notes |
|---|---|---|
| `captured_on` | `DateField(db_index)` | The snapshot run date this placement was earned. |
| `realm` | `CharField(choices=REALM_CHOICES)` | |
| `ship_id` | `BigIntegerField(db_index)` | |
| `ship_name` | `CharField` | Denormalized (stable per ship). |
| `rank` | `IntegerField` | 1..`SHIP_BADGE_TOP_N`. |
| `player` | `FK(Player, related_name='ship_awards')` | |
| `created_at` | `DateTimeField(auto_now_add)` | |

- `UniqueConstraint(captured_on, realm, ship_id, rank, name='unique_ship_award_per_rank')` — one player
  per placement per run; makes same-day re-runs idempotent (mirrors the snapshot's constraint).
- `Index(player, ship_id, name='ship_award_player_ship_idx')` — the per-player career aggregate read.
- Additive `CreateModel` — cloud-DB-safe.

## Write path (`data.compute_ship_top_player_snapshot`)

In the existing per-ship loop, when `rank <= top_n` (already the badge branch), also collect a ledger
row:
```python
award_rows.append(ShipAward(
    captured_on=today, realm=realm, ship_id=ship_id,
    ship_name=ship_names.get(ship_id) or entry['player__name'] or '',
    rank=rank, player_id=entry['player_id']))
```
In the existing `transaction.atomic()` block, alongside the snapshot delete/bulk_create, append the
ledger **idempotently for today only** — and **never prune** it:
```python
ShipAward.objects.filter(realm=realm, captured_on=today).delete()  # idempotent re-run
if award_rows:
    ShipAward.objects.bulk_create(award_rows)
# NOTE: no retention delete — the ledger is the durable record.
```
This means a same-day re-run replaces today's awards (no inflation); past days are untouched and
permanent. Accretion is ~1 row-set per ship per **week** (the schedule cadence).

## Read path (`data.get_player_ship_awards(player) -> list`)

Sibling to `get_player_ship_badges`. Aggregate the ledger per ship, then graft the live current rank:
```python
from warships.models import ShipAward
rows = list(
    ShipAward.objects.filter(player=player)
    .values('ship_id')
    .annotate(
        ship_name=Max('ship_name'),
        times_first=Count('id', filter=Q(rank=1)),
        times_top3=Count('id'),
        best_rank=Min('rank'),
        first_on=Min('captured_on'),
        last_on=Max('captured_on'),
    )
    .order_by('-times_first', 'best_rank', '-times_top3'))
if not rows:
    return []
current = {b['ship_id']: b['rank'] for b in get_player_ship_badges(player)}
return [{
    'ship_id': r['ship_id'], 'ship_name': r['ship_name'],
    'times_first': r['times_first'], 'times_top3': r['times_top3'],
    'best_rank': r['best_rank'], 'current_rank': current.get(r['ship_id']),
    'first_on': r['first_on'].isoformat() if r['first_on'] else None,
    'last_on': r['last_on'].isoformat() if r['last_on'] else None,
} for r in rows]
```
- Group by `ship_id` only; `ship_name = Max('ship_name')` resolves the (stable) name without splitting
  rows if a name ever changed.
- Requires `Max, Min` in the `data.py` `django.db.models` import (currently absent — `Count`/`Q` are
  present). Add them.
- `current_rank` is `None` when the player isn't in the latest snapshot's top-3 → the vacation case.

`serializers.PlayerSerializer`: add `ship_awards = serializers.SerializerMethodField()` +
`get_ship_awards(self, obj)` delegating to `get_player_ship_awards(obj)` (mirrors `get_ship_badges`).
> **N+1 note:** like `ship_badges`, this adds one indexed aggregate per serialized player; the bulk
> warmers loop `PlayerSerializer()` over ≤~150 players every 12h — bounded, accepted.

## Frontend

`client/app/components/ShipHonors.tsx` — a "Ship Honors" panel rendered on the player page (below the
live `ShipTopPlayerBanner`, above Battle History). One row per ship in `ship_awards` (cap ~12 + "+N
more"), each:
- medal colored by `best_rank` (gold/silver/bronze) + ship name, linking to `/ship/<id>`.
- `times_first > 0` → "`N×` #1"; else "best #`best_rank`".
- tenure: "`times_top3` windows top-3".
- status: `current_rank != null` → "currently #`current_rank`"; else "last held `<last_on>`".

Add `ship_awards?: ShipAward[]` to the `PlayerDetail` player type. Render only when non-empty and
`!player.is_hidden`.

## Backfill

None. Pre-ledger history wasn't retained, so the ledger accretes from the **first snapshot run after
deploy** (which already writes today's awards → the panel shows "1× #1" immediately). It grows ~1
window/week thereafter. (The current `ShipTopPlayerSnapshot` rows could seed one extra data point but
it isn't worth a special path.)

## Test plan (`server/warships/tests/test_ship_awards.py`, new)

Assertions to pin the load-bearing behavior:
1. **Ledger written on top-3:** run the snapshot over seeded events → `ShipAward` rows exist for the
   top-3 of each qualifying ship, with `rank` 1/2/3 and `captured_on = today`.
2. **Idempotent same-day re-run:** run twice on the same date → award count is unchanged (delete-today
   + re-append), no duplicates.
3. **Append-only across dates:** create a prior-date `ShipAward` row directly, run the snapshot today →
   the prior-date row **survives** (not pruned), and `get_player_ship_awards` shows `times_first`
   incremented across the two dates.
4. **Aggregation correctness:** direct ledger rows for one player/ship across 3 dates (ranks 1,1,2) →
   `get_player_ship_awards` returns `times_first=2`, `times_top3=3`, `best_rank=1`, `first_on`/`last_on`
   = min/max dates.
5. **Vacation case (current_rank None):** player has ledger rows but is NOT in the latest snapshot →
   `current_rank is None`, `last_on` = their last award date. A player currently #1 → `current_rank=1`.
6. **Payload contract** (`test_views.py`): a player with awards exposes `ship_awards` with the documented
   keys; an unbadged player → `[]`.

Frontend (`ShipHonors` via `PlayerDetail.test.tsx` or a focused test): renders "N× #1" + current/last
status from `ship_awards`; renders nothing when empty.

## Rollout

1. Ship code + migration `0062` (additive). Apply to the cloud DB (same pattern as `0060`/`0061`).
2. Re-run `compute_ship_top_player_snapshot` for na/eu/asia so the ledger gets its first row-set
   (today's awards) — the panel is otherwise empty until the next weekly run.
3. `feat:` → minor. Client + backend deploy (payload shape changed) when ready.

## Validation results

**Local (2026-06-05):** `test_ship_awards.py` (7: ledger-on-top3, idempotent same-day, append-only
across dates, career aggregate, vacation→current_rank None, current_rank from latest snapshot, empty)
+ `test_views.py` `ship_awards` payload + `[]` cases; full backend gate → **291 passed**;
`manage.py check` clean. Frontend `PlayerDetail.test.tsx` (3 honors: career record, vacation
"last held", empty) → **36 passed**; lint + build + TypeScript clean.

**Prod first-run (pending):** apply `0062` to the cloud DB, re-run the snapshot for na/eu/asia so the
ledger gets its first row-set, then record a sample profile's `ship_awards`.

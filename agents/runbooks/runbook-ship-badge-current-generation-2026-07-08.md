# Runbook: ship badge stale after displacement → anchor on current generation (2026-07-08)

**Status:** SHIPPED
**Area:** ship standings / profile badges (`data.get_player_ship_badges`, `get_players_ship_badges_bulk`)
**Trigger:** user report — `/ship/3752736720-cossack?realm=na` showed **piperpa42** as top Cossack, while **castorice_my_beloved**'s profile still wore the "Cossack 1st place" badge.

## Symptom

Two surfaces disagreed on who holds a ship's #1:
- `/ship/<id>` leaderboard (`ship_leaderboard`): current #1 = piperpa42.
- Profile badge (`ship_badges`): castorice still showed Cossack rank 1 (94.74% WR, 19 battles).

## Diagnosis

Both read the same table (`ShipTopPlayerSnapshot`), but with **different anchors**:
- The board reads the ship's **current generation** (`latest_ship_snapshot_window` → realm's latest `captured_on`; on the reported day, `2026-07-08`).
- `get_player_ship_badges` read the **player's own** latest `captured_on`.

DB confirmed: castorice held Cossack #1 in generations `07-03…07-07`, then was **absent** from the `07-08` generation (their thin 19-battle run aged out of the sliding 30d window, dropping below the listing floor). Their overall latest row stayed `07-07`, so the badge read served that stale rank-1 row. Rows are retained `SHIP_BADGE_RETENTION_DAYS=5`, so a dethroned #1 wore a stale badge for up to 5 days. The nightly writer's "invalidate previous top-N holders" safety net (`data.py:6377`) only refreshed the cache; the read logic re-served the same stale row.

## Fix (this branch)

Anchor both badge read paths on the **realm's current generation** (the same `captured_on` the board uses), not the player's own latest:
- `get_player_ship_badges`: `latest, _, _ = latest_ship_snapshot_window(player.realm)`.
- `get_players_ship_badges_bulk`: derive the realms in play from the candidates, compute each realm's current `captured_on` over **all** rows (not restricted to candidates, else a table full of dropped players drags the anchor back), match rows via a per-realm `Q(realm, captured_on)`; keep `player__is_hidden=False` on the final row query.

A player absent from the current generation now shows **no badge** — display tracks the live board; the `RETENTION_DAYS` grace no longer leaks into badge display.

Regression tests (`test_ship_badges.py`): `test_badge_dropped_when_absent_from_current_generation`, `test_badge_dropped_when_player_absent_from_all_current_boards`. Both fail on the pre-fix (player-latest) logic and pass after — they pin the exact failure so a refactor back to per-player `Max(captured_on)` re-breaks the build.

## Post-deploy: clear already-stale badges (REQUIRED)

The read fix alone won't clear warm cached payloads. The nightly writer only invalidates the *previous* generation's top-N holders, so every currently-displaced ex-#1 keeps a stale cached badge until their detail cache next churns. One-off sweep on the droplet after the backend deploy:

```
# For each realm, invalidate detail cache for players holding a badge-tier rank<=top_n
# row whose captured_on != that realm's current generation.
from warships.models import ShipTopPlayerSnapshot as S
from warships.data import latest_ship_snapshot_window, invalidate_player_detail_cache, _badge_tiers
top_n = int(os.getenv('SHIP_BADGE_TOP_N','3')); eligible = _badge_tiers()
for realm in ('na','eu','asia'):
    cur,_,_ = latest_ship_snapshot_window(realm)
    if not cur: continue
    stale = (S.objects.filter(realm=realm, rank__lte=top_n)
             .exclude(captured_on=cur)
             .values_list('player__player_id','ship_id').distinct())
    # filter to badge-eligible tiers via Ship, then invalidate each wg_id
```

Loop-closer for the reported case: `invalidate_player_detail_cache(1040379275, realm='na')`, then confirm `ship_badges == []` on the live API for castorice.

## Related
- `runbook-ship-badges-rolling-2026-06-14.md` (nightly rolling model)
- `runbook-shipleaderboard-warm-before-evict-2026-06-18.md` (board cache)

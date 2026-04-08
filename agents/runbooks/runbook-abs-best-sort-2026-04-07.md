# Runbook: ABS (Absolute Best) Sub-Sort

**Status:** Implemented 2026-04-07
**Owner:** august
**Surfaces:** Landing page → Best Players strip, Best Clans strip

## Why this exists

Every existing Best sub-sort applies some flavor of penalty:

- `wr` requires 2,500 PvP battles, 500 high-tier battles, ≤180 days idle, and uses high-tier-only WR.
- `overall` blends WR with score, efficiency, volume, ranked, and clan multipliers.
- Clan `wr` adds a CB lift weighted by member score, active members, and CB sample size.

Users wanted a "no asterisks" view: just show the highest pure win rate on the realm. ABS is that view. It is intentionally complementary to `wr` — comparing the two surfaces players/clans the WR sort filters out for low high-tier volume or stale recency.

## Sanity floors

ABS is not "no filters" — those would surface 99%-WR-over-3-battles noise. The minimum floors are:

| Entity | Filter | Value |
|---|---|---|
| Player | not hidden | required |
| Player | `pvp_battles >` | 100 |
| Player | sort key | overall `pvp_ratio`, then `pvp_battles`, then name |
| Clan | excluded clan ids | respected |
| Clan | `members_count >` | 0 |
| Clan | `cached_total_battles >=` | 1,000 |
| Clan | sort key | `cached_clan_wr`, then `cached_total_battles`, then name |

The 100-battle player floor and 1,000-battle clan floor are the minimum that keeps the leaderboard meaningful. They are deliberately documented in the in-app tooltips so users know what they're looking at.

## Files touched

| File | Change |
|---|---|
| `server/warships/landing.py` | Added `'abs'` to `LANDING_PLAYER_BEST_SORTS` and `LANDING_CLAN_BEST_SORTS`; new constant `LANDING_PLAYER_BEST_ABS_MIN_PVP_BATTLES = 100`; new builder `_build_best_abs_landing_players`; dispatch branch in `_build_best_landing_player_snapshot_payload`; `apply_recency_cap` kwarg on `_best_landing_player_candidate_rows`; updated normalizer error strings. |
| `server/warships/data.py` | Added `'abs'` to `BEST_CLAN_SORTS`; new constant `BEST_CLAN_ABS_MIN_TOTAL_BATTLES = 1_000`; early-return ABS branch in `score_best_clans` (relaxed query, pure clan_wr ordering). |
| `server/warships/tests/test_landing.py` | Updated normalizer rejection-message assertions; added `test_normalize_landing_*_best_sort_accepts_abs`; added `test_materialize_landing_player_best_snapshot_abs_ignores_recency_and_high_tier_filters`. |
| `client/app/components/PlayerSearch.tsx` | Widened `PlayerBestSort` and `ClanBestSort` types; added `'abs'` to player and clan sort button arrays after `'wr'`; new label branches; new formula constants `PLAYER_BEST_ABS_FORMULA_APPROXIMATION` and `CLAN_BEST_ABS_FORMULA_APPROXIMATION`; added ABS sections to both ranking-formula tooltips; updated clan tooltip header copy to note ABS bypasses the hard filters. |
| `agents/runbooks/runbook-abs-best-sort-2026-04-07.md` | This runbook. |

No model changes — `LandingPlayerBestSnapshot` is generic on `(realm, sort)`. No cache key format changes — sort name is interpolated. No namespace bump.

## Deploy + populate

```bash
# 1. Backend tests
cd server && python -m pytest warships/tests/test_landing.py -x --tb=short

# 2. Backend deploy
./server/deploy/deploy_to_droplet.sh battlestats.online

# 3. SSH to droplet, materialize ABS player snapshots for both realms
ssh root@battlestats.online '
  cd /opt/battlestats-server/current/server &&
  set -a && source .env && source .env.secrets && set +a &&
  python manage.py materialize_landing_player_best_snapshots --realm na --sort abs &&
  python manage.py materialize_landing_player_best_snapshots --realm eu --sort abs
'

# 4. Force-warm landing caches so first request is hot
ssh root@battlestats.online '
  cd /opt/battlestats-server/current/server &&
  set -a && source .env && source .env.secrets && set +a &&
  python manage.py run_post_deploy_operations warm-landing --force-refresh
'

# 5. Frontend tests + deploy
cd client && npm test
./client/deploy/deploy_to_droplet.sh battlestats.online
```

Clans have no snapshot table — the per-sort cache is built on-demand by `score_best_clans` and then warmed by `warm_landing_page_content`, so the warm-landing step in step 4 covers them.

## Verification

```bash
# Player ABS — should return 25 entries with the realm's highest pvp_ratio first
curl -s "https://battlestats.online/api/landing/players/?mode=best&sort=abs&realm=na&limit=25" \
  | jq '.players | map(.name) | .[0:5]'

# Clan ABS — should rank by clan_wr alone, with a different leader than wr
curl -s "https://battlestats.online/api/landing/clans/?mode=best&sort=abs&realm=na" \
  | jq '.clans | map({name, clan_wr}) | .[0:5]'

# Compare to wr sort — ABS should surface entities WR filters out
curl -s "https://battlestats.online/api/landing/players/?mode=best&sort=wr&realm=na" \
  | jq '.players | map(.name) | .[0:5]'
```

Browser smoke test on `https://battlestats.online/?realm=na`:

1. Click **Best** under Active Players → ABS button appears between WR and CB.
2. Click **Best** under Active Clans → ABS button appears after WR.
3. Hover the ⓘ tooltip on each strip → ABS section renders with formula and "no penalties" copy.
4. Selecting ABS reorders the chart and grid to the pure-WR ordering.

## Rollback

Revert the two `LANDING_*_BEST_SORTS` tuple entries (`landing.py:90,92`) and the `BEST_CLAN_SORTS` entry (`data.py:5240`). The frontend buttons will keep rendering until the client deploy is reverted; clicking ABS after backend rollback returns a 400 from the normalizer. Cached `abs` snapshots in `LandingPlayerBestSnapshot` are harmless leftovers and can be deleted with:

```sql
DELETE FROM warships_landingplayerbestsnapshot WHERE sort = 'abs';
```

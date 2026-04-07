# Incident Runbook: DO Function Enrichment Writing Empty Data

_Created: 2026-04-06_
_Status: **Resolved** — data repaired, enrichment moved to droplet_

## Summary

The DigitalOcean Functions enrichment pipeline (`enrichment/enrich-batch`) was writing empty `battles_json = []` for players instead of real ship/battle data. Players marked with empty `battles_json` were excluded from future enrichment passes because the eligibility filter checks `battles_json__isnull=True`. This silently blocked ~91K players across NA and Asia from being enriched.

## Root Cause

Two compounding issues:

### 1. WG API IP Restriction

The Wargaming API validates the calling IP against the registered application's whitelist. The DO Function's egress IP is not on the whitelist, so all data-fetching endpoints return `INVALID_IP_ADDRESS`:

- `ships/stats/` (ship battle data)
- `seasons/accountinfo/` (ranked data)
- `clans/season/` (CB season metadata)
- `clans/seasonstats/` (CB per-player stats)

The `account/info/` endpoint (used for snapshot/activity updates) does NOT enforce IP restrictions, so the enrichment function appeared to succeed — it updated snapshots and logged "Enriched <player>" — but saved no ship data.

The function IP changed on each redeploy (v0.0.5: `167.99.145.160`, v0.0.6: `167.99.145.160`, v0.0.7: `159.203.186.84`), none of which were whitelisted.

### 2. Empty List vs NULL Semantics

When the WG API returns `INVALID_IP_ADDRESS` for `ships/stats/`, the response parser returns an empty list `[]`. The enrichment code saves this empty list to `battles_json`, which makes the field non-NULL. The eligibility query:

```python
Player.objects.filter(battles_json__isnull=True, ...)
```

...skips these players because `[] IS NOT NULL` in PostgreSQL. The players are effectively marked as "enriched" with no data.

## Impact

| Realm | Players with empty `battles_json = []` | Eligible players blocked | Truly enriched (real data) |
|-------|----------------------------------------|--------------------------|---------------------------|
| NA    | 49,542                                 | 49,541                   | 35,148                    |
| EU    | 0                                      | 0                        | 50,547                    |
| Asia  | 41,497                                 | ~41,000                  | 43                        |

- **NA**: 49,541 eligible players silently blocked. The droplet-based enrichment (v0.0.5 era and prior Celery runs) had enriched 35,148 with real data. The DO Function produced 49,542 empty records.
- **Asia**: Nearly all 41,497 "enriched" players had empty data. Only 43 had real ship records (likely from on-demand player page visits hitting the droplet).
- **EU**: Not affected — EU enrichment completed before the DO Function was introduced.

### Downstream effects

- **Tier-type correlation heatmap**: Asia showed 0 tracked population (requires `battles_json` ship data)
- **Ranked WR-battles heatmap**: Asia showed only 63 players (requires `ranked_json` from enrichment)
- **Player detail charts**: Charts for "enriched" players showed empty battle data sections

## Timeline

| Time | Event |
|------|-------|
| 2026-04-04 | DO Functions enrichment pipeline deployed (v0.0.5), targeting EU then Asia |
| 2026-04-05 | Asia enrichment started on DO Function. IP `167.99.145.160` not whitelisted for WG API data endpoints. Function appears to succeed, writes empty `battles_json = []` |
| 2026-04-06 03:15 | v0.0.5 still running on 15-min cron, writing empty data for Asia |
| 2026-04-06 03:23 | Redeployed as v0.0.6 with `ENRICH_REALMS=na,eu,asia`. New IP also not whitelisted. NA enrichment attempts timeout at 900s due to 6s/player Celery broker connection failure penalty |
| 2026-04-06 03:55 | Redeployed as v0.0.7 with `ENRICH_REALMS=asia`. Same IP issue |
| 2026-04-06 ~19:45 | Root cause identified: `INVALID_IP_ADDRESS` on all data endpoints |
| 2026-04-06 ~19:50 | DO Function cron disabled (removed from droplet crontab) |
| 2026-04-06 ~19:55 | Asia: 41,497 empty `battles_json` reset to NULL |
| 2026-04-06 ~20:00 | NA enrichment launched on droplet (4,785 players, completed successfully) |
| 2026-04-06 ~20:10 | NA: 49,542 empty `battles_json` reset to NULL |
| 2026-04-06 ~22:10 | Asia enrichment launched on droplet (~92K eligible) |

## Remediation Steps Taken

### 1. Disabled DO Function cron

```bash
ssh root@battlestats.online "crontab -r"
```

### 2. Reset empty `battles_json` to NULL

```sql
UPDATE warships_player SET battles_json = NULL WHERE battles_json = '[]'::jsonb;
```

Per-realm results: NA 49,542 reset, Asia 41,497 reset (+ 151 from ongoing), EU 0.

### 3. Launched enrichment from droplet

The droplet's IP is whitelisted with the WG API. Enrichment runs via the management command:

```bash
# NA (completed: 4,785 players, 0 errors)
nohup python manage.py enrich_player_data --realm na --batch 6000 > /tmp/na-enrich.log 2>&1 &

# Asia (in progress: ~92K eligible)
nohup python manage.py enrich_player_data --realm asia --batch 92500 --delay 0.05 > /tmp/asia-enrich.log 2>&1 &
```

### 4. Post-enrichment state (2026-04-06 22:30 UTC)

| Realm | Truly Enriched | Eligible | Total |
|-------|----------------|----------|-------|
| NA    | 35,148         | 49,541   | 281,439 |
| EU    | 50,547         | 99,431   | 481,903 |
| Asia  | 466            | 91,401   | 257,176 |

NA enrichment from the droplet completed (4,785 new). Asia enrichment running on droplet. EU has 99K eligible — these are lower-priority players below the original enrichment thresholds.

## Prevention

### Short-term

- All enrichment runs from the droplet via management command or Celery task
- DO Function cron remains disabled

### Long-term options

1. **Whitelist DO Function IPs**: Add the DO Functions egress IP range to the WG API application settings. Problem: IPs change on each function redeploy.
2. **Validate enrichment output**: Add a check in the enrichment code that rejects empty `battles_json` — don't save `[]`, leave it as NULL so the player remains eligible:

```python
# In _enrich_player_parallel or update_battle_data:
if not battle_data:  # empty list from API failure
    # Don't write empty list — leave battles_json as NULL
    return
```

3. **Use droplet as enrichment proxy**: Route DO Function API calls through the droplet (adds complexity, likely not worth it).
4. **Enrichment health check**: Add monitoring that compares "enriched count" (battles_json IS NOT NULL) against "enriched with data" (battles_json has records). Alert on divergence.

### Recommended fix

Option 2 is the simplest and most robust. The enrichment code in `enrich_player_data.py` should validate that `update_battle_data` actually produced non-empty results before marking the player as enriched. This prevents the empty-list problem regardless of the API failure mode.

## Detection

This issue was invisible to the enrichment logs — each player was logged as "Enriched" with correct WR/battles stats (from the core crawl data). The only signals were:

- Tier-type correlation showing 0 population for Asia
- `battles_json` having 84K non-NULL entries in NA but 0 with the expected `pvp` key structure
- DO Function activation logs showing `INVALID_IP_ADDRESS` errors (not surfaced to any dashboard)

## Related

- `runbook-enrichment-crawler-2026-04-03.md` — enrichment pipeline architecture
- `runbook-asia-realm-data-load-2026-04-05.md` — Asia load plan (Phase 3 enrichment)
- `spec-serverless-background-workers-2026-04-04.md` — DO Functions architecture

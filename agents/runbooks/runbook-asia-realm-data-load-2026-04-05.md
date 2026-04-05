# Runbook: Asia Realm Data Load

_Created: 2026-04-05_
_Status: **Ready to execute** — code changes complete, awaiting commit + deploy_

## Purpose

Bring the Asia realm online from scratch. Populate clans and players via an initial clan crawl, then backfill `battles_json` via the DO Functions enrichment pipeline. All other periodic tasks are suspended to focus resources on the Asia load.

## Prerequisites

| Condition | Status |
|-----------|--------|
| NA enrichment backfill | Complete (79,255 enriched, 1 eligible remaining) |
| EU enrichment backfill | Complete (139,584 enriched, 0 eligible remaining) |
| DO Functions enrichment cron | Firing every 15 min, 0 candidates (no-ops) |
| Celery Beat periodic tasks | All 23 suspended since NA-only mode |
| Asia data in database | None — zero clans, zero players |

## Language Policy

No i18n or localization changes. The entire UI — navigation, charts, labels, headings, buttons — stays in English. Player names, clan names, and clan tags from the Asia realm will display in their native script (Japanese, Korean, Chinese, Thai, etc.) because they are Unicode strings returned by the WG API and rendered as-is. No translation layer, no character normalization, no locale switching. The `encodeURIComponent` used in URL routing handles CJK characters correctly. Clan URL slugs use numeric `clan_id` as the routing key, so non-ASCII clan names in the cosmetic slug portion are harmless.

## Estimated Asia Population

| Entity | Estimated Count |
|--------|----------------|
| Clans | ~21,000-22,000 |
| Players (clan members) | ~430,000-520,000 |
| Eligible for enrichment (500+ battles, 48%+ WR) | ~50,000-80,000 |

## Code Changes

All changes are committed and QA-verified. No remaining hardcoded `['na', 'eu']` references in the codebase.

### Backend

| File | Change |
|------|--------|
| `server/warships/models.py:5` | Added `('asia', 'ASIA')` to `REALM_CHOICES` |
| `server/warships/api/client.py:20` | Added `'asia': 'https://api.worldofwarships.asia/wows/'` to `REALM_BASE_URLS` |
| `server/warships/migrations/0040_add_asia_realm.py` | Choices-only migration for Player, Clan, LandingPlayerBestSnapshot, EntityVisitEvent, EntityVisitDaily |
| `server/warships/management/commands/run_clan_crawl.py` | New management command wrapping `clan_crawl.run_clan_crawl()` for direct SSH execution |
| `server/warships/tests/test_realm_isolation.py` | Added Asia base URL routing test, Asia view extraction test |

### Frontend

| File | Change |
|------|--------|
| `client/app/context/RealmContext.tsx:5,7` | Extended `Realm` type and `VALID_REALMS` to include `'asia'` |
| `client/app/components/RealmSelector.tsx:16` | Added `{ value: 'asia', label: 'ASIA' }` to realm dropdown |
| `client/app/layout.tsx:48` | Updated FOUC realm validation from `['na','eu']` to `['na','eu','asia']` |
| `client/app/player/[playerName]/page.tsx:20` | Updated `generateMetadata` realm validation to include `'asia'` |
| `client/app/clan/[clanSlug]/page.tsx:20` | Updated `generateMetadata` realm validation to include `'asia'` |
| `client/app/sitemap.ts:27` | Updated sitemap realm list to include `'asia'` |

### Infrastructure

| File | Change |
|------|--------|
| `functions/.env:9` | Changed `ENRICH_REALMS` from `eu` to `asia` |

### What does NOT change

- No language/locale files — none exist, none added
- No translation system — all UI text stays English
- No character normalization — CJK names render as-is from the WG API
- No new fonts or font stacks — existing fonts support CJK via system fallback
- No changes to chart rendering (D3), theme system, or CSS

## Execution Checklist

### Phase 1: Commit and Deploy

- [ ] **1.1** Commit all code changes
- [ ] **1.2** Deploy backend: `./server/deploy/deploy_to_droplet.sh battlestats.online`
  - Migration `0040_add_asia_realm` runs automatically
  - `post_migrate` signal auto-creates 8 Asia periodic tasks (landing warmer, hot entity warmer, distributions, correlations, bulk cache loader, recently-viewed warmer, best-player snapshot materializer, clan tier dist warmer)
- [ ] **1.3** Suspend ALL periodic tasks immediately after deploy (the `post_migrate` signal re-enables them):

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  source /opt/battlestats-server/venv/bin/activate && \
  set -a && source .env && source .env.secrets && set +a && \
  python -c \"
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'battlestats.settings'
django.setup()
from django_celery_beat.models import PeriodicTask
count = PeriodicTask.objects.filter(enabled=True).update(enabled=False)
print(f'Suspended {count} periodic tasks')
\""
```

- [ ] **1.4** Deploy frontend: `./client/deploy/deploy_to_droplet.sh battlestats.online`
  - Realm selector now shows NA / EU / ASIA
  - ASIA landing page will be empty until clan crawl completes (expected)
- [ ] **1.5** Verify realm selector is visible and shows ASIA option: visit `https://battlestats.online/`, click the globe icon, confirm three options appear

### Phase 2: Initial Clan Crawl

- [ ] **2.1** SSH to droplet and open a tmux session:

```bash
ssh root@battlestats.online
tmux new -s asia-crawl
cd /opt/battlestats-server/current/server
source /opt/battlestats-server/venv/bin/activate
set -a && source .env && source .env.secrets && set +a
```

- [ ] **2.2** Dry-run to validate Asia API endpoint and discover clan count:

```bash
python manage.py run_clan_crawl --realm asia --dry-run
```

Expected output: JSON with `"clans_found": 21000+`, `"dry_run": true`. If this fails with an API error, stop and investigate before proceeding.

- [ ] **2.3** Run full clan crawl:

```bash
python manage.py run_clan_crawl --realm asia --core-only 2>&1 | tee /tmp/asia-crawl.log
```

This runs directly on the droplet (not through Celery) to avoid OOM and warmer starvation. `core_only=True` saves only core player stats from `account/info` — no ships/stats or ranked data.

| Parameter | Value |
|-----------|-------|
| Rate limit delay | 0.25s (default) |
| API calls per clan | ~3-4 |
| Estimated duration | 4-6 hours |
| Memory usage | ~200-400 MB |

Optional: reduce delay if no 429 errors seen:

```bash
CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY=0.15 python manage.py run_clan_crawl --realm asia --core-only 2>&1 | tee /tmp/asia-crawl.log
```

If interrupted, resume with `--resume` (skips clans already saved):

```bash
python manage.py run_clan_crawl --realm asia --core-only --resume 2>&1 | tee -a /tmp/asia-crawl.log
```

- [ ] **2.4** Post-crawl verification:

```bash
python manage.py shell -c "
from warships.models import Player, Clan
p = Player.objects.filter(realm='asia').count()
c = Clan.objects.filter(realm='asia').count()
eligible = Player.objects.filter(realm='asia', is_hidden=False, pvp_battles__gte=500, pvp_ratio__gte=48.0, battles_json__isnull=True).exclude(name='').count()
print(f'Asia clans: {c:,}')
print(f'Asia players: {p:,}')
print(f'Eligible for enrichment: {eligible:,}')
"
```

Expected: ~21K+ clans, ~430K-520K players, 50K-80K eligible for enrichment, 0 with `battles_json`.

### Phase 3: Enrichment Backfill

- [ ] **3.1** Deploy DO Function with Asia config:

```bash
./functions/deploy.sh --include enrichment/enrich-batch
```

The `functions/.env` already has `ENRICH_REALMS=asia`. This deploys the updated server code (with Asia realm support) into the function package.

- [ ] **3.2** Verify first enrichment invocation succeeds:

```bash
# Wait for next cron cycle (fires every 15 min), then:
doctl serverless activations list --limit 4
# Check that activations show status=success and total_enriched > 0:
doctl serverless activations result <activation-id>
```

- [ ] **3.3** Monitor progress periodically:

```bash
ssh root@battlestats.online "set -a && source /etc/battlestats-server.env && \
  source /etc/battlestats-server.secrets.env && set +a && \
  PGPASSWORD=\$DB_PASSWORD psql -h \$DB_HOST -U \$DB_USER -d \$DB_NAME \
  -p \${DB_PORT:-25060} --set=sslmode=require -t -c \
  \"SELECT COUNT(battles_json) || ' enriched / ' || COUNT(*) || ' total (' || \
  ROUND(100.0 * COUNT(battles_json) / NULLIF(COUNT(*), 0), 1) || '%)' \
  FROM warships_player WHERE realm = 'asia';\""
```

Throughput: ~8,000-10,000 players/hour. Estimated 6-10 hours for the eligible pool.

### Phase 4: Post-Load Restoration

Once enrichment reaches ~25%+ or completes:

- [ ] **4.1** Re-enable ALL periodic tasks (all realms):

```bash
ssh root@battlestats.online "cd /opt/battlestats-server/current/server && \
  source /opt/battlestats-server/venv/bin/activate && \
  set -a && source .env && source .env.secrets && set +a && \
  python -c \"
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'battlestats.settings'
django.setup()
from django_celery_beat.models import PeriodicTask
count = PeriodicTask.objects.filter(enabled=False).update(enabled=True)
print(f'Re-enabled {count} periodic tasks')
\""
```

- [ ] **4.2** Run post-deploy operations for Asia:

```bash
./scripts/post_deploy_operations.sh battlestats.online snapshots --realm asia
./scripts/post_deploy_operations.sh battlestats.online warm-landing --realm asia
./scripts/post_deploy_operations.sh battlestats.online warm-best-entities --realm asia
```

- [ ] **4.3** Update `functions/.env` for steady-state enrichment:

```
ENRICH_REALMS=na,eu,asia
```

Then redeploy: `./functions/deploy.sh --include enrichment/enrich-batch`

- [ ] **4.4** Verify Asia landing page:
  1. Visit `https://battlestats.online/`, select ASIA realm
  2. Confirm landing page shows players, clans, distributions
  3. Click into a player detail page — confirm charts load, name renders in native script
  4. Click into a clan detail page — confirm member list loads

### Phase 5: Documentation Updates

- [ ] **5.1** Update `runbook-daily-data-refresh-schedule-2026-04-05.md` with Asia backfill totals
- [ ] **5.2** Update `runbook-enrichment-crawler-2026-04-03.md` running totals with Asia numbers
- [ ] **5.3** Add this runbook to `agents/doc_registry.json`
- [ ] **5.4** Add this runbook to `agents/runbooks/README.md`
- [ ] **5.5** Archive this runbook once Asia is fully operational and steady-state

## Risk Mitigations

| Risk | Mitigation |
|------|------------|
| OOM during clan crawl | Management command bypasses Celery; all periodic tasks suspended; `core_only=True` minimizes memory |
| SSH disconnect during crawl | tmux session; `--resume` flag to continue from last saved clan |
| Asia API rate limit (429) | Configurable delay via `CLAN_CRAWL_CORE_ONLY_RATE_LIMIT_DELAY`; start at 0.25s |
| `max_length=4` too short for 'asia' | Verified: exactly 4 characters, fits all CharField(max_length=4) fields |
| `post_migrate` re-enables tasks on deploy | Step 1.3 re-suspends immediately after each deploy |
| Frontend shows empty Asia realm | Expected during Phases 2-3; landing page handles empty data gracefully |
| Enrichment targets wrong realm | Step 3.2 verifies first activation shows asia realm and non-zero enrichment |
| CJK player/clan names break routing | Verified: `encodeURIComponent` handles Unicode; clan routing uses numeric `clan_id` as key |

## QA Verification Summary

The following was verified during QA:

- All `['na', 'eu']` hardcoded references updated to include `'asia'` across backend and frontend
- `_get_realm()` in views.py uses `VALID_REALMS` (derived from `REALM_CHOICES`) — no hardcoded set
- `get_base_url('asia')` returns `https://api.worldofwarships.asia/wows/`
- All landing page, data.py, and view functions accept `realm` parameter — no Asia-specific logic needed
- `signals.py` already has `REALM_CRAWL_CRON_HOURS['asia'] = 12` — periodic tasks auto-register
- `generateMetadata` in player and clan pages updated to validate `'asia'`
- `sitemap.ts` updated to include `'asia'` realm
- Realm selector dropdown includes ASIA option
- FOUC prevention script in `layout.tsx` validates `'asia'`
- `enrich_player_data.py` uses `VALID_REALMS` for realm validation; `ENRICH_REALMS` env var parsing handles `'asia'`
- `run_post_deploy_operations.py` uses `VALID_REALMS` for `--realm` choices
- No i18n system exists or is needed — UI stays English, player/clan names render in native script

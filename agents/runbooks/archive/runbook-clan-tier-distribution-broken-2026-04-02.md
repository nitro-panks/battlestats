# Runbook: Clan Tier Distribution Chart — Broken on Production

**Status:** Fixed and deployed (2026-04-02)
**Date:** 2026-04-02
**Severity:** P1 — Feature is 100% non-functional on production
**Surface:** Clan detail page tier distribution bar chart

---

## Symptoms

- Clan tier distribution chart never renders on any clan page
- API endpoint `/api/fetch/clan_tiers/<clan_id>/` always returns `[]` with `X-Clan-Tiers-Pending: true`
- Celery task dispatches but immediately crashes with `FieldError`
- No error state or loading indicator visible to users — the chart section silently fails

## Playwright Diagnostic Results

Tested 10 random NA clans from the `best` landing mode. **10/10 failed identically.**

| Clan | API Status | Pending | Tier Bars | Error State |
|------|-----------|---------|-----------|-------------|
| [MIO] MOE! MOE! KYUN! | 200 | true | 0 | none |
| [RESIN] I can't push… | 200 | true | 0 | none |
| [HYPE] No Sleep and High… | 200 | true | 0 | none |
| [SCCC] Seal Cub Clubbing Club | 200 | true | 0 | none |
| [-WD-] Well Done! | 200 | true | 0 | none |
| [KITED] Knives In Torpedo… | 200 | true | 0 | none |
| [KTR] In cherished memory… | 200 | true | 0 | none |
| [RBMK] Mistakes. Were. Made. | 200 | true | 0 | none |
| [KSC] Kill Steal Confirmed | 200 | true | 0 | none |
| [3X] Triple Strike | 200 | true | 0 | none |

EU clans could not be tested — the EU `best` landing mode returns an empty list (separate issue, likely EU `score_best_clans()` has not been warmed yet for the EU realm).

---

## Root Cause Analysis

### Bug 1 (CRITICAL): Wrong field name in `update_clan_tier_distribution`

**File:** `server/warships/data.py:4206`

```python
players = Player.objects.filter(
    clan__clan_id=clan_id, realm=realm, is_hidden=False
).values_list('account_id', 'tiers_json')  # <-- BUG: 'account_id' does not exist
```

The Player model field is `player_id`, not `account_id`. The query crashes on every invocation:

```
django.core.exceptions.FieldError: Cannot resolve keyword 'account_id' into field.
Choices are: ... player_id ...
```

**Evidence:** Production Celery logs (`journalctl -u battlestats-celery`) show this exact error repeating for every dispatched task.

**Fix:** Change `'account_id'` to `'player_id'` in the `values_list()` call, and update the loop variable and downstream `update_tiers_data_task` dispatch accordingly. **Fixed in commit `0ed8000`.**

### Bug 1b (CRITICAL): Nonexistent module import

**File:** `server/warships/data.py:4208`

```python
from warships.utils import _delay_task_safely  # warships.utils does not exist
```

This import was never reached because Bug 1 crashed first. Once Bug 1 was fixed, this became the next crash point. `_delay_task_safely` lives in `warships.views`, but importing from views into data creates circular import risk. **Fixed by replacing with direct `.delay()` call** (dedup is already handled at the view layer). **Fixed in commit `642c859`.**

### Bug 2 (MODERATE): No proactive cache warming

The clan tier distribution is **not integrated into any warming pipeline**:

- `warm_clan_entity_caches()` (called by `warm_hot_entity_caches_task` every 30 min) warms clan data, members, battle seasons, and plot data — but **does not call `update_clan_tier_distribution`**
- No periodic task is registered in `signals.py` for tier distribution warming
- The manual backfill script (`scripts/warm_clan_tiers.py`) exists but is not automated
- **Impact:** Even after Bug 1 is fixed, the cache will only be populated on first user visit, guaranteeing a cold-cache skeleton loader experience

### Bug 3 (MINOR): EU best clans landing returns empty

`/api/landing/clans/?mode=best&realm=eu` returns `[]`. This means:

- EU clans cannot be tested via the landing mode discovery path
- The Playwright test only got NA clans
- Likely the EU `score_best_clans()` cache has not been warmed, or the EU clan crawl hasn't populated enough clans with the required fields

---

## Fix Plan

### Step 1: Fix the FieldError (Bug 1)

In `server/warships/data.py`, line 4206:

```python
# Before
).values_list('account_id', 'tiers_json')

# After
).values_list('player_id', 'tiers_json')
```

Also update the loop variable on line 4211:
```python
# Before
for account_id, tiers_json in players:
    if not tiers_json:
        _delay_task_safely(update_tiers_data_task, player_id=account_id, realm=realm)

# After
for player_id, tiers_json in players:
    if not tiers_json:
        _delay_task_safely(update_tiers_data_task, player_id=player_id, realm=realm)
```

### Step 2: Integrate into warming pipeline (Bug 2)

Add `update_clan_tier_distribution(clan_id, realm)` call to `warm_clan_entity_caches()` in `data.py`, after the existing clan plot warming calls. This ensures hot/best clans get tier distribution data pre-cached every 30 minutes.

### Step 3: Deploy and validate

1. Deploy the server with the fix
2. Restart Celery workers to pick up new code
3. Manually trigger a tier distribution for one clan to verify
4. Run the Playwright diagnostic test to confirm chart renders
5. Monitor Celery logs for any remaining errors

### Step 4: Address EU best clans (Bug 3)

Investigate separately — this is a landing page warming issue, not specific to tier distribution.

---

## Deploy Considerations

Per `runbook-client-droplet-deploy.md`, the deploy process runs `npm run build` on the production droplet, which starves live services of CPU. However, this fix is **server-side only** — no client deploy is needed. The fix requires:

1. Server deploy: `./server/deploy/deploy_to_droplet.sh battlestats.online`
2. Celery restart: `systemctl restart battlestats-celery battlestats-celery-background`
3. No client rebuild needed — the frontend component (`ClanTierDistributionSVG.tsx`) is already deployed and correct; it's the backend task that crashes

The deploy runbook also documents prior issues with the clan tier distribution feature (Root Cause 1: React hook double-instantiation, Root Cause 2: caching empty datasets). The current Bug 1 is a **new, distinct issue** — a field name mismatch that was likely introduced when the Player model was refactored from `account_id` to `player_id`, or the spec used `account_id` (WG API field name) instead of the Django model field name `player_id`.

---

## Validation

After fix deployment, run:

```bash
cd client && PLAYWRIGHT_EXTERNAL_BASE_URL=https://battlestats.online \
  npx playwright test e2e/clan-tier-distribution-live.spec.ts --reporter=list
```

Expected: 11 tier bars visible per clan, no pending headers, no error states.

---

## Diagnostic Test

A diagnostic Playwright test was created at `client/e2e/clan-tier-distribution-diagnostic.spec.ts` during this investigation. It tests 10 random clans and produces detailed per-clan diagnostics including API response inspection, DOM state, console errors, and screenshots. It can be removed or kept as a diagnostic tool.

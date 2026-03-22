# Clan Scatterplot Load Failure

## Scope

This runbook covers the clan page scatterplot failure reported on:

- `/clan/1000060384-go-to-horny-jail-2-electric-boogaloo`

The issue is limited to the scatterplot panel rendered by `ClanSVG`. Other clan page panels can still succeed.

## Symptom

The clan page shows `Unable to load clan chart.` almost immediately after load.

## Reproduction

### Client request path

`ClanSVG` fetches:

- `/api/fetch/clan_data/${clanId}:active`

Current implementation:

- `client/app/components/ClanSVG.tsx`

### Direct endpoint checks

The following requests were tested against the reported clan:

```bash
curl -i http://localhost:8888/api/fetch/clan_members/1000060384/
curl -i http://localhost:8888/api/fetch/clan_battle_seasons/1000060384/
curl -i http://localhost:8888/api/fetch/clan_data/1000060384:active
```

Observed results:

- `clan_members`: `200 OK`
- `clan_battle_seasons`: `200 OK`
- `clan_data`: `500 Internal Server Error`

Trailing-slash behavior was also checked:

```bash
curl -i http://localhost:3001/api/fetch/clan_data/1000060384:active/
```

Observed result:

- `308 Permanent Redirect` to `/api/fetch/clan_data/1000060384:active`

Conclusion: the request shape is valid. The failure is not caused by a bad client URL.

## Root Cause

The scatterplot endpoint crashes inside `fetch_clan_plot_data` with a runtime `NameError`:

```text
NameError at /api/fetch/clan_data/1000060384:active
name 'update_clan_data_task' is not defined
```

Exception location from the Django debug page:

- `server/warships/data.py`, inside `fetch_clan_plot_data`

The function references both:

- `update_clan_data_task`
- `update_clan_members_task`

but `server/warships/data.py` only imports these task symbols near the top:

- `update_activity_data_task`
- `update_battle_data_task`
- `update_randoms_data_task`
- `update_snapshot_data_task`
- `update_tiers_data_task`
- `update_type_data_task`

That means the scatterplot read path can crash as soon as it decides a clan refresh or clan-members refresh should be queued.

## Why It Fails Immediately

`ClanSVG` treats any rejected fetch as a hard panel failure and switches to the text fallback:

- `Unable to load clan chart.`

Because the server throws before returning JSON, the fetch rejects immediately and the UI lands in the error state without any visible loading delay.

## Fix

### Minimal fix

Update the task imports in `server/warships/data.py` so `fetch_clan_plot_data` can dispatch the clan refresh tasks it references.

Expected import additions:

- `update_clan_data_task`
- `update_clan_members_task`

Likely patch area:

- `server/warships/data.py`

### Safer follow-up

Add coverage that exercises the scatterplot endpoint when a clan exists and the read path decides it needs queued refresh work.

Good validation targets:

- `server/warships/views.py` clan data endpoint
- `server/warships/data.py` `fetch_clan_plot_data`

## Existing Guardrail That Should Catch This

The smoke test suite already includes a clan-data endpoint case:

- `server/scripts/smoke_test_site_endpoints.py`

Relevant case:

- `clan_data_naumachia` hitting `/api/fetch/clan_data/1000055908:active`

That smoke case is still useful as a status and JSON-shape guardrail for this endpoint.

QA note:

- under the current cache-first clan-plot contract, `clan_data` may validly return `[]` while clan metadata or roster refresh work is queued
- the smoke check should therefore assert `200` plus JSON list shape, not require `min_items=1`

## Validation After Fix

1. Request the scatterplot endpoint directly and confirm `200 OK` with JSON:

```bash
curl -i http://localhost:8888/api/fetch/clan_data/1000060384:active
```

2. Load the clan page and confirm the scatterplot renders instead of `Unable to load clan chart.`

3. Run the site smoke test task or the equivalent endpoint test coverage to confirm the clan-data API stays green.

## Files Involved

- `client/app/components/ClanSVG.tsx`
- `server/warships/data.py`
- `server/warships/views.py`
- `server/scripts/smoke_test_site_endpoints.py`

# Incident Runbook: Bulk Enrichment Poison-Batch False Empties

_Created: 2026-04-07_
_Status: **Resolved 2026-04-07** — fetcher fix deployed, bogus empties reset, 4 partitions running with ratio guard + DQ sampler. Verified via logs ("Poison ship batch — per-player fallback") and healthy batch summaries (`enriched: 498, empty: 0`)._

## Summary

The bulk `ships/stats/` fetcher in `enrich_player_data.py` was silently marking ~138K legitimate players across NA, EU, and Asia as `battles_json = []` (empty, "checked") instead of enriching them. Root cause: when any single account_id in a 100-player bulk batch is invalid, the Wargaming API rejects the **entire batch** with `INVALID_ACCOUNT_ID`, and our fetcher treated the resulting `None` response as "these 100 players genuinely have no ships" — marking all 100 as empty and removing them from the eligible pool.

## Root Cause

### 1. WG bulk endpoint all-or-nothing failure mode

`ships/stats/` with a comma-separated `account_id` list returns:
- `{pid: [ships], pid: [ships], ...}` on success
- `{"status": "error", "error": {"message": "INVALID_ACCOUNT_ID", ...}}` if **any** ID in the batch is malformed/deleted/out-of-range
- The whole request is rejected. There is no partial success.

Legacy/test/deleted account IDs (e.g. `760200`, `760100`, `760103`) sit below the normal WG account_id range (~1,000,000,000+). The candidate pool contains ~6K of these scattered throughout the 800K-player eligible set.

### 2. Bulk fetcher conflated "error" with "empty"

`_bulk_fetch_ship_stats` in `enrich_player_data.py`:

```python
data = make_api_request("ships/stats/", params, realm=realm)
return data if isinstance(data, dict) else {}
```

When `make_api_request` returned `None` (error path), the fetcher returned `{}`. The per-player loop then did `bulk_ship_data.get(pid_str)` → `None` for every player in the chunk → `_process_player_ship_data` hit its "empty → mark as checked" branch → 100 legitimate players stamped `battles_json = []`.

### 3. Compounding factor: `_process_player_ship_data` empty-branch

The fix deployed earlier today (runbook-incident-do-function-empty-enrichment-2026-04-06) restored the else-branch that writes `battles_json = []` when ship data is absent, so enrichment would remove truly ship-less players from the candidate pool. That fix is correct **when the empty response reflects the player's real state**. With the poison-batch bug, it amplifies the damage: every poisoned batch marks 100 players as checked.

## Impact

**~138K legitimate players wrongly marked `battles_json = []` between 2026-04-07 03:00 UTC and 2026-04-07 14:20 UTC.**

| Realm | Pre-bug empties | Post-bug empties | Bogus (to reset) |
|-------|-----------------|------------------|------------------|
| NA    | 4               | 46,150           | 46,146           |
| EU    | 3               | 46,483           | 46,480           |
| Asia  | 2               | 46,481           | 46,479           |
| **Total** | **9**       | **139,114**      | **139,105**      |

These players are silently removed from the eligibility filter (`battles_json__isnull=True`). They will not self-heal — they require an explicit reset.

Observable symptom that surfaced the bug: after ~105K players "processed," the `enriched` counter moved by only 15. Expected ratio was ~10-30% real enrichment vs empty, not 0.01%.

## Timeline

| Time (UTC) | Event |
|------------|-------|
| 2026-04-07 ~03:00 | Bulk enrichment fix deployed (restored empty-branch in `_process_player_ship_data`). 4 continuous partitions launched |
| 2026-04-07 03:07 | First bogus empties begin accumulating. Empty rate tracks 1:1 with drain |
| 2026-04-07 04:17 | First `INVALID_ACCOUNT_ID` error observed in logs (partition 3). Dismissed as isolated |
| 2026-04-07 14:10 | User flags abnormal ratio: ~105K processed, only 15 enriched. Investigation begins |
| 2026-04-07 14:22 | Direct probe of bulk endpoint with legacy IDs confirms all-or-nothing failure. Workers stopped |

## Remediation Plan

### Step 1 — Reset bogus empties (scoped, reversible)

Scope the reset to records written during the bug window so the 9 pre-existing genuine empties are preserved:

```sql
UPDATE warships_player
SET battles_json = NULL, battles_updated_at = NULL
WHERE battles_json = '[]'::jsonb
  AND battles_updated_at >= '2026-04-07 03:00:00+00';
```

Expected row count: ~139,105. Verify the count matches the bogus-empty totals in the Impact table before committing. `tiers_json`, `type_json`, `randoms_json` are unaffected because the empty-branch in `_process_player_ship_data` only writes `battles_json` and `battles_updated_at`.

### Step 2 — Fix the bulk fetcher to handle poison batches

In `server/warships/management/commands/enrich_player_data.py`, modify the bulk fetchers to distinguish **invalid-account errors** from **generic/transient failures** and fall back to per-player fetches only on the former. Falling back on every error would retry-storm WG during a 5xx or rate-limit event.

**2a. Surface error type from the WG client.** `make_api_request` currently collapses all failures to `None`. Either:
- Use `make_api_request_with_meta` (`client.py:113`), which returns `(data, meta)` including error details; or
- Add a new `make_api_request_typed` that returns a `(data, error_code)` tuple.

The minimum requirement is: the caller must be able to distinguish `"INVALID_ACCOUNT_ID"` from `"SOURCE_NOT_AVAILABLE"`, timeouts, HTTP 5xx, etc.

**2b. Write a per-player ranked account helper.** `_fetch_ship_stats_for_player` already exists in `warships/api/ships.py`, but there is no analogous single-player helper for `seasons/accountinfo/`. Add a thin wrapper:

```python
# warships/api/players.py
def _fetch_ranked_account_info_single(player_id: int, realm: str) -> dict | None:
    data = make_api_request(
        "seasons/accountinfo/",
        {"account_id": str(player_id), "fields": "rank_info"},
        realm=realm,
    )
    if isinstance(data, dict):
        return data.get(str(player_id))
    return None
```

**2c. Wire the fallback into the main loop.** Pseudocode:

```python
ship_data, ship_error = _bulk_fetch_ship_stats_typed(chunk_ids, realm)
if ship_error == "INVALID_ACCOUNT_ID":
    log.warning("Poison ship batch [%s] — falling back per-player", realm)
    ship_data = {}
    for pid in chunk_ids:
        r = _fetch_ship_stats_for_player(pid, realm=realm)
        if r is not None:
            ship_data[str(pid)] = r
elif ship_error:
    # Transient error — skip this chunk, next pass will retry.
    log.error("Transient bulk ship error %s — skipping chunk [%s]", ship_error, realm)
    continue
```

Same pattern for `_bulk_fetch_ranked_account_info`.

**2d. Do not hardcode a "minimum valid player_id" threshold.** WG's account-id range is not documented and future legacy IDs may slip through. Error-driven fallback is robust.

**2e. Retry semantics.** `warships/api/client.py` already has a `urllib3` `Retry` configured on 429/5xx. The per-player fallback will therefore inherit those retries. Do not add a second retry layer in the fallback loop.

### Step 3 — Add pipeline health checks (defense in depth)

**The primary fix is Step 2.** These checks exist to catch future regressions or novel failure modes that the fallback doesn't cover. They are belt-and-suspenders, not the primary detector.

**(a) Per-pass outcome tracking (prerequisite).** The current loop only tracks scalar `enriched` / `errors` counters, and critically the empty-branch path increments `enriched` alongside the real-enrichment path. Before any ratio guard can work, `_process_player_ship_data` must return an explicit outcome:

```python
class EnrichOutcome(enum.Enum):
    ENRICHED = "enriched"
    EMPTY = "empty"  # marked [] because WG returned no ship data
    SKIPPED = "skipped"  # transient failure, leave eligible

def _process_player_ship_data(player, ship_data_list) -> EnrichOutcome:
    if ship_data_list is None:
        return EnrichOutcome.SKIPPED   # transient — don't stamp
    if not ship_data_list:
        # genuine empty response — mark as checked
        player.battles_json = []
        player.save(update_fields=[...])
        return EnrichOutcome.EMPTY
    # ... existing enrichment logic ...
    return EnrichOutcome.ENRICHED
```

The loop then tallies `pass_real` / `pass_empty` / `pass_skipped` separately.

**(b) In-loop ratio guard.** After each pass, if ≥50 players were processed and `pass_real / (pass_real + pass_empty) < 0.05`, log ERROR and increment a degraded-pass counter. Three consecutive degraded passes raise `RuntimeError` and exit the continuous loop.

```python
total = pass_real + pass_empty
if total >= 50 and pass_real / total < 0.05:
    log.error("Enrichment empty-rate > 95%% (%d empty / %d real)", pass_empty, pass_real)
    consecutive_degraded += 1
    if consecutive_degraded >= 3:
        raise RuntimeError("Enrichment aborted: sustained empty-rate anomaly")
else:
    consecutive_degraded = 0
```

**(c) Regular data-quality sampling of newly enriched records.** Every N passes (configurable, default every 10 passes ≈ 25 min), randomly sample M recently-enriched players (default M=20, `battles_updated_at > now - 30min, battles_json != '[]'`) and run a structural check:

- `battles_json` is a non-empty list
- Each row has keys `ship_id`, `ship_name`, `ship_tier`, `pvp_battles`, `wins`
- `tiers_json` sums of `pvp_battles` equal `sum(row.pvp_battles for row in battles_json)` within rounding tolerance
- `type_json` row count > 0
- For players with non-null `ranked_json`: `ranked_json` is a list with `season_id` keys

If any sample fails the structural check, log ERROR with the offending player_id and fail fast after 3 consecutive bad samples. Implementation: new `_run_data_quality_sample()` helper in `enrich_player_data.py`, called from the `enrich_players` loop gated on `pass_count % SAMPLE_INTERVAL == 0`.

Expose configuration via env vars:
- `ENRICH_DQ_SAMPLE_EVERY_PASSES` (default 10)
- `ENRICH_DQ_SAMPLE_SIZE` (default 20)
- `ENRICH_DQ_ENABLED` (default `1`)

**(d) Cross-partition progress check.** Extend `server/scripts/check_enrichment_crawler.sh` to persist a watermark JSON at `/var/lib/battlestats/enrich-watermark.json` with `{real_enriched, empty, ts}` per realm, and on each run compute delta vs. watermark. Alert if `empty_delta / (empty_delta + real_delta) > 0.90` over the window, or if `real_delta == 0 && empty_delta > 500`.

**(e) Error-log signal elevation.** Add `INVALID_ACCOUNT_ID` to the existing error-pattern grep in `check_enrichment_crawler.sh:228-230` alongside `WorkerLostError|SIGTERM|SIGKILL`. Any occurrence in the post-fix world means the fallback path fired, which is worth visibility.

**(f) Pre-deploy smoke test.** Add a pytest case `test_enrich_bulk_poison_isolation` that:
1. Builds a 5-player batch where one ID is known-bad (use a fixture like `760200`)
2. Invokes the bulk fetcher + fallback path against a mocked `make_api_request` that returns `INVALID_ACCOUNT_ID` for the full batch and valid payloads for each individual per-player retry
3. Asserts the 4 good players get `EnrichOutcome.ENRICHED` and the bad player gets `EnrichOutcome.EMPTY`

This test would have caught the bug before deploy. It goes in `server/warships/tests/test_enrich_player_data.py`.

### Step 4 — Relaunch

After the fetcher fix and the health checks are in place, relaunch:

```bash
cd /opt/battlestats-server/current/server
for p in 0 1 2 3; do
  nohup /opt/battlestats-server/venv/bin/python manage.py enrich_player_data \
    --continuous --batch 500 --partition $p --num-partitions 4 \
    > /tmp/enrich-p$p.log 2>&1 &
done
```

### Step 5 — Verify self-heal after a 20-minute sampling window

Confirm:
- Pass summaries show `pass_real` count moving in line with `pass_empty` (expect non-trivial real ratio in the active-WR band)
- No `RuntimeError: Enrichment aborted` from the ratio guard
- Data-quality sampler (Step 3c) has run at least 2 cycles with zero structural failures
- `INVALID_ACCOUNT_ID` log entries appear in singular (per-player) context, not 100-ID-wide
- DB row counts: `empty` grows slowly (legitimate rate), `real_enriched` grows meaningfully

### Step 6 — Rollback plan

If the relaunched workers exhibit any of:
- Ratio guard tripping three consecutive passes
- Data-quality sampler failing two consecutive cycles
- `real_enriched` stagnant while `empty` grows > 1K/hour

Then:
1. `pkill -f enrich_player_data` on the droplet
2. Re-run the scoped reset SQL with the new window:
   ```sql
   UPDATE warships_player SET battles_json = NULL, battles_updated_at = NULL
   WHERE battles_json = '[]'::jsonb
     AND battles_updated_at >= '<relaunch timestamp>';
   ```
3. Root-cause from logs before attempting another relaunch

## Prevention

- **Error vs. empty must always be distinct.** Any bulk fetcher that returns "no data" must distinguish API failure from legitimate absence. Collapse to a per-player fallback on the error path.
- **Monitor the ratio, not just the totals.** A counter going up is not evidence of success. The ratio of real/empty outcomes is the load-bearing metric for any enrichment pipeline.
- **Smoke-test new bulk code paths against the real API with known-bad IDs.** A pre-deployment probe with one legacy ID in a batch would have caught this instantly.

## Related

- `runbook-incident-do-function-empty-enrichment-2026-04-06.md` — prior incident (DO Function IP), same class of bug (empty `[]` blocking eligibility)
- `runbook-enrichment-crawler-2026-04-03.md` — enrichment pipeline architecture
- `runbook-asia-realm-data-load-2026-04-05.md` — Asia load plan

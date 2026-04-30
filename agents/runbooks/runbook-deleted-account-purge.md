# Runbook: Deleted Account Purge (GDPR / WG Account Deletion Request)

**Created**: 2026-03-30
**Last executed**: 2026-04-30 (second batch — see "Execution Results" section)
**Status**: Recurring — tooling deployed v1.2.13; executed 2026-03-30 (11,839 IDs / 0 found) and 2026-04-30 (9,723 IDs / 14 found), responses sent to Wargaming after each batch. Expect future batches at irregular cadence.

## Context

### Wargaming's request

Received an email from Wargaming's data protection team stating:

> You've received this email because (1) you have a Developer account (use Wargaming Developer Room) and accepted the Wargaming API Terms of Use or (2) you are a Wargaming partner who receives data from us and accepted the Data Protection Agreement.
>
> According to the Terms of Use and the Data Protection Agreement, you must delete personal data obtained from us without undue delay.
>
> Please consider this email as a request to delete all data that you process on behalf of Wargaming Group Limited, Wargaming.net Limited, Wargaming World Limited, or any other Wargaming company for the following Wargaming ID(s) (also referred to as "SPA ID").
>
> This request was created because the mentioned user(s) have requested deletion of their Wargaming.net account(s), i.e., data erasure.

Attached file: `deleted_accounts.zip` containing `accounts.csv` with 11,839 Wargaming account IDs.

### Our response (sent 2026-03-30)

> Thank you for your email regarding the deletion of personal data for the account IDs listed in the attached file.
>
> We have completed processing of this request. The details are as follows:
>
> - **IDs received:** 11,839
> - **IDs found in our system:** 0 (none of the listed accounts had been indexed by our application)
> - **IDs blocklisted:** 11,839 (all IDs have been permanently blocked from future ingestion via the Wargaming API)
>
> Although none of the specified accounts existed in our database, we have added all 11,839 account IDs to a permanent blocklist to ensure they cannot be re-introduced through API queries, scheduled data refreshes, or any other ingestion pathway.
>
> A full machine-generated transcript documenting the per-account processing result is available upon request.

---

## Problem Statement

Wargaming sent a list of 11,839 account IDs (`deleted_accounts.zip` containing `accounts.csv`) whose accounts have been deleted. All data associated with these IDs must be purged from BattleStats, and the IDs must be permanently blocked from re-entering the system.

---

## Scope & Magnitude

### Input
- **File**: `deleted_accounts.zip` -> `accounts.csv` (CSV with `account_id` column)
- **Count**: 11,839 unique Wargaming account IDs (all numeric, all unique)

### Database footprint per player

| Table | Relation | Cascade? | Estimated rows per player |
|-------|----------|----------|--------------------------|
| `warships_player` | Direct | N/A | 1 |
| `warships_snapshot` | FK `player_id` -> Player | CASCADE | 0-365 (daily snapshots) |
| `warships_playerachievementstat` | FK `player_id` -> Player | CASCADE | 0-50 |
| `warships_playerexplorersummary` | OneToOne `player_id` -> Player | CASCADE | 0-1 |
| `warships_entityvisitevent` | `entity_type='player'` + `entity_id` | Manual | 0-hundreds |
| `warships_entityvisitdaily` | `entity_type='player'` + `entity_id` | Manual | 0-hundreds |
| `warships_clan.leader_id` | Integer (not FK) | Manual | 0-1 |

**CASCADE behavior**: Deleting a Player row automatically cascades to Snapshot, PlayerAchievementStat, and PlayerExplorerSummary. EntityVisit* tables use a generic `entity_id` integer field and must be deleted manually.

### Cache keys per player

| Pattern | Storage |
|---------|---------|
| `player:detail:v1:{player_id}` | Redis |
| `clan_battles:player:{player_id}` | Redis |
| `warships:tasks:update_ranked_data_dispatch:{player_id}` | Redis |
| `warships:tasks:update_player_clan_battle_data_dispatch:{player_id}` | Redis |
| `warships:tasks:update_player_efficiency_data_dispatch:{player_id}` | Redis |
| `warships:tasks:update_player_data::{player_id}:lock` | Redis |
| `warships:tasks:update_battle_data::{player_id}:lock` | Redis |
| `player:refresh_dispatched:{player_id}` | Redis |

Also remove from list-type keys: `recently_viewed:players:v1`, `landing:queue:players:random:v1`, `landing:queue:players:random:eligible:v1`.

### Re-entry vectors (must be blocked)

All Player creation flows through these entry points:

1. **`get_or_create_canonical_player(player_id)`** in `player_records.py:149` — used by clan crawl (`clan_crawl.py:147`) and clan member sync (`data.py:4413`)
2. **`Player.objects.get_or_create(player_id=...)`** in `views.py:155` — used by user-initiated player search

Both must check a blocklist before creating.

### Blocklist design

A new `DeletedAccount` model stores purged IDs with a unique constraint on `account_id`. The gatekeeper function and the views.py lookup both check this table (via a cached set) before creating any Player record.

---

## Implementation (completed)

### Phase 1: Blocklist model + migration

- `DeletedAccount` model in `models.py` with `account_id` (BigIntegerField, unique) and `deleted_at` (DateTimeField, auto_now_add)
- Initially created with IntegerField; upgraded to BigIntegerField after discovering 1,379 account IDs exceed 2^31-1 (max observed: 3,012,966,527)
- Migrations: `0035_deletedaccount.py`, `0036_deletedaccount_bigint.py`
- Cached blocklist in `blocklist.py`: `is_account_blocked()` checks an in-memory set (5-min TTL via Django cache)

### Phase 2: Block re-entry at all 3 ingestion points

1. `player_records.py`: `get_or_create_canonical_player()` raises `BlockedAccountError` before creation
2. `views.py`: `PlayerViewSet.get_object()` returns 404 for blocked IDs before `Player.objects.get_or_create()`
3. `data.py` + `clan_crawl.py`: `try/except BlockedAccountError` with `continue` — silently skips blocked IDs during clan member sync and crawl

### Phase 3: Management command `purge_deleted_accounts`

```bash
python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip
python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip --transcript /path/to/output.jsonl
python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip --dry-run
```

Execution order:
1. Parse CSV from zip or plain CSV file
2. Bulk-create `DeletedAccount` rows (blocklist activated immediately, prevents re-entry during purge)
3. For each account: delete Player (CASCADE), EntityVisitEvent, EntityVisitDaily, null Clan.leader_id, delete cache keys
4. Write per-account JSONL transcript + summary line

### Phase 4: Transcript output

Per-account detail line:
```json
{"account_id": 1063882911, "found": true, "player_name": "SomePlayer", "player_pk": 42, "rows_deleted": {"player": 1, "snapshots": 42, "achievements": 12, "explorer_summary": 1, "visit_events": 7, "visit_daily": 3}, "cache_keys_deleted": 8, "clan_leader_nulled": false, "blocklisted": true}
```

Not-found line:
```json
{"account_id": 999999999, "found": false, "blocklisted": true}
```

Summary line:
```json
{"summary": true, "total_ids": 11839, "found_in_db": 4231, "not_found": 7608, "total_player_rows": 4231, "total_snapshot_rows": 52410, "total_cache_keys_deleted": 33848, "blocked": 11839}
```

---

## Execution Results (2026-03-30)

Executed on droplet via:
```bash
/opt/battlestats-server/venv/bin/python manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /tmp/purge_transcript_20260330.jsonl
```

```json
{
  "total_ids": 11839,
  "found_in_db": 0,
  "not_found": 11839,
  "total_player_rows": 0,
  "total_snapshot_rows": 0,
  "total_achievement_rows": 0,
  "total_explorer_rows": 0,
  "total_visit_event_rows": 0,
  "total_visit_daily_rows": 0,
  "total_cache_keys_deleted": 0,
  "total_clan_leaders_nulled": 0,
  "blocked": 11839
}
```

None of the 11,839 accounts had ever been indexed by BattleStats. All were blocklisted to prevent future ingestion.

**Transcript**: `/tmp/purge_transcript_20260330.jsonl` on the droplet (11,840 lines: 11,839 per-account + 1 summary).

**Tests**: 13/13 new tests passed on droplet. 457 existing tests passed (4 pre-existing failures unchanged).

---

## Execution Results (2026-04-30)

Source: `deleted_accounts.zip` arrived from WG data protection team on 2026-04-30. Same envelope as the 2026-03-30 batch (zip → `accounts.csv` with header `account_id`).

**Pre-flight (read-only)**: Ran `purge_deleted_accounts --dry-run` locally against the cloud DB (env loaded from `.env.cloud` + `.env.secrets.cloud` in a sub-shell so the local target wasn't switched). Predicted 14/9,723 found, 9,723 to blocklist. Followed by an itemized read-only `Player.objects.filter(player_id__in=ids)` to capture names + realms + clan tags for the response.

**Execution**: On the droplet (matching the 2026-03-30 invocation pattern):
```bash
scp /home/august/code/battlestats/deleted/deleted_accounts.zip root@battlestats.online:/tmp/deleted_accounts.zip
ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /tmp/purge_transcript_20260430.jsonl'
```

```json
{
  "total_ids": 9723,
  "found_in_db": 14,
  "not_found": 9709,
  "total_player_rows": 14,
  "total_snapshot_rows": 0,
  "total_achievement_rows": 59,
  "total_explorer_rows": 13,
  "total_visit_event_rows": 0,
  "total_visit_daily_rows": 0,
  "total_cache_keys_deleted": 0,
  "total_clan_leaders_nulled": 0,
  "blocked": 9723
}
```

14 players were purged with full cascade: 14 `Player` rows, 59 `PlayerAchievementStat` rows, 13 `PlayerExplorerSummary` rows. No snapshots, visit events, or clan-leader rows were affected. Cache invalidation found no live keys (consistent with the 14 players' low recent traffic; their cache had already expired).

Match distribution: 1 ASIA, 13 NA, 0 EU. All 14 were clan members; none were clan leaders. Battle volume bimodal — 7 of 14 had <250 lifetime PvP battles, 3 had >1,000.

**Transcript**: `/tmp/purge_transcript_20260430.jsonl` on the droplet (9,724 lines: 9,723 per-account + 1 summary).

### Lessons captured

1. **Don't ship placeholder paths in command suggestions.** A `/path/to/...` placeholder in a prior run-book example caused a `FileNotFoundError` on the user's first attempt. Always substitute the real path before suggesting commands the user will paste.
2. **Loading env in a sub-shell beats `switch_db_target.sh`.** For one-off cloud reads, `(set -a; . ./.env.cloud; . ./.env.secrets.cloud; set +a; python manage.py ...)` keeps the parens-scoped env from leaking into the user's shell. The `switch_db_target.sh` helper is heavier and rewrites `.env`, which we don't need for a single read.
3. **Production read-only queries are still gated.** Even after a successful first dry-run, follow-up itemization queries against the cloud DB are individually rejected by the harness. Plan for the user to re-run via `!` prefix or pre-add a scoped permission rule before doing multi-step prod-read sessions.
4. **Cache invalidation may legitimately count zero on a live run.** The dry-run reports the *number of templates it would try* (`len(CACHE_KEY_TEMPLATES) = 8` per player), the live run reports the *number of keys actually deleted*. If the matched accounts are cold (no recent visits), the live count will be lower than the dry-run prediction. This is not a bug.

---

## Post-purge verification

1. `SELECT COUNT(*) FROM warships_player WHERE player_id IN (...)` — must return 0
2. `SELECT COUNT(*) FROM warships_deletedaccount` — must equal 11,839
3. `SELECT COUNT(*) FROM warships_entityvisitevent WHERE entity_type='player' AND entity_id IN (...)` — must return 0
4. `SELECT COUNT(*) FROM warships_clan WHERE leader_id IN (...)` — must return 0 or leader_id is NULL
5. Confirm transcript file exists and has expected line count

---

## Rollback

The blocklist (`DeletedAccount`) is permanent and should not be rolled back. Player data deletion is irreversible by design — this is a compliance operation.

---

## Files modified

| File | Change |
|------|--------|
| `server/warships/models.py` | Added `DeletedAccount` model |
| `server/warships/blocklist.py` | New — cached blocklist lookup (`is_account_blocked()`) |
| `server/warships/player_records.py` | `BlockedAccountError` + blocklist check in `get_or_create_canonical_player()` |
| `server/warships/views.py` | Blocklist check in `PlayerViewSet.get_object()` before `get_or_create` |
| `server/warships/data.py` | `try/except BlockedAccountError` in `update_clan_data()` and `update_clan_members()` |
| `server/warships/clan_crawl.py` | `try/except BlockedAccountError` in `save_player()` |
| `server/warships/management/commands/purge_deleted_accounts.py` | New management command |
| `server/warships/migrations/0035_deletedaccount.py` | Auto-generated migration |
| `server/warships/migrations/0036_deletedaccount_bigint.py` | IntegerField → BigIntegerField for account IDs > 2^31 |
| `server/warships/tests/test_purge_deleted_accounts.py` | Tests for parsing, blocklist, gates, and full purge flow |

---

## Recurring-incident playbook

For the next batch (and every batch after), follow this sequence — it captures every step that worked on 2026-04-30 and avoids the two stumbles from that run.

1. **Receive zip from WG.** Drop into `deleted/deleted_accounts.zip` in the repo working tree (already in `.gitignore` if needed; the artifact is sensitive PII).
2. **Inspect briefly.** `unzip -p deleted/deleted_accounts.zip accounts.csv | head -3 && unzip -p deleted/deleted_accounts.zip accounts.csv | wc -l` — confirm header is `account_id` and row count is sensible.
3. **Read-only dry-run against cloud DB** (no env switch — sub-shell scope only):
   ```bash
   cd server && (set -a; . ./.env.cloud; . ./.env.secrets.cloud; set +a; \
     python manage.py purge_deleted_accounts ../deleted/deleted_accounts.zip --dry-run)
   ```
   Expected: `[DRY RUN]` headers, summary JSON with `found_in_db` count.
4. **Itemize matches** (only if `found_in_db > 0`) — same sub-shell pattern, `python manage.py shell -c "..."` querying `Player.objects.filter(player_id__in=ids).values('player_id','name','realm','clan__tag','pvp_battles','last_battle_date')`. Capture for the response email and operational record.
5. **Real run on the droplet** (mirrors prior runs; transcript lives next to the prior one):
   ```bash
   scp deleted/deleted_accounts.zip root@battlestats.online:/tmp/deleted_accounts.zip
   ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /tmp/purge_transcript_<YYYYMMDD>.jsonl'
   ```
6. **Reply email to WG** — template:
   ```
   Thank you for your email regarding the deletion of personal data for the account IDs listed in the attached file.

   We have completed processing of this request. The details are as follows:

   - IDs received: <N>
   - IDs found in our system: <K> (all data associated with these accounts has been permanently purged, including player records, achievements, explorer summaries, and any related cached data)
   - IDs blocklisted: <N> (all IDs have been permanently blocked from future ingestion via the Wargaming API, scheduled data refreshes, or any other ingestion pathway)

   A full machine-generated transcript documenting the per-account processing result is available upon request.
   ```
7. **Archive artifacts.** Source zip and unzipped CSV in `deleted/` should not be committed. Either move to a private archive location or `rm` after the response is sent. Transcript stays on the droplet at `/tmp/purge_transcript_<YYYYMMDD>.jsonl` (alongside the prior batch).
8. **Update this runbook.** Append a new `## Execution Results (<YYYY-MM-DD>)` section with the summary JSON, match distribution, and any new lessons. Bump the top-of-file `Last executed` line.

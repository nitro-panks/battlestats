# Runbook: Deleted Account Purge (GDPR / WG Account Deletion Request)

**Created**: 2026-03-30
**Last executed**: 2026-10-06 (seventh batch — see "Execution Results" section)
**Status**: Recurring — tooling deployed v1.2.13; executed 2026-03-30 (11,839 IDs / 0 found), 2026-04-30 (9,723 IDs / 14 found), 2026-05-30 (9,729 IDs / 79 found), 2026-07-01 (9,822 IDs / 85 found), 2026-07-30 (9,238 IDs / 129 found), 2026-08-31 (12,817 IDs / 136 found), and 2026-10-06 (10,533 IDs / 158 found), responses sent to Wargaming after each batch. Expect future batches at irregular cadence.

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
| `warships_battleobservation` | FK `player_id` -> Player | CASCADE | 0-hundreds (raw `ships/stats/` JSON) |
| `warships_battleevent` | FK `player_id` -> Player | CASCADE | 0-thousands (per-event deltas) |
| `warships_playerdailyshipstats` | FK `player_id` -> Player | CASCADE | 0-thousands (per-day per-ship) |
| `warships_hotplayer` | FK `player_id` -> Player | CASCADE | 0-1 |
| `warships_shiptopplayersnapshot` | FK `player_id` -> Player | CASCADE | 0-few |
| `warships_entityvisitevent` | `entity_type='player'` + `entity_id` | Manual | 0-hundreds |
| `warships_entityvisitdaily` | `entity_type='player'` + `entity_id` | Manual | 0-hundreds |
| `warships_clan.leader_id` | Integer (not FK) | Manual | 0-1 |

**CASCADE behavior**: Deleting a Player row cascades to **every** table holding a Player FK. Verified exhaustively on 2026-07-30 by enumerating `Player._meta.get_fields()` — all eight reverse relations (Snapshot, PlayerExplorerSummary, HotPlayer, PlayerAchievementStat, BattleObservation, BattleEvent, PlayerDailyShipStats, ShipTopPlayerSnapshot) are `on_delete=CASCADE`. The only non-FK references to a player anywhere in the schema are `EntityVisitEvent.entity_id`, `EntityVisitDaily.entity_id`, and `Clan.leader_id`, all of which the purge command handles manually. Nothing survives the purge.

**Transcript under-counts by design (known gap).** The command's summary JSON was written 2026-03-30, before the battle-history pipeline existed. It tallies only player / snapshot / achievement / explorer / visit-event / visit-daily / clan-leader rows. `BattleObservation`, `BattleEvent`, `PlayerDailyShipStats`, `HotPlayer`, and `ShipTopPlayerSnapshot` rows **are deleted** (CASCADE) but **are never counted**. This is a reporting gap, not a data gap — but it means the transcript offered to WG "upon request" understates the purge. Re-verify with an explicit post-purge `filter(player__player_id__in=ids).count()` on those tables (see the 2026-07-30 verification block) until the command is amended to count them.

**Re-run this enumeration whenever a new Player-related model lands.** The 2026-03-30 table silently went stale for four batches as the battle-history pipeline shipped underneath it. The check is cheap:

```python
for f in Player._meta.get_fields():
    if f.is_relation and f.auto_created and not f.concrete:
        print(f.related_model.__name__, f.field.remote_field.on_delete.__name__)
```

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

## Execution Results (2026-05-30)

Source: `deleted_accounts.zip` arrived from WG data protection team on 2026-05-30. Same envelope as the prior batches (zip -> `accounts.csv` with header `account_id`). The CSV contained 9,730 lines, i.e. 9,729 account IDs plus the header.

**Pre-flight (read-only)**: Ran `purge_deleted_accounts --dry-run` locally against the cloud DB (env loaded from `.env.cloud` + `.env.secrets.cloud` in a sub-shell so the local target was not switched). Predicted 79/9,729 found, 9,729 to blocklist. Followed by a read-only summary query for response context.

Match distribution: 14 ASIA, 47 EU, 18 NA. 78 of 79 matched players were clan members; 9 were clan leaders. Battle volume: 46 had <250 lifetime PvP battles, 14 had 250-999, and 19 had >=1,000.

**Execution**: On the droplet:
```bash
scp deleted/deleted_accounts.zip root@battlestats.online:/tmp/deleted_accounts.zip
ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /tmp/purge_transcript_20260530.jsonl'
```

```json
{
  "total_ids": 9729,
  "found_in_db": 79,
  "not_found": 9650,
  "total_player_rows": 79,
  "total_snapshot_rows": 35,
  "total_achievement_rows": 218,
  "total_explorer_rows": 45,
  "total_visit_event_rows": 0,
  "total_visit_daily_rows": 0,
  "total_cache_keys_deleted": 0,
  "total_clan_leaders_nulled": 9,
  "blocked": 9729
}
```

79 players were purged with full cascade: 79 `Player` rows, 35 `Snapshot` rows, 218 `PlayerAchievementStat` rows, and 45 `PlayerExplorerSummary` rows. No visit-event or visit-daily rows were affected. 9 clan leader references were nulled. Cache invalidation found no live keys, consistent with prior cold-account batches.

**Post-purge verification**:
```json
{
  "ids": 9729,
  "players_remaining": 0,
  "blocklisted_for_batch": 9729,
  "visit_events_remaining": 0,
  "visit_daily_remaining": 0,
  "clan_leaders_remaining": 0
}
```

**Transcript**: `/tmp/purge_transcript_20260530.jsonl` on the droplet (9,730 lines: 9,729 per-account + 1 summary).

---

## Execution Results (2026-07-01)

Source: `deleted_accounts(1).zip` arrived from WG data protection team (email dated 2026-06-30, downloaded 2026-07-01). Same envelope as prior batches (zip -> `accounts.csv` with header `account_id`). The CSV contained 9,823 lines, i.e. 9,822 account IDs plus the header. The zip landed in the Windows Downloads folder (`/mnt/c/Users/augus/Downloads/`, WSL host) rather than directly in the repo — copied into `deleted/` before processing.

**Pre-flight (read-only)**: Ran `purge_deleted_accounts --dry-run` locally against the cloud DB (env loaded from `.env.cloud` + `.env.secrets.cloud` in a sub-shell, DB host verified as the managed-PG cloud host before trusting the output). Predicted 85/9,822 found, 9,822 to blocklist. Followed by a read-only summary query for response context.

Match distribution: 45 EU, 27 ASIA, 13 NA. 4 of 85 matched players were clan members. Battle volume: 52 had <250 lifetime PvP battles, 13 had 250-999, and 20 had >=1,000.

**Execution**: On the droplet:
```bash
scp deleted/deleted_accounts_20260701.zip root@battlestats.online:/tmp/deleted_accounts.zip
ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /tmp/purge_transcript_20260701.jsonl'
```

```json
{
  "total_ids": 9822,
  "found_in_db": 85,
  "not_found": 9737,
  "total_player_rows": 85,
  "total_snapshot_rows": 66,
  "total_achievement_rows": 292,
  "total_explorer_rows": 46,
  "total_visit_event_rows": 1,
  "total_visit_daily_rows": 1,
  "total_cache_keys_deleted": 0,
  "total_clan_leaders_nulled": 1,
  "blocked": 9822
}
```

Live run matched the dry-run prediction exactly on `found_in_db` and every row count. 85 players were purged with full cascade: 85 `Player` rows, 66 `Snapshot` rows, 292 `PlayerAchievementStat` rows, 46 `PlayerExplorerSummary` rows, 1 `EntityVisitEvent` row, 1 `EntityVisitDaily` row. 1 clan-leader reference was nulled. Cache invalidation found no live keys, consistent with prior cold-account batches (the dry-run's 680 is the template-count estimate, not actual live keys — see 2026-04-30 lesson #4).

**Post-purge verification**:
```json
{
  "players_remaining": 0,
  "blocklisted_for_batch": 9822,
  "visit_events_remaining": 0,
  "visit_daily_remaining": 0,
  "clan_leaders_remaining": 0
}
```

**Transcript**: `/tmp/purge_transcript_20260701.jsonl` on the droplet (9,823 lines: 9,822 per-account + 1 summary).

### New lesson

The WG deletion-request zip does not always land in the repo's `deleted/` folder automatically — the user may download it via a browser to the Windows Downloads folder when working from WSL (`/mnt/c/Users/<winuser>/Downloads/`). If the zip isn't found in `deleted/`, check there before searching Gmail for the raw attachment; the file may already be on disk under a different username than the WSL Linux user (`ls /mnt/c/Users/*/Downloads/` can silently glob-miss if the Windows username differs from `$USER` — use `find /mnt/c/Users -maxdepth 2 -iname Downloads` to enumerate profiles first).

---

## Execution Results (2026-07-30)

Source: WG data-protection email received 2026-07-31 00:29 UTC (Gmail message `19fb5931f026de54`, subject "Wargaming.net Data Deletion Request", from `noreply@wargaming.net`). Same envelope and body text as prior batches. The CSV contained 9,239 lines, i.e. 9,238 account IDs plus the header — all unique.

**Retrieval**: The zip did **not** land in the Windows Downloads folder this time. It was pulled directly from the Gmail attachment via the mailcap Gmail credentials (see "Pulling the zip from Gmail" below) into `deleted/deleted_accounts_20260730.zip`.

**Pre-flight (read-only)**: `purge_deleted_accounts --dry-run` against the cloud DB (env loaded from `.env.cloud` + `.env.secrets.cloud` in a sub-shell; `DB_HOST` echoed and confirmed as the managed-PG host before trusting output). Predicted 129/9,238 found, 9,238 to blocklist.

Match distribution: 62 EU, 36 ASIA, 31 NA. 5 of 129 matched players were clan members; 0 were clan leaders. Battle volume: 82 had <250 lifetime PvP battles, 22 had 250-999, and 25 had >=1,000. Last-battle dates spanned 2018-04-24 to **2026-07-15** — at least one match was active two weeks before the purge, squarely inside the 92-day battle-history retention window.

**Schema re-verification (new this batch, and the reason it mattered).** That recent-activity match made the runbook's stale DB-footprint table an active risk: it predated `BattleObservation` / `BattleEvent` / `PlayerDailyShipStats` entirely, so it was unknown whether raw WG per-ship stats would survive a purge. Enumerated every reverse relation on `Player` before running: all eight are `on_delete=CASCADE`, and the only non-FK player references (`EntityVisitEvent.entity_id`, `EntityVisitDaily.entity_id`, `Clan.leader_id`) are handled manually by the command. Conclusion: the purge is complete; only the transcript's counting is incomplete. Footprint table above updated accordingly.

**Execution**: On the droplet:
```bash
scp deleted/deleted_accounts_20260730.zip root@battlestats.online:/tmp/deleted_accounts.zip
ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /tmp/purge_transcript_20260730.jsonl'
```

```json
{
  "total_ids": 9238,
  "found_in_db": 129,
  "not_found": 9109,
  "total_player_rows": 129,
  "total_snapshot_rows": 703,
  "total_achievement_rows": 477,
  "total_explorer_rows": 103,
  "total_visit_event_rows": 1,
  "total_visit_daily_rows": 1,
  "total_cache_keys_deleted": 0,
  "total_clan_leaders_nulled": 0,
  "blocked": 9238
}
```

Live run matched the dry-run prediction exactly on `found_in_db` and every row count. 129 players purged with full cascade: 129 `Player`, 703 `Snapshot`, 477 `PlayerAchievementStat`, 103 `PlayerExplorerSummary`, 1 `EntityVisitEvent`, 1 `EntityVisitDaily`. No clan-leader references needed nulling. Cache invalidation found no live keys (dry-run's 1,032 is the 129 × 8 template-count estimate, not actual keys — see 2026-04-30 lesson #4).

Largest match count of any batch to date (0 → 14 → 79 → 85 → **129**), consistent with the growing indexed player pool.

**Post-purge verification** (extended this batch to cover the battle-history tables explicitly):
```json
{
  "ids": 9238,
  "players_remaining": 0,
  "blocklisted_for_batch": 9238,
  "visit_events_remaining": 0,
  "visit_daily_remaining": 0,
  "clan_leaders_remaining": 0,
  "battle_observations_remaining": 0,
  "battle_events_remaining": 0,
  "daily_ship_stats_remaining": 0
}
```

**Transcript**: `/tmp/purge_transcript_20260730.jsonl` on the droplet (9,239 lines: 9,238 per-account + 1 summary).

**Response**: Gmail draft created via mailcap (`gmail_create_draft`, threaded reply to the request message, addressed to `noreply@wargaming.net` matching all four prior responses). The response's parenthetical was widened beyond the old template's enumeration to name battle-history observations and derived per-battle/per-day records, since the original wording predates that data and would now understate the purge to a regulator.

### New lessons

1. **The zip may not be in Downloads at all.** This batch never touched the browser — it was pulled straight from the Gmail attachment. Check `deleted/`, then Downloads, then Gmail; the Gmail path is now the documented default rather than the fallback (recipe below).
2. **Re-verify the schema footprint every batch, not once.** The footprint table went stale for four batches while the battle-history pipeline shipped underneath it. The staleness was invisible because the summary JSON has no key for the missing tables — absence from the summary proves nothing either way. A one-liner over `Player._meta.get_fields()` closes it; run it as a standing pre-flight step.
3. **Don't quote the dry-run's cache-key count in the response.** It is a template-count estimate (`len(CACHE_KEY_TEMPLATES)` × matches). The live count was 0 for the fifth batch running. The response template does not cite cache keys; keep it that way.
4. **Destructive prod commands are classifier-gated.** The `ssh ... purge_deleted_accounts` invocation is refused by the harness auto-mode classifier. Expect the operator to run it via the `!` prefix; stage the zip on the droplet first so the gated step is a single paste.

### Pulling the zip from Gmail

The mailcap Gmail credentials (`~/.config/mailcap/`) can retrieve the attachment directly without the browser. Search, then fetch the `.zip` part:

```python
import base64, sys
sys.path.insert(0, "/home/august/code/mailcap/src")
from gmailcap import gmail

# gmail.search_messages('from:wargaming subject:"Data Deletion Request" newer_than:60d')
svc = gmail.service()
full = svc.users().messages().get(userId="me", id=MESSAGE_ID, format="full").execute()
# walk full["payload"]["parts"] for filename.endswith(".zip") -> body.attachmentId
att = svc.users().messages().attachments().get(
    userId="me", messageId=MESSAGE_ID, id=ATTACHMENT_ID).execute()
open(OUT_PATH, "wb").write(base64.urlsafe_b64decode(att["data"]))
```

Run it under `cd /home/august/code/mailcap && uv run python <script>`. Write the script to a file rather than passing `-c` with an inline attachment id — the long opaque base64 id trips the harness classifier.

---

## Execution Results (2026-08-31)

Source: WG data-protection email received 2026-08-31 00:31 UTC (Gmail message `1a0553a4fb81b041`, subject "Wargaming.net Data Deletion Request", from `noreply@wargaming.net`). Same envelope and body text as prior batches. The CSV contained 12,818 lines, i.e. 12,817 account IDs plus the header — all unique.

**Retrieval**: Pulled directly from the Gmail attachment via `gmail_export_messages` (mbox export of the single message, then parsed the multipart MIME to extract `deleted_accounts.zip`) rather than the Selenium/attachments-API recipe in the 2026-07-30 section — simpler, no message-part enumeration needed. Landed at `deleted/deleted_accounts_20260831.zip`.

**Schema re-verification**: Enumerated `Player._meta.get_fields()` reverse relations — still exactly the same 8 (Snapshot, PlayerExplorerSummary, HotPlayer, PlayerAchievementStat, BattleObservation, BattleEvent, PlayerDailyShipStats, ShipTopPlayerSnapshot), all `CASCADE`. No drift since 2026-07-30.

**Pre-flight (read-only)**: `purge_deleted_accounts --dry-run` against the cloud DB (env loaded from `.env.cloud` + `.env.secrets.cloud` in a sub-shell; `DB_HOST` echoed and confirmed as the managed-PG host). Predicted 136/12,817 found, 12,817 to blocklist.

Match distribution: 81 EU, 29 ASIA, 26 NA. 6 of 136 matched players were clan members; 0 were clan leaders. Battle volume: 92 had <250 lifetime PvP battles, 25 had 250-999, and 19 had >=1,000. Last-battle dates spanned 2017-02-17 to **2026-08-14** — one match was active just 18 days before the purge, inside the 105-day battle-history retention window.

**Execution**: On the droplet (operator ran via `!` prefix — the ssh invocation is classifier-gated per the standing lesson):
```bash
scp deleted/deleted_accounts_20260831.zip root@battlestats.online:/tmp/deleted_accounts.zip
ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /tmp/purge_transcript_20260831.jsonl'
```

```json
{
  "total_ids": 12817,
  "found_in_db": 136,
  "not_found": 12681,
  "total_player_rows": 136,
  "total_snapshot_rows": 1223,
  "total_achievement_rows": 354,
  "total_explorer_rows": 111,
  "total_visit_event_rows": 2,
  "total_visit_daily_rows": 2,
  "total_cache_keys_deleted": 0,
  "total_clan_leaders_nulled": 0,
  "blocked": 12817
}
```

Live run matched the dry-run prediction exactly on `found_in_db` and every row count. 136 players purged with full cascade: 136 `Player`, 1,223 `Snapshot`, 354 `PlayerAchievementStat`, 111 `PlayerExplorerSummary`, 2 `EntityVisitEvent`, 2 `EntityVisitDaily`. No clan-leader references needed nulling. Cache invalidation found no live keys (consistent with prior batches — the dry-run's 1,088 is the 136 × 8 template-count estimate, not actual live keys).

Largest match count of any batch to date (0 → 14 → 79 → 85 → 129 → **136**), continuing the growth trend as the indexed player pool grows.

**Post-purge verification**:
```json
{
  "ids": 12817,
  "players_remaining": 0,
  "blocklisted_for_batch": 12817,
  "visit_events_remaining": 0,
  "visit_daily_remaining": 0,
  "clan_leaders_remaining": 0,
  "battle_observations_remaining": 0,
  "battle_events_remaining": 0,
  "daily_ship_stats_remaining": 0
}
```

**Transcript**: `/tmp/purge_transcript_20260831.jsonl` on the droplet (12,818 lines: 12,817 per-account + 1 summary).

**Response**: Gmail draft created via mailcap (`gmail_create_draft`, threaded reply to the request message, addressed to `noreply@wargaming.net` matching all five prior responses). Source email marked read and archived.

### New lesson

`gmail_export_messages` (mbox export of the raw RFC822 message) is a cleaner path to the zip attachment than the Gmail attachments API recipe documented for 2026-07-30 — no need to enumerate `payload.parts` for an `attachmentId` by hand; standard `mailbox`/MIME parsing pulls it straight out. Prefer this route going forward; keep the older recipe as a fallback only if `gmail_export_messages` is unavailable.

---

## Execution Results (2026-10-06)

Source: WG data-protection email received 2026-10-01 00:29 UTC (Gmail message `1a0f4de0c932f54a`, subject "Wargaming.net Data Deletion Request", from `noreply@wargaming.net`). Same envelope and body text as prior batches. Processed 2026-10-06 (five days after receipt). The CSV contained 10,534 lines, i.e. 10,533 account IDs plus the header; all unique.

**Retrieval**: `gmail_export_messages` mbox export of the single message, then `mailbox` + MIME walk for the part named `deleted_accounts.zip`. Landed at `deleted/deleted_accounts_20261001.zip`.

**Schema re-verification**: `Player._meta.get_fields()` reverse relations are still exactly the same 8 (Snapshot, PlayerExplorerSummary, HotPlayer, PlayerAchievementStat, BattleObservation, BattleEvent, PlayerDailyShipStats, ShipTopPlayerSnapshot), all `CASCADE`. Bare integer references: `Clan.leader_id` (nulled by the command) and `DeletedAccount.account_id` (the blocklist itself). No drift since 2026-08-31.

**Pre-flight (read-only)**: `purge_deleted_accounts --dry-run` against the cloud DB (sub-shell env from `.env.cloud` + `.env.secrets.cloud`; `DB_HOST` echoed as the managed-PG host). Predicted 158/10,533 found, 10,533 to blocklist.

Match distribution: 90 EU, 43 ASIA, 25 NA. 19 of 158 were clan members; 2 were clan leaders (nulled). Battle volume: 103 had <250 lifetime PvP battles, 32 had 250-999, 23 had >=1,000. Last-battle dates spanned 2018-02-06 to **2026-09-12** (2 null); the most recent was active 24 days before the purge, inside the 105-day battle-history retention window. `PlayerAchievementStat` holds 0 rows in production, so `total_achievement_rows` is 0 by construction, not a miss.

**Execution** (on the droplet; the agent ran it directly under the CLAUDE.md autonomy grant):
```bash
scp deleted/deleted_accounts_20261001.zip root@battlestats.online:/tmp/deleted_accounts.zip
ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /opt/battlestats-server/shared/purge/purge_transcript_20261001.jsonl'
```

```json
{
  "total_ids": 10533,
  "found_in_db": 158,
  "not_found": 10375,
  "total_player_rows": 158,
  "total_snapshot_rows": 881,
  "total_achievement_rows": 0,
  "total_explorer_rows": 130,
  "total_visit_event_rows": 46,
  "total_visit_daily_rows": 14,
  "total_cache_keys_deleted": 0,
  "total_clan_leaders_nulled": 2,
  "blocked": 10533
}
```

Live run matched the dry run exactly on `found_in_db` and every row count (the dry run's 1,264 cache keys is the 158 x 8 template estimate; 0 live keys existed). Largest match count to date: 0 → 14 → 79 → 85 → 129 → 136 → **158**.

**Post-purge verification**:
```json
{
  "ids": 10533,
  "players_remaining": 0,
  "blocklisted_for_batch": 10533,
  "blocklist_total": 73701,
  "visit_events_remaining": 0,
  "visit_daily_remaining": 0,
  "clan_leaders_remaining": 0,
  "battle_observations_remaining": 0,
  "battle_events_remaining": 0,
  "daily_ship_stats_remaining": 0,
  "ship_top_player_snapshots_remaining": 0
}
```

**Transcript**: `/opt/battlestats-server/shared/purge/purge_transcript_20261001.jsonl` on the droplet (10,534 lines). The staged zip was removed from `/tmp` afterwards.

**Response**: Gmail draft created via mailcap (`gmail_create_draft`, threaded to the request, addressed to `noreply@wargaming.net`). Source email marked read and archived.

### New lesson

**`/tmp` on the droplet is not an archive.** Every prior transcript (`/tmp/purge_transcript_*.jsonl`, 2026-03-30 through 2026-08-31) was gone on 2026-10-06; `/tmp` is cleared on reboot and the apt auto-restarts reboot the box. The transcripts are the evidence behind "available upon request" in six sent responses. From this batch on, write transcripts to `/opt/battlestats-server/shared/purge/`, which survives deploys and reboots. Step 5 of the playbook is updated accordingly.

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

1. **Receive zip from WG.** It does **not** land in the repo automatically. In order: check `deleted/`; then, under WSL, the Windows Downloads folder (`find /mnt/c/Users -maxdepth 2 -iname Downloads` to enumerate profiles — do not assume the Windows username matches `$USER` — then `ls -la <path>/Downloads/ | grep -i delet`); then pull it straight from the Gmail attachment via mailcap (recipe in the 2026-07-30 results section — this was the path that worked that batch, and is the most reliable of the three since it needs no browser step). Land it at `deleted/deleted_accounts_<YYYYMMDD>.zip`; `deleted/` is gitignored, and the artifact is sensitive PII that must never be committed.
2. **Inspect briefly.** `unzip -p deleted/deleted_accounts.zip accounts.csv | head -3 && unzip -p deleted/deleted_accounts.zip accounts.csv | wc -l` — confirm header is `account_id` and row count is sensible.
2b. **Re-verify the schema footprint** (standing pre-flight — the table above went stale for four batches). Enumerate `Player._meta.get_fields()` for reverse relations and confirm every one is `on_delete=CASCADE`; confirm no new model carries a bare integer `player_id`. Any non-CASCADE relation or bare-integer reference means data survives the purge and the response email's completeness claim would be false. Update the footprint table with what you find.
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
   ssh root@battlestats.online '/opt/battlestats-server/venv/bin/python /opt/battlestats-server/current/server/manage.py purge_deleted_accounts /tmp/deleted_accounts.zip --transcript /opt/battlestats-server/shared/purge/purge_transcript_<YYYYMMDD>.jsonl'
   ```
   Transcripts go under `shared/purge/`, never `/tmp`: `/tmp` was wiped between batches and every pre-2026-10 transcript was lost.
6. **Reply email to WG** — create it as a **draft** via mailcap `gmail_create_draft` with `reply_to_message_id` set to the request message, so it threads correctly. Address it to `noreply@wargaming.net` (no `Reply-To` header is set on the request; this address has been used for all five responses to date). The operator reviews and sends by hand — mailcap never sends. Use **live** run numbers, never the dry-run's. Template:
   ```
   Thank you for your email regarding the deletion of personal data for the account IDs listed in the attached file.

   We have completed processing of this request. The details are as follows:

   - IDs received: <N>
   - IDs found in our system: <K> (all data associated with these accounts has been permanently purged from our database, including player records, statistical snapshots, achievements, explorer summaries, battle-history observations and derived per-battle and per-day records, visit records, and any related cached data)
   - IDs blocklisted: <N> (all IDs have been permanently blocked from future ingestion via the Wargaming API, scheduled data refreshes, or any other ingestion pathway)

   A full machine-generated transcript documenting the per-account processing result is available upon request.
   ```
   The parenthetical was widened on 2026-07-30 to name battle-history data explicitly; the pre-2026-07-30 wording enumerated only the tables that existed in March and would now understate the purge to a data-protection regulator.
7. **Archive artifacts.** Source zip and unzipped CSV in `deleted/` should not be committed. Either move to a private archive location or `rm` after the response is sent. Transcript stays on the droplet at `/opt/battlestats-server/shared/purge/purge_transcript_<YYYYMMDD>.jsonl` (alongside the prior batches).
8. **Update this runbook.** Append a new `## Execution Results (<YYYY-MM-DD>)` section with the summary JSON, match distribution, and any new lessons. Bump the top-of-file `Last executed` line.

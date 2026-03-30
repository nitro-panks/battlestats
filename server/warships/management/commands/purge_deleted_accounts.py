"""
Management command to purge deleted Wargaming accounts and blocklist them.

Usage:
    python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip
    python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip --transcript /path/to/output.jsonl
    python manage.py purge_deleted_accounts /path/to/deleted_accounts.zip --dry-run
"""
import csv
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from django.core.cache import cache
from django.db import transaction
from django.core.management.base import BaseCommand

from warships.models import (
    Clan,
    DeletedAccount,
    EntityVisitDaily,
    EntityVisitEvent,
    Player,
)

logger = logging.getLogger(__name__)

CACHE_KEY_TEMPLATES = [
    "player:detail:v1:{pid}",
    "clan_battles:player:{pid}",
    "warships:tasks:update_ranked_data_dispatch:{pid}",
    "warships:tasks:update_player_clan_battle_data_dispatch:{pid}",
    "warships:tasks:update_player_efficiency_data_dispatch:{pid}",
    "warships:tasks:update_player_data::{pid}:lock",
    "warships:tasks:update_battle_data::{pid}:lock",
    "player:refresh_dispatched:{pid}",
]

BATCH_SIZE = 500


def _parse_account_ids(file_path: str) -> list[int]:
    path = Path(file_path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                raise ValueError(f"No CSV file found in {path}")
            raw = zf.read(csv_names[0]).decode("utf-8")
    elif path.suffix == ".csv":
        raw = path.read_text()
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")

    reader = csv.DictReader(io.StringIO(raw))
    ids = []
    for row in reader:
        val = row.get("account_id", "").strip()
        if val.isdigit():
            ids.append(int(val))
    return ids


def _delete_cache_keys(player_id: int) -> int:
    keys = [t.format(pid=player_id) for t in CACHE_KEY_TEMPLATES]
    deleted = 0
    for key in keys:
        if cache.delete(key):
            deleted += 1
    return deleted


def _remove_from_list_keys(player_id: int) -> None:
    """Best-effort removal from Redis list keys. Silently skip if backend
    doesn't support lrem (e.g. LocMemCache in tests)."""
    list_keys = [
        "recently_viewed:players:v1",
        "landing:queue:players:random:v1",
        "landing:queue:players:random:eligible:v1",
    ]
    try:
        client = cache._cache.get_client()  # noqa: SLF001 — Redis-specific
    except (AttributeError, Exception):
        return
    for key in list_keys:
        try:
            client.lrem(key, 0, str(player_id))
        except Exception:
            pass


class Command(BaseCommand):
    help = "Purge deleted Wargaming accounts and blocklist their IDs."

    def add_arguments(self, parser):
        parser.add_argument(
            "file",
            help="Path to deleted_accounts.zip or accounts.csv",
        )
        parser.add_argument(
            "--transcript",
            default=None,
            help="Output path for JSONL transcript (default: purge_transcript_<timestamp>.jsonl)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without making changes",
        )

    def handle(self, *args, **options):
        file_path = options["file"]
        dry_run = options["dry_run"]
        transcript_path = options["transcript"] or (
            f"purge_transcript_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
        )

        account_ids = _parse_account_ids(file_path)
        if not account_ids:
            self.stderr.write("No account IDs found in input file.")
            return

        self.stdout.write(f"{'[DRY RUN] ' if dry_run else ''}Processing {len(account_ids)} account IDs...")

        account_id_set = set(account_ids)
        transcript_file = open(transcript_path, "w") if not dry_run else None

        totals = {
            "total_ids": len(account_ids),
            "found_in_db": 0,
            "not_found": 0,
            "total_player_rows": 0,
            "total_snapshot_rows": 0,
            "total_achievement_rows": 0,
            "total_explorer_rows": 0,
            "total_visit_event_rows": 0,
            "total_visit_daily_rows": 0,
            "total_cache_keys_deleted": 0,
            "total_clan_leaders_nulled": 0,
            "blocked": 0,
        }

        try:
            # Phase 1: Blocklist all IDs first (prevents re-entry during purge)
            self.stdout.write("Phase 1: Populating blocklist...")
            if not dry_run:
                existing_blocked = set(
                    DeletedAccount.objects.filter(
                        account_id__in=account_ids,
                    ).values_list("account_id", flat=True)
                )
                new_blocked = [
                    DeletedAccount(account_id=aid)
                    for aid in account_ids
                    if aid not in existing_blocked
                ]
                for i in range(0, len(new_blocked), BATCH_SIZE):
                    DeletedAccount.objects.bulk_create(
                        new_blocked[i:i + BATCH_SIZE],
                        ignore_conflicts=True,
                    )
                totals["blocked"] = len(account_ids)
                # Invalidate the in-memory cache so blocklist takes effect immediately
                from warships.blocklist import invalidate_blocklist_cache
                invalidate_blocklist_cache()
            else:
                totals["blocked"] = len(account_ids)

            self.stdout.write(f"  Blocklisted {len(account_ids)} IDs")

            # Phase 2: Delete player data in batches
            self.stdout.write("Phase 2: Purging player data...")
            batch_ids = list(account_ids)

            for i in range(0, len(batch_ids), BATCH_SIZE):
                batch = batch_ids[i:i + BATCH_SIZE]
                players = {
                    p.player_id: p
                    for p in Player.objects.filter(player_id__in=batch).select_related("clan")
                }

                for account_id in batch:
                    record = {
                        "account_id": account_id,
                        "found": account_id in players,
                        "blocklisted": True,
                    }

                    if account_id not in players:
                        totals["not_found"] += 1
                        record["cache_keys_deleted"] = 0
                        if not dry_run and transcript_file:
                            transcript_file.write(json.dumps(record) + "\n")
                        continue

                    player = players[account_id]
                    record["player_name"] = player.name
                    record["player_pk"] = player.pk

                    # Count related rows before deletion
                    from warships.models import PlayerAchievementStat, Snapshot, PlayerExplorerSummary
                    snapshot_count = Snapshot.objects.filter(player=player).count()
                    achievement_count = PlayerAchievementStat.objects.filter(player=player).count()
                    explorer_count = PlayerExplorerSummary.objects.filter(player=player).count()
                    visit_event_count = EntityVisitEvent.objects.filter(
                        entity_type="player", entity_id=account_id,
                    ).count()
                    visit_daily_count = EntityVisitDaily.objects.filter(
                        entity_type="player", entity_id=account_id,
                    ).count()
                    clan_leader_match = Clan.objects.filter(leader_id=account_id).exists()

                    record["rows_deleted"] = {
                        "player": 1,
                        "snapshots": snapshot_count,
                        "achievements": achievement_count,
                        "explorer_summary": explorer_count,
                        "visit_events": visit_event_count,
                        "visit_daily": visit_daily_count,
                    }
                    record["clan_leader_nulled"] = clan_leader_match

                    if not dry_run:
                        with transaction.atomic():
                            # Manual deletes (not cascaded)
                            EntityVisitEvent.objects.filter(
                                entity_type="player", entity_id=account_id,
                            ).delete()
                            EntityVisitDaily.objects.filter(
                                entity_type="player", entity_id=account_id,
                            ).delete()
                            if clan_leader_match:
                                Clan.objects.filter(leader_id=account_id).update(
                                    leader_id=None, leader_name=None,
                                )
                            # CASCADE handles Snapshot, Achievement, Explorer
                            player.delete()

                        cache_deleted = _delete_cache_keys(account_id)
                        _remove_from_list_keys(account_id)
                    else:
                        cache_deleted = len(CACHE_KEY_TEMPLATES)

                    record["cache_keys_deleted"] = cache_deleted

                    totals["found_in_db"] += 1
                    totals["total_player_rows"] += 1
                    totals["total_snapshot_rows"] += snapshot_count
                    totals["total_achievement_rows"] += achievement_count
                    totals["total_explorer_rows"] += explorer_count
                    totals["total_visit_event_rows"] += visit_event_count
                    totals["total_visit_daily_rows"] += visit_daily_count
                    totals["total_cache_keys_deleted"] += cache_deleted
                    if clan_leader_match:
                        totals["total_clan_leaders_nulled"] += 1

                    if not dry_run and transcript_file:
                        transcript_file.write(json.dumps(record) + "\n")

                    if totals["found_in_db"] % 100 == 0:
                        self.stdout.write(f"  Purged {totals['found_in_db']} players...")

            # Write summary line
            summary = {"summary": True, **totals}
            if not dry_run and transcript_file:
                transcript_file.write(json.dumps(summary) + "\n")

        finally:
            if transcript_file:
                transcript_file.close()

        self.stdout.write("")
        self.stdout.write(f"{'[DRY RUN] ' if dry_run else ''}Purge complete.")
        self.stdout.write(json.dumps(summary, indent=2))
        if not dry_run:
            self.stdout.write(f"\nTranscript written to: {transcript_path}")

"""Discover players not in the database by scanning WG account ID ranges.

The clan crawl only discovers players who are in clans. This command scans
sequential account ID ranges via the WG `account/info` endpoint (100 IDs
per call) to find players we don't have — including clanless players.

Discovered players meeting the quality threshold (min battles + WR) are
created in the database and become eligible for the enrichment pipeline.

Usage:
    # Discover NA players in the densest ID range
    python manage.py discover_players --realm na --batch 50000

    # Target a specific ID range
    python manage.py discover_players --realm na --start 1000000000 --end 1010000000

    # Dry run to see how many would be found
    python manage.py discover_players --realm na --batch 10000 --dry-run

    # Parallel partitions
    python manage.py discover_players --realm na --partition 0 --num-partitions 4
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests
from django.core.management.base import BaseCommand

from warships.api.client import DEFAULT_REALM, REALM_BASE_URLS, get_base_url
from warships.models import Player, VALID_REALMS

log = logging.getLogger("discover")

DEFAULT_BATCH = 50000  # IDs to scan per invocation
DEFAULT_MIN_BATTLES = 500
DEFAULT_MIN_WR = 48.0
DEFAULT_DELAY = 0.1  # seconds between API calls
IDS_PER_CALL = 100
REQUEST_TIMEOUT = 15

# Empirically determined dense ID ranges per realm
REALM_ID_RANGES = {
    'na': (1000000000, 1076100000),
    'eu': (500000000, 725500000),
    'asia': (2000000000, 2025000000),
}


def _get_app_id():
    import os
    app_id = os.environ.get('WG_APP_ID')
    if not app_id:
        raise RuntimeError("WG_APP_ID environment variable is not set")
    return app_id


def _fetch_account_batch(account_ids: list[int], realm: str, app_id: str) -> dict:
    """Fetch basic stats for up to 100 account IDs from the WG API."""
    base_url = get_base_url(realm)
    ids_str = ','.join(str(i) for i in account_ids)
    resp = requests.get(
        f'{base_url}account/info/',
        params={
            'application_id': app_id,
            'account_id': ids_str,
            'fields': 'account_id,nickname,statistics.pvp.battles,statistics.pvp.wins,'
                      'last_battle_time,hidden_profile',
        },
        timeout=REQUEST_TIMEOUT,
    )
    data = resp.json()
    if data.get('status') != 'ok':
        error = data.get('error', {})
        log.warning("WG API error: %s", error.get('message', data))
        return {}
    return data.get('data', {})


def discover_players(
    realm: str = DEFAULT_REALM,
    batch: int = DEFAULT_BATCH,
    start: int | None = None,
    end: int | None = None,
    min_battles: int = DEFAULT_MIN_BATTLES,
    min_wr: float = DEFAULT_MIN_WR,
    delay: float = DEFAULT_DELAY,
    dry_run: bool = False,
    partition: int = 0,
    num_partitions: int = 1,
    heartbeat_callback=None,
) -> dict:
    """Scan an account ID range and create Player records for quality accounts."""
    app_id = _get_app_id()

    # Determine scan range
    realm_start, realm_end = REALM_ID_RANGES.get(realm, (0, 0))
    scan_start = start if start is not None else realm_start
    scan_end = end if end is not None else realm_end

    if scan_start >= scan_end:
        return {"status": "error", "reason": "invalid range"}

    total_range = scan_end - scan_start

    # Partition the range
    if num_partitions > 1:
        partition_size = total_range // num_partitions
        scan_start = scan_start + partition * partition_size
        scan_end = scan_start + partition_size
        if partition == num_partitions - 1:
            scan_end = (start if start is not None else realm_start) + total_range

    # Cap to batch size
    ids_to_scan = min(batch, scan_end - scan_start)
    scan_end = scan_start + ids_to_scan
    api_calls = (ids_to_scan + IDS_PER_CALL - 1) // IDS_PER_CALL

    log.info(
        "Player discovery: realm=%s range=%d-%d (%d IDs, %d API calls, partition=%d/%d)",
        realm, scan_start, scan_end, ids_to_scan, api_calls, partition, num_partitions,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "realm": realm,
            "range": [scan_start, scan_end],
            "ids_to_scan": ids_to_scan,
            "api_calls": api_calls,
        }

    # Pre-load existing player IDs in this range
    existing_ids = set(
        Player.objects.filter(
            realm=realm,
            player_id__gte=scan_start,
            player_id__lt=scan_end,
        ).values_list('player_id', flat=True)
    )

    scanned = 0
    accounts_found = 0
    already_known = 0
    created = 0
    skipped_quality = 0
    skipped_hidden = 0
    errors = 0

    cursor = scan_start
    while cursor < scan_end:
        if heartbeat_callback:
            heartbeat_callback()

        chunk_size = min(IDS_PER_CALL, scan_end - cursor)
        ids = list(range(cursor, cursor + chunk_size))
        cursor += chunk_size
        scanned += chunk_size

        try:
            results = _fetch_account_batch(ids, realm, app_id)
        except Exception:
            log.exception("API call failed at ID %d", ids[0])
            errors += 1
            if delay > 0:
                time.sleep(delay)
            continue

        for aid_str, info in results.items():
            if info is None:
                continue
            if info.get('hidden_profile'):
                skipped_hidden += 1
                continue

            aid = int(aid_str)
            accounts_found += 1

            if aid in existing_ids:
                already_known += 1
                continue

            pvp = (info.get('statistics') or {}).get('pvp') or {}
            battles = pvp.get('battles', 0)
            wins = pvp.get('wins', 0)
            wr = round(wins / battles * 100, 2) if battles > 0 else 0.0
            name = info.get('nickname', '')

            if battles < min_battles or wr < min_wr:
                skipped_quality += 1
                continue

            # Create the player record
            player, was_created = Player.objects.get_or_create(
                player_id=aid,
                realm=realm,
                defaults={
                    'name': name,
                    'pvp_battles': battles,
                    'pvp_ratio': wr,
                    'last_battle_date': datetime.fromtimestamp(
                        info.get('last_battle_time', 0), tz=timezone.utc
                    ) if info.get('last_battle_time') else None,
                },
            )
            if was_created:
                created += 1
                log.info(
                    "Discovered %s [%s]: %db %.1f%%WR (id=%d) [%d/%d]",
                    name, realm.upper(), battles, wr, aid, created, scanned,
                )

        if delay > 0:
            time.sleep(delay)

        # Progress log every 100 batches
        if scanned % (IDS_PER_CALL * 100) == 0:
            pct = scanned / ids_to_scan * 100
            log.info(
                "Discovery progress: %d/%d (%.1f%%) — %d found, %d known, %d created",
                scanned, ids_to_scan, pct, accounts_found, already_known, created,
            )

    summary = {
        "status": "completed",
        "realm": realm,
        "range": [scan_start, scan_end],
        "scanned": scanned,
        "api_calls": scanned // IDS_PER_CALL,
        "accounts_found": accounts_found,
        "already_known": already_known,
        "created": created,
        "skipped_quality": skipped_quality,
        "skipped_hidden": skipped_hidden,
        "errors": errors,
    }
    log.info("Discovery complete: %s", summary)
    return summary


class Command(BaseCommand):
    help = "Discover players by scanning WG account ID ranges."

    def add_arguments(self, parser):
        parser.add_argument(
            "--realm", choices=sorted(VALID_REALMS), default='na',
            help="Target realm (default: na).",
        )
        parser.add_argument(
            "--batch", type=int, default=DEFAULT_BATCH,
            help=f"Max IDs to scan (default {DEFAULT_BATCH}).",
        )
        parser.add_argument(
            "--start", type=int, default=None,
            help="Start of ID range (default: realm-specific).",
        )
        parser.add_argument(
            "--end", type=int, default=None,
            help="End of ID range (default: realm-specific).",
        )
        parser.add_argument(
            "--min-battles", type=int, default=DEFAULT_MIN_BATTLES,
            help=f"Min PvP battles to import (default {DEFAULT_MIN_BATTLES}).",
        )
        parser.add_argument(
            "--min-wr", type=float, default=DEFAULT_MIN_WR,
            help=f"Min win rate %% to import (default {DEFAULT_MIN_WR}).",
        )
        parser.add_argument(
            "--delay", type=float, default=DEFAULT_DELAY,
            help=f"Seconds between API calls (default {DEFAULT_DELAY}).",
        )
        parser.add_argument(
            "--partition", type=int, default=0,
            help="Partition index (0-based) for parallel invocations.",
        )
        parser.add_argument(
            "--num-partitions", type=int, default=1,
            help="Total number of partitions.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report scan plan without executing.",
        )

    def handle(self, *args, **options):
        summary = discover_players(
            realm=options["realm"],
            batch=options["batch"],
            start=options["start"],
            end=options["end"],
            min_battles=options["min_battles"],
            min_wr=options["min_wr"],
            delay=options["delay"],
            dry_run=options["dry_run"],
            partition=options["partition"],
            num_partitions=options["num_partitions"],
        )
        self.stdout.write(self.style.SUCCESS(str(summary)))

"""Unified player enrichment crawler.

Fills battles_json, tiers_json, type_json, randoms_json, ranked_json,
and snapshot/activity data for players who are missing it.  Alternates
between NA and EU to give both realms steady progress.

Players are selected by overall win-rate (descending) with configurable
minimum PvP battle and WR thresholds so low-value accounts are skipped.

Can run as a management command (manual / cron) or be invoked from a
Celery task for scheduled background enrichment.

Batch API optimisations
-----------------------
* Ship cache pre-warm: bulk-loads all Ship records into Redis before the
  loop so per-ship DB lookups inside update_battle_data are cache hits.
* refresh_player=False on snapshot: skips the redundant account/info +
  clans/accountinfo API calls (2 per player) — the clan crawler already
  keeps these current.
* update_snapshot_data already calls update_activity_data internally, so
  we do NOT call update_activity_data separately.
* Net cost per player: ~2 API calls (ships/stats + ranked) instead of ~5.
"""
from __future__ import annotations

import logging
import time
from itertools import zip_longest

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import F

from warships.models import DEFAULT_REALM, Player, Ship, VALID_REALMS

log = logging.getLogger("enrich")

DEFAULT_BATCH = 500
DEFAULT_MIN_PVP_BATTLES = 500
DEFAULT_MIN_WR = 48.0
DEFAULT_DELAY = 0.2  # seconds between players (not per API call)


def _prewarm_ship_cache() -> int:
    """Bulk-load all complete Ship records into Redis.

    Avoids per-ship DB queries inside update_battle_data when processing
    ships the crawler hasn't seen yet in this worker's cache.
    Returns the number of ships cached.
    """
    from warships.api.ships import _ship_cache_is_complete

    ships = Ship.objects.filter(
        name__gt="", ship_type__gt="", tier__isnull=False,
    ).exclude(chart_name="")
    count = 0
    for ship in ships.iterator(chunk_size=500):
        if _ship_cache_is_complete(ship):
            cache.set(f"ship:{ship.ship_id}", ship, 86400)
            count += 1
    log.info("Pre-warmed %d ship cache entries", count)
    return count


def _candidates(realm: str, min_pvp_battles: int, min_wr: float, limit: int):
    """Return players missing battles_json, ordered by WR desc."""
    return list(
        Player.objects.filter(
            realm=realm,
            is_hidden=False,
            pvp_battles__gte=min_pvp_battles,
            pvp_ratio__gte=min_wr,
            battles_json__isnull=True,
        )
        .exclude(name="")
        .order_by(
            F("pvp_ratio").desc(nulls_last=True),
            F("pvp_battles").desc(nulls_last=True),
            "name",
        )
        .values_list("player_id", "name", "pvp_ratio", "pvp_battles", "realm")
        [:limit]
    )


def _interleave(na_rows, eu_rows):
    """Yield (player_id, name, wr, battles, realm) alternating NA / EU."""
    for na, eu in zip_longest(na_rows, eu_rows):
        if na is not None:
            yield na
        if eu is not None:
            yield eu


def enrich_players(
    batch: int = DEFAULT_BATCH,
    min_pvp_battles: int = DEFAULT_MIN_PVP_BATTLES,
    min_wr: float = DEFAULT_MIN_WR,
    delay: float = DEFAULT_DELAY,
    dry_run: bool = False,
    realms: tuple[str, ...] | None = None,
    heartbeat_callback=None,
) -> dict:
    """Run one enrichment pass.  Returns summary dict."""
    from warships.data import (
        update_battle_data,
        update_ranked_data,
        update_snapshot_data,
    )

    target_realms = realms or tuple(sorted(VALID_REALMS))

    # Fetch candidates per realm (half the batch each when two realms)
    per_realm = max(batch // len(target_realms), 1)
    realm_candidates = {}
    for realm in target_realms:
        realm_candidates[realm] = _candidates(realm, min_pvp_battles, min_wr, per_realm)

    # Build interleaved queue
    if len(target_realms) == 2 and 'na' in target_realms and 'eu' in target_realms:
        queue = list(_interleave(
            realm_candidates.get('na', []),
            realm_candidates.get('eu', []),
        ))
    else:
        queue = []
        for realm in target_realms:
            queue.extend(realm_candidates.get(realm, []))

    total_candidates = {r: len(rows) for r, rows in realm_candidates.items()}
    log.info(
        "Enrichment pass: %d players queued (candidates: %s, min_pvp=%d, min_wr=%.1f)",
        len(queue), total_candidates, min_pvp_battles, min_wr,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "candidates": total_candidates,
            "queue_size": len(queue),
        }

    # Pre-warm ship cache so per-ship lookups in update_battle_data are
    # Redis hits instead of individual DB queries + API calls.
    _prewarm_ship_cache()

    enriched = 0
    errors = 0
    by_realm = {r: 0 for r in target_realms}

    for player_id, name, wr, battles, realm in queue:
        if heartbeat_callback:
            heartbeat_callback()

        try:
            # 1. Battle data — 1 API call (ships/stats) + cached ship lookups
            update_battle_data(player_id, realm=realm)

            # 2. Snapshot + activity — 0 API calls (refresh_player=False
            #    skips the redundant account/info + clans/accountinfo calls;
            #    update_activity_data is called internally by update_snapshot_data)
            update_snapshot_data(player_id, realm=realm, refresh_player=False)

            # 3. Ranked data — 2 API calls (seasons metadata cached + account rank_info + shipstats)
            update_ranked_data(player_id, realm=realm)

            enriched += 1
            by_realm[realm] = by_realm.get(realm, 0) + 1
            log.info(
                "Enriched %s [%s] WR=%.1f%% battles=%d (%d/%d)",
                name, realm.upper(), wr, battles, enriched, len(queue),
            )
        except Exception:
            log.exception("Failed to enrich player_id=%s name=%s realm=%s", player_id, name, realm)
            errors += 1

        if delay > 0:
            time.sleep(delay)

    summary = {
        "status": "completed",
        "enriched": enriched,
        "errors": errors,
        "by_realm": by_realm,
        "candidates": total_candidates,
    }
    log.info("Enrichment pass complete: %s", summary)
    return summary


class Command(BaseCommand):
    help = "Enrich players missing battle/ranked/activity data, ordered by WR, alternating realms."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch", type=int, default=DEFAULT_BATCH,
            help=f"Max players to process (default {DEFAULT_BATCH}).",
        )
        parser.add_argument(
            "--min-pvp-battles", type=int, default=DEFAULT_MIN_PVP_BATTLES,
            help=f"Min PvP battles to be eligible (default {DEFAULT_MIN_PVP_BATTLES}).",
        )
        parser.add_argument(
            "--min-wr", type=float, default=DEFAULT_MIN_WR,
            help=f"Min win rate %% to be eligible (default {DEFAULT_MIN_WR}).",
        )
        parser.add_argument(
            "--delay", type=float, default=DEFAULT_DELAY,
            help=f"Seconds between players (default {DEFAULT_DELAY}).",
        )
        parser.add_argument(
            "--realm", choices=sorted(VALID_REALMS), default=None,
            help="Restrict to one realm (default: alternate both).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report candidates without processing.",
        )

    def handle(self, *args, **options):
        realms = (options["realm"],) if options["realm"] else None
        summary = enrich_players(
            batch=options["batch"],
            min_pvp_battles=options["min_pvp_battles"],
            min_wr=options["min_wr"],
            delay=options["delay"],
            dry_run=options["dry_run"],
            realms=realms,
        )

        self.stdout.write(self.style.SUCCESS(f"\n=== Enrichment Summary ==="))
        for key, val in summary.items():
            self.stdout.write(f"  {key}: {val}")

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
* Parallel API calls: ships/stats and ranked account_info are fetched
  concurrently, cutting per-player API wait from ~900ms to ~600ms.
* Partitioned batches: multiple function invocations can process disjoint
  player slices concurrently via partition/num_partitions parameters.
* Net cost per player: ~3 API calls (ships/stats + rank_info +
  ranked/shipstats) with 2 of the 3 running in parallel.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from itertools import zip_longest

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import F

from warships.models import DEFAULT_REALM, Player, Ship, VALID_REALMS

log = logging.getLogger("enrich")

DEFAULT_BATCH = 500
DEFAULT_MIN_PVP_BATTLES = 500
DEFAULT_MIN_WR = 48.0
DEFAULT_DELAY = 0.05  # seconds between players (reduced from 0.2 — safe with parallel calls)


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


def _candidates(realm: str, min_pvp_battles: int, min_wr: float, limit: int,
                partition: int = 0, num_partitions: int = 1):
    """Return players missing battles_json, ordered by WR desc.

    When num_partitions > 1, only returns players whose player_id falls
    into the given partition (player_id % num_partitions == partition).
    """
    qs = (
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
    )

    if num_partitions > 1:
        # Use raw SQL annotation for modulo partitioning
        from django.db.models import Value
        from django.db.models.functions import Mod
        qs = qs.annotate(
            _partition=Mod(F("player_id"), Value(num_partitions))
        ).filter(_partition=partition)

    return list(
        qs.values_list("player_id", "name", "pvp_ratio", "pvp_battles", "realm")
        [:limit]
    )


def _interleave(na_rows, eu_rows):
    """Yield (player_id, name, wr, battles, realm) alternating NA / EU."""
    for na, eu in zip_longest(na_rows, eu_rows):
        if na is not None:
            yield na
        if eu is not None:
            yield eu


def _enrich_player_parallel(player_id, realm: str):
    """Enrich a single player with parallelized API calls.

    Phase 1 (parallel):  ships/stats  +  ranked account_info
    Phase 2 (sequential): ranked shipstats (needs season IDs from phase 1)
    Phase 3 (local):      battle processing, snapshot, explorer summary
    """
    from warships.api.ships import (
        _fetch_ship_stats_for_player,
        _fetch_ship_info,
        _fetch_ranked_ship_stats_for_player,
    )
    from warships.api.players import _fetch_ranked_account_info
    from warships.data import (
        _build_ship_row_metadata,
        _get_ranked_seasons_metadata,
        _aggregate_ranked_seasons,
        _build_top_ranked_ship_names_by_season,
        _extract_randoms_rows,
        _aggregate_battles_by_key,
        update_snapshot_data,
        refresh_player_explorer_summary,
    )

    player = Player.objects.get(player_id=player_id, realm=realm)

    # ── Phase 1: parallel API calls ──────────────────────────
    ship_data = None
    account_data = None

    with ThreadPoolExecutor(max_workers=2) as ex:
        ship_future = ex.submit(
            _fetch_ship_stats_for_player, player_id, realm=realm)
        rank_future = ex.submit(
            _fetch_ranked_account_info, int(player_id), realm=realm)

        ship_data = ship_future.result()
        account_data = rank_future.result()

    # ── Phase 2: ranked ship stats (needs season IDs from phase 1) ──
    rank_info = account_data.get('rank_info') if account_data else None
    ranked_ship_stats_rows = []
    requested_season_ids = []

    if rank_info:
        requested_season_ids = sorted(
            [int(sid) for sid in rank_info.keys() if str(sid).isdigit()]
        )
        if requested_season_ids:
            ranked_ship_stats_rows = _fetch_ranked_ship_stats_for_player(
                int(player_id), season_ids=requested_season_ids, realm=realm)

    # ── Phase 3a: process battle data ────────────────────────
    battles_rows = None
    if ship_data:
        prepared_data = []
        for ship in ship_data:
            ship_model = _fetch_ship_info(ship['ship_id'])
            ship_metadata = _build_ship_row_metadata(
                ship.get('ship_id'), ship_model)

            pvp_battles = ship['pvp']['battles']
            wins = ship['pvp']['wins']
            losses = ship['pvp']['losses']
            frags = ship['pvp']['frags']
            battles = ship['battles']
            distance = ship['distance']

            prepared_data.append({
                'ship_id': ship_metadata['ship_id'],
                'ship_name': ship_metadata['ship_name'],
                'ship_chart_name': ship_metadata['ship_chart_name'],
                'ship_tier': ship_metadata['ship_tier'],
                'all_battles': battles,
                'distance': distance,
                'wins': wins,
                'losses': losses,
                'ship_type': ship_metadata['ship_type'],
                'pve_battles': battles - (wins + losses),
                'pvp_battles': pvp_battles,
                'win_ratio': round(wins / pvp_battles, 2) if pvp_battles > 0 else 0,
                'kdr': round(frags / pvp_battles, 2) if pvp_battles > 0 else 0,
            })

        battles_rows = sorted(
            prepared_data, key=lambda x: x.get('pvp_battles', 0), reverse=True)

        # Derive tier, type, randoms aggregations
        tier_aggregates = {tier: {'pvp_battles': 0, 'wins': 0}
                          for tier in range(1, 12)}
        for row in battles_rows:
            tier = row.get('ship_tier')
            if isinstance(tier, int) and tier in tier_aggregates:
                tier_aggregates[tier]['pvp_battles'] += int(
                    row.get('pvp_battles', 0) or 0)
                tier_aggregates[tier]['wins'] += int(row.get('wins', 0) or 0)

        tiers_data = []
        for tier in range(11, 0, -1):
            b = tier_aggregates[tier]['pvp_battles']
            w = tier_aggregates[tier]['wins']
            tiers_data.append({
                'ship_tier': tier,
                'pvp_battles': b,
                'wins': w,
                'win_ratio': round(w / b, 2) if b > 0 else 0,
            })

        now = datetime.now()
        player.battles_json = battles_rows
        player.battles_updated_at = now
        player.tiers_json = tiers_data
        player.tiers_updated_at = now
        player.type_json = _aggregate_battles_by_key(battles_rows, 'ship_type')
        player.type_updated_at = now
        player.randoms_json = _extract_randoms_rows(battles_rows, limit=20)
        player.randoms_updated_at = now
        player.save(update_fields=[
            'battles_json', 'battles_updated_at',
            'tiers_json', 'tiers_updated_at',
            'type_json', 'type_updated_at',
            'randoms_json', 'randoms_updated_at',
        ])
    else:
        # No ship data — set empty list to remove from candidate pool
        # (candidates query filters on battles_json__isnull=True)
        player.battles_json = []
        player.battles_updated_at = datetime.now()
        player.save(update_fields=['battles_json', 'battles_updated_at'])

    # ── Phase 3b: process ranked data ────────────────────────
    ranked_rows = None
    if rank_info and requested_season_ids:
        season_meta = _get_ranked_seasons_metadata()
        top_ship_names = _build_top_ranked_ship_names_by_season(
            ranked_ship_stats_rows, requested_season_ids)
        ranked_rows = _aggregate_ranked_seasons(
            rank_info, season_meta, top_ship_names_by_season=top_ship_names)
    else:
        ranked_rows = []

    player.ranked_json = ranked_rows
    player.ranked_updated_at = datetime.now()
    player.save(update_fields=['ranked_json', 'ranked_updated_at'])

    # ── Phase 3c: snapshot + activity (no API calls) ─────────
    update_snapshot_data(player_id, realm=realm, refresh_player=False)

    # ── Phase 3d: explorer summary ─���─────────────────────────
    refresh_player_explorer_summary(
        player, battles_rows=battles_rows, ranked_rows=ranked_rows)


def enrich_players(
    batch: int = DEFAULT_BATCH,
    min_pvp_battles: int = DEFAULT_MIN_PVP_BATTLES,
    min_wr: float = DEFAULT_MIN_WR,
    delay: float = DEFAULT_DELAY,
    dry_run: bool = False,
    realms: tuple[str, ...] | None = None,
    heartbeat_callback=None,
    partition: int = 0,
    num_partitions: int = 1,
) -> dict:
    """Run one enrichment pass.  Returns summary dict."""
    target_realms = realms or tuple(sorted(VALID_REALMS))

    # Fetch candidates per realm (half the batch each when two realms)
    per_realm = max(batch // len(target_realms), 1)
    realm_candidates = {}
    for realm in target_realms:
        realm_candidates[realm] = _candidates(
            realm, min_pvp_battles, min_wr, per_realm,
            partition=partition, num_partitions=num_partitions,
        )

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
        "Enrichment pass: %d players queued (candidates: %s, min_pvp=%d, min_wr=%.1f, partition=%d/%d)",
        len(queue), total_candidates, min_pvp_battles, min_wr,
        partition, num_partitions,
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
            _enrich_player_parallel(player_id, realm=realm)
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
            "--partition", type=int, default=0,
            help="Partition index (0-based) for parallel invocations.",
        )
        parser.add_argument(
            "--num-partitions", type=int, default=1,
            help="Total number of partitions.",
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
            partition=options["partition"],
            num_partitions=options["num_partitions"],
        )

        self.stdout.write(self.style.SUCCESS(f"\n=== Enrichment Summary ==="))
        for key, val in summary.items():
            self.stdout.write(f"  {key}: {val}")

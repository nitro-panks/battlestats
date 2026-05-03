"""Unified player enrichment crawler.

Fills battles_json, tiers_json, type_json, randoms_json, ranked_json,
and snapshot/activity data for players who are missing it.

Players are selected with configurable minimum PvP battle and WR
thresholds so low-value accounts are skipped.

Can run as a management command (manual / cron) or be invoked from a
Celery task for scheduled background enrichment.

Batch API optimisations
-----------------------
* Bulk API fetches: ships/stats and ranked accountinfo are fetched for
  up to BULK_API_BATCH_SIZE players per API call (WG supports up to 100
  comma-separated account_ids). This reduces per-player API overhead
  from 2+ round-trips to ~0.02 per player for the heavy endpoints.
* Ship cache pre-warm: bulk-loads all Ship records into Redis before the
  loop so per-ship DB lookups inside update_battle_data are cache hits.
* refresh_player=False on snapshot: skips the redundant account/info +
  clans/accountinfo API calls (2 per player) -- the clan crawler already
  keeps these current.
* update_snapshot_data already calls update_activity_data internally, so
  we do NOT call update_activity_data separately.
* Partitioned batches: multiple function invocations can process disjoint
  player slices concurrently via partition/num_partitions parameters.
"""
from __future__ import annotations

import enum
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from itertools import zip_longest

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from warships.models import DEFAULT_REALM, Player, Ship, VALID_REALMS

log = logging.getLogger("enrich")

DEFAULT_BATCH = 500
DEFAULT_MIN_PVP_BATTLES = 500
DEFAULT_MIN_WR = 0.0
DEFAULT_DELAY = 0.0
DEFAULT_MAX_INACTIVE_DAYS = 365
BULK_API_BATCH_SIZE = 100  # max account_ids per WG API call

# Health-check tunables (env-overridable)
DQ_SAMPLE_EVERY_PASSES = int(os.environ.get("ENRICH_DQ_SAMPLE_EVERY_PASSES", "10"))
DQ_SAMPLE_SIZE = int(os.environ.get("ENRICH_DQ_SAMPLE_SIZE", "20"))
DQ_ENABLED = os.environ.get("ENRICH_DQ_ENABLED", "1") == "1"
RATIO_GUARD_MIN = float(os.environ.get("ENRICH_RATIO_GUARD_MIN", "0.05"))
RATIO_GUARD_MIN_SAMPLE = int(os.environ.get("ENRICH_RATIO_GUARD_MIN_SAMPLE", "50"))
RATIO_GUARD_MAX_CONSECUTIVE = int(os.environ.get("ENRICH_RATIO_GUARD_MAX_CONSECUTIVE", "3"))


class EnrichOutcome(enum.Enum):
    ENRICHED = "enriched"   # real ship data written
    EMPTY = "empty"         # marked battles_json=[] because WG returned no ships
    SKIPPED = "skipped"     # transient failure, left eligible for retry


def _prewarm_ship_cache() -> int:
    """Bulk-load all complete Ship records into Redis."""
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
    """Return players missing battles_json, ordered by WR desc."""
    qs = (
        Player.objects.filter(
            realm=realm,
            enrichment_status=Player.ENRICHMENT_PENDING,
            is_hidden=False,
            pvp_battles__gte=min_pvp_battles,
            pvp_ratio__gte=min_wr,
            days_since_last_battle__lte=DEFAULT_MAX_INACTIVE_DAYS,
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


# ── Bulk API fetchers ────────────────────────────────────────

def _bulk_fetch_ship_stats(player_ids: list[int], realm: str) -> tuple[dict, str | None]:
    """Fetch ships/stats for up to 100 players. Returns (data, error_code)."""
    from warships.api.client import make_api_request_typed
    params = {"account_id": ",".join(str(pid) for pid in player_ids)}
    log.info("Bulk fetching ships/stats for %d players [%s]", len(player_ids), realm.upper())
    data, err = make_api_request_typed("ships/stats/", params, realm=realm)
    return (data if isinstance(data, dict) else {}), err


def _bulk_fetch_ranked_account_info(player_ids: list[int], realm: str) -> tuple[dict, str | None]:
    """Fetch seasons/accountinfo for up to 100 players. Returns (data, error_code)."""
    from warships.api.client import make_api_request_typed
    params = {
        "account_id": ",".join(str(pid) for pid in player_ids),
        "fields": "rank_info",
    }
    log.info("Bulk fetching ranked info for %d players [%s]", len(player_ids), realm.upper())
    data, err = make_api_request_typed("seasons/accountinfo/", params, realm=realm)
    return (data if isinstance(data, dict) else {}), err


def _fetch_ranked_account_info_single(player_id: int, realm: str) -> dict | None:
    """Per-player seasons/accountinfo fetch for poison-batch fallback."""
    from warships.api.client import make_api_request
    data = make_api_request(
        "seasons/accountinfo/",
        {"account_id": str(player_id), "fields": "rank_info"},
        realm=realm,
    )
    if isinstance(data, dict):
        return data.get(str(player_id))
    return None


def _per_player_ship_fallback(player_ids: list[int], realm: str) -> dict:
    """Fallback: fetch ships/stats individually to isolate poison IDs."""
    from warships.api.ships import _fetch_ship_stats_for_player
    out: dict = {}
    for pid in player_ids:
        try:
            r = _fetch_ship_stats_for_player(pid, realm=realm)
            if r is not None:
                out[str(pid)] = r
            else:
                out[str(pid)] = None  # explicit empty -> EMPTY outcome
        except Exception:
            log.warning("Per-player ship fallback failed for %s [%s]", pid, realm)
            out[str(pid)] = "SKIP"  # sentinel: transient
    return out


def _per_player_rank_fallback(player_ids: list[int], realm: str) -> dict:
    """Fallback: fetch seasons/accountinfo individually."""
    out: dict = {}
    for pid in player_ids:
        try:
            r = _fetch_ranked_account_info_single(pid, realm=realm)
            out[str(pid)] = r
        except Exception:
            log.warning("Per-player rank fallback failed for %s [%s]", pid, realm)
            out[str(pid)] = None
    return out


# ── Per-player processing (uses pre-fetched bulk data) ───────

def _process_player_ship_data(player, ship_data_list):
    """Process raw ship stats. Returns (battles_rows, EnrichOutcome).

    - ship_data_list is None  -> SKIPPED (transient, leave eligible)
    - ship_data_list is []    -> EMPTY   (genuine no-ships, mark as checked)
    - ship_data_list has rows -> ENRICHED
    """
    from warships.api.ships import _fetch_ship_info
    from warships.data import (
        _build_ship_row_metadata,
        extract_randoms_rows,
        _aggregate_battles_by_key,
    )

    if ship_data_list is None:
        return None, EnrichOutcome.SKIPPED

    if not ship_data_list:
        # Empty list = WG confirmed the player has no ship records.
        # Only write this when we TRUST the source (post-fallback).
        now = datetime.now()
        player.battles_json = []
        player.battles_updated_at = now
        player.enrichment_status = Player.ENRICHMENT_EMPTY
        player.save(update_fields=[
            'battles_json', 'battles_updated_at', 'enrichment_status'])
        return [], EnrichOutcome.EMPTY

    prepared_data = []
    for ship in ship_data_list:
        ship_model = _fetch_ship_info(ship['ship_id'])
        ship_metadata = _build_ship_row_metadata(
            ship.get('ship_id'), ship_model)

        pvp = ship.get('pvp') or {}
        pvp_battles = pvp.get('battles', 0)
        wins = pvp.get('wins', 0)
        losses = pvp.get('losses', 0)
        frags = pvp.get('frags', 0)
        battles = ship.get('battles', 0)
        distance = ship.get('distance', 0)

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
    player.randoms_json = extract_randoms_rows(battles_rows, limit=20)
    player.randoms_updated_at = now
    player.enrichment_status = Player.ENRICHMENT_ENRICHED
    player.save(update_fields=[
        'battles_json', 'battles_updated_at',
        'tiers_json', 'tiers_updated_at',
        'type_json', 'type_updated_at',
        'randoms_json', 'randoms_updated_at',
        'enrichment_status',
    ])
    return battles_rows, EnrichOutcome.ENRICHED


def _process_player_ranked_data(player, rank_info, realm: str):
    """Fetch ranked ship stats and save ranked_json.

    This is still per-player because seasons/shipstats does not support
    multi-account lookups.
    """
    from warships.api.ships import _fetch_ranked_ship_stats_for_player
    from warships.data import (
        _get_ranked_seasons_metadata,
        _aggregate_ranked_seasons,
        _build_top_ranked_ship_names_by_season,
    )

    ranked_rows = []
    if rank_info:
        requested_season_ids = sorted(
            [int(sid) for sid in rank_info.keys() if str(sid).isdigit()]
        )
        if requested_season_ids:
            ranked_ship_stats_rows = _fetch_ranked_ship_stats_for_player(
                int(player.player_id), season_ids=requested_season_ids, realm=realm)
            season_meta = _get_ranked_seasons_metadata()
            top_ship_names = _build_top_ranked_ship_names_by_season(
                ranked_ship_stats_rows, requested_season_ids)
            ranked_rows = _aggregate_ranked_seasons(
                rank_info, season_meta, top_ship_names_by_season=top_ship_names)

    player.ranked_json = ranked_rows
    player.ranked_updated_at = datetime.now()
    player.save(update_fields=['ranked_json', 'ranked_updated_at'])
    return ranked_rows


def _enrich_player_from_bulk(player_id, realm: str, ship_data_list, rank_account_data):
    """Enrich a single player using pre-fetched bulk API data.

    Returns an EnrichOutcome so the caller can tally real enrichments
    separately from empties (for health checks).
    """
    from warships.data import (
        update_snapshot_data,
        refresh_player_explorer_summary,
        fetch_player_clan_battle_seasons,
    )

    player = Player.objects.get(player_id=player_id, realm=realm)

    # Process ship/battle data (no API call -- already bulk-fetched)
    battles_rows, outcome = _process_player_ship_data(player, ship_data_list)

    if outcome == EnrichOutcome.SKIPPED:
        # Transient: don't touch snapshot/ranked/CB; next pass will retry.
        return outcome

    # Ranked data (1 API call for ranked ship stats if player has ranked seasons)
    rank_info = rank_account_data.get('rank_info') if rank_account_data else None
    ranked_rows = _process_player_ranked_data(player, rank_info, realm)

    # Snapshot + activity (no API calls -- refresh_player=False)
    update_snapshot_data(player_id, realm=realm, refresh_player=False)

    # Explorer summary (no API calls)
    refresh_player_explorer_summary(
        player, battles_rows=battles_rows or [], ranked_rows=ranked_rows)

    # Clan battle summary (2 API calls) -- only for enriched players
    if outcome == EnrichOutcome.ENRICHED:
        try:
            fetch_player_clan_battle_seasons(int(player_id), realm=realm)
        except Exception:
            log.warning("CB data fetch failed for player_id=%s realm=%s", player_id, realm)

    return outcome


def _run_data_quality_sample() -> tuple[int, int, list[str]]:
    """Sample recently-enriched players and validate structure.

    Returns (passed, failed, messages).
    """
    cutoff = timezone.now() - timedelta(minutes=30)
    qs = Player.objects.filter(
        battles_updated_at__gte=cutoff,
        battles_json__isnull=False,
    ).exclude(battles_json=[])
    ids = list(qs.values_list('player_id', flat=True)[:DQ_SAMPLE_SIZE * 5])
    if not ids:
        return 0, 0, ["no recent enrichments to sample"]
    sample_ids = random.sample(ids, min(DQ_SAMPLE_SIZE, len(ids)))
    sample = Player.objects.filter(player_id__in=sample_ids).only(
        'player_id', 'name', 'battles_json', 'tiers_json', 'type_json', 'ranked_json')

    passed = 0
    failed = 0
    messages: list[str] = []
    for p in sample:
        try:
            bj = p.battles_json or []
            if not isinstance(bj, list) or not bj:
                raise ValueError("battles_json empty/non-list")
            required_keys = {'ship_id', 'ship_name', 'ship_tier', 'pvp_battles', 'wins'}
            for row in bj:
                missing = required_keys - set(row.keys())
                if missing:
                    raise ValueError(f"battles row missing keys: {missing}")
            # Tier sum cross-check
            bj_sum = sum(int(r.get('pvp_battles', 0) or 0) for r in bj)
            tj = p.tiers_json or []
            tj_sum = sum(int(r.get('pvp_battles', 0) or 0) for r in tj)
            if tj_sum != bj_sum:
                raise ValueError(f"tier sum {tj_sum} != battles sum {bj_sum}")
            # type_json present
            if not isinstance(p.type_json, list) or not p.type_json:
                raise ValueError("type_json empty/non-list")
            passed += 1
        except Exception as e:
            failed += 1
            messages.append(f"player_id={p.player_id} name={p.name}: {e}")
    return passed, failed, messages


# ── Legacy single-player enrichment (kept for Celery task compatibility) ──

def _enrich_player_parallel(player_id, realm: str):
    """Enrich a single player with individual API calls (non-bulk path)."""
    from warships.api.ships import _fetch_ship_stats_for_player
    from warships.api.players import _fetch_ranked_account_info

    with ThreadPoolExecutor(max_workers=2) as ex:
        ship_future = ex.submit(
            _fetch_ship_stats_for_player, player_id, realm=realm)
        rank_future = ex.submit(
            _fetch_ranked_account_info, int(player_id), realm=realm)
        ship_data = ship_future.result()
        account_data = rank_future.result()

    _enrich_player_from_bulk(
        player_id, realm,
        ship_data_list=ship_data,
        rank_account_data=account_data,
    )


# ── Main enrichment loop ────────────────────────────────────

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

    per_realm = max(batch // len(target_realms), 1)
    realm_candidates = {}
    for realm in target_realms:
        realm_candidates[realm] = _candidates(
            realm, min_pvp_battles, min_wr, per_realm,
            partition=partition, num_partitions=num_partitions,
        )

    # Build queue grouped by realm (bulk fetches are per-realm)
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

    _prewarm_ship_cache()

    enriched = 0        # real ship-data writes
    empty = 0           # marked battles_json=[]
    skipped = 0         # transient failures, left eligible
    errors = 0          # exceptions
    by_realm = {r: 0 for r in target_realms}

    # Group queue by realm for bulk API calls
    realm_queues: dict[str, list] = {}
    for row in queue:
        realm_queues.setdefault(row[4], []).append(row)

    for realm, realm_queue in realm_queues.items():
        # Process in chunks of BULK_API_BATCH_SIZE for bulk API calls
        for chunk_start in range(0, len(realm_queue), BULK_API_BATCH_SIZE):
            chunk = realm_queue[chunk_start:chunk_start + BULK_API_BATCH_SIZE]
            chunk_player_ids = [row[0] for row in chunk]

            # Bulk fetch: 2 API calls for up to 100 players
            with ThreadPoolExecutor(max_workers=2) as ex:
                ship_future = ex.submit(
                    _bulk_fetch_ship_stats, chunk_player_ids, realm)
                rank_future = ex.submit(
                    _bulk_fetch_ranked_account_info, chunk_player_ids, realm)
                bulk_ship_data, ship_err = ship_future.result()
                bulk_rank_data, rank_err = rank_future.result()

            # POISON-BATCH FALLBACK: WG rejects the whole batch if any ID
            # is invalid. Fall back to per-player fetches ONLY for account
            # errors (not transient 5xx/timeout) to isolate the bad ID.
            if ship_err == "INVALID_ACCOUNT_ID":
                log.warning(
                    "Poison ship batch [%s] (%d players) — per-player fallback",
                    realm.upper(), len(chunk_player_ids),
                )
                bulk_ship_data = _per_player_ship_fallback(chunk_player_ids, realm)
            elif ship_err:
                log.error(
                    "Transient ship bulk error '%s' [%s] — skipping chunk",
                    ship_err, realm.upper(),
                )
                skipped += len(chunk_player_ids)
                continue

            if rank_err == "INVALID_ACCOUNT_ID":
                log.warning(
                    "Poison rank batch [%s] — per-player fallback", realm.upper())
                bulk_rank_data = _per_player_rank_fallback(chunk_player_ids, realm)
            elif rank_err:
                # Rank is secondary — log and proceed with empty rank data
                log.warning(
                    "Transient rank bulk error '%s' [%s] — proceeding without rank",
                    rank_err, realm.upper(),
                )
                bulk_rank_data = {}

            # Process each player using pre-fetched data
            for player_id, name, wr, battles, _ in chunk:
                if heartbeat_callback:
                    heartbeat_callback()

                pid_str = str(player_id)
                ship_data_list = bulk_ship_data.get(pid_str)
                # Fallback sentinel: transient per-player failure -> SKIP
                if ship_data_list == "SKIP":
                    skipped += 1
                    continue
                rank_account_data = bulk_rank_data.get(pid_str)

                try:
                    outcome = _enrich_player_from_bulk(
                        player_id, realm, ship_data_list, rank_account_data)
                    if outcome == EnrichOutcome.ENRICHED:
                        enriched += 1
                        by_realm[realm] = by_realm.get(realm, 0) + 1
                        log.info(
                            "Enriched %s [%s] WR=%.1f%% battles=%d (e=%d emp=%d/%d)",
                            name, realm.upper(), wr, battles,
                            enriched, empty, len(queue),
                        )
                    elif outcome == EnrichOutcome.EMPTY:
                        empty += 1
                    else:
                        skipped += 1
                except Exception:
                    log.exception(
                        "Failed to enrich player_id=%s name=%s realm=%s",
                        player_id, name, realm)
                    errors += 1

                if delay > 0:
                    time.sleep(delay)

    summary = {
        "status": "completed",
        "enriched": enriched,
        "empty": empty,
        "skipped": skipped,
        "errors": errors,
        "by_realm": by_realm,
        "candidates": total_candidates,
    }
    log.info("Enrichment pass complete: %s", summary)
    return summary


class Command(BaseCommand):
    help = "Enrich players missing battle/ranked/activity data."

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
            help="Restrict to one realm (default: all realms).",
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
        parser.add_argument(
            "--continuous", action="store_true",
            help="Chain batches until no eligible players remain.",
        )
        parser.add_argument(
            "--batch-pause", type=int, default=5,
            help="Seconds to pause between batches in continuous mode (default 5).",
        )

    def handle(self, *args, **options):
        realms = (options["realm"],) if options["realm"] else None
        continuous = options["continuous"]
        batch_pause = options["batch_pause"]
        batch_num = 0
        consecutive_degraded = 0
        consecutive_dq_failures = 0

        while True:
            batch_num += 1
            if continuous:
                self.stdout.write(f"\n--- Batch {batch_num} ---")

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

            # ── Ratio guard: catch any future silent-empty regressions ──
            pass_real = summary.get("enriched", 0)
            pass_empty = summary.get("empty", 0)
            total = pass_real + pass_empty
            if total >= RATIO_GUARD_MIN_SAMPLE:
                ratio = pass_real / total
                if ratio < RATIO_GUARD_MIN:
                    log.error(
                        "RATIO GUARD: real=%d empty=%d ratio=%.2f%% < %.1f%% floor",
                        pass_real, pass_empty, ratio * 100, RATIO_GUARD_MIN * 100,
                    )
                    consecutive_degraded += 1
                    if consecutive_degraded >= RATIO_GUARD_MAX_CONSECUTIVE:
                        raise RuntimeError(
                            f"Enrichment aborted: {consecutive_degraded} consecutive "
                            f"degraded passes (real/empty ratio below "
                            f"{RATIO_GUARD_MIN * 100:.0f}%)"
                        )
                else:
                    consecutive_degraded = 0

            # ── Data-quality sampling ──
            if DQ_ENABLED and batch_num % DQ_SAMPLE_EVERY_PASSES == 0:
                passed, failed, msgs = _run_data_quality_sample()
                log.info(
                    "DQ sample (batch %d): passed=%d failed=%d", batch_num, passed, failed)
                if failed > 0:
                    for m in msgs:
                        log.error("DQ FAIL: %s", m)
                    consecutive_dq_failures += 1
                    if consecutive_dq_failures >= 3:
                        raise RuntimeError(
                            f"Enrichment aborted: {consecutive_dq_failures} consecutive "
                            f"failed data-quality samples"
                        )
                else:
                    consecutive_dq_failures = 0

            if not continuous:
                break

            if total == 0 and summary.get("skipped", 0) == 0:
                self.stdout.write(self.style.SUCCESS(
                    f"\nNo players processed in batch {batch_num} -- all caught up."
                ))
                break

            self.stdout.write(f"Pausing {batch_pause}s before next batch...")
            time.sleep(batch_pause)

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
import logging
from django.core.cache import cache
from warships.models import Player, Snapshot, Clan
from warships.api.ships import _fetch_ship_stats_for_player, _fetch_ship_info, _fetch_ranked_ship_stats_for_player
from warships.api.players import _fetch_snapshot_data, _fetch_player_personal_data, _fetch_ranked_account_info
from warships.api.clans import _fetch_clan_data, _fetch_clan_member_ids, _fetch_clan_membership_for_player, \
    _fetch_clan_battle_seasons_info, _fetch_clan_battle_season_stats
from warships.tasks import update_tiers_data_task, update_type_data_task

logging.basicConfig(level=logging.INFO)

def _ranked_rows_have_top_ship(rows: Any) -> bool:
    if not isinstance(rows, list):
        return False

    return all(isinstance(row, dict) and 'top_ship_name' in row for row in rows)


def _extract_ranked_ship_battles(season_stats: Any) -> int:
    if not isinstance(season_stats, dict):
        return 0

    if 'battles' in season_stats:
        return int(season_stats.get('battles', 0) or 0)

    total_battles = 0
    for mode_key in ('rank_solo', 'rank_div2', 'rank_div3'):
        mode_stats = season_stats.get(mode_key)
        if not isinstance(mode_stats, dict):
            continue
        total_battles += int(mode_stats.get('battles', 0) or 0)

    return total_battles


def _build_top_ranked_ship_names_by_season(ranked_ship_stats_rows: Any, requested_season_ids: list[int]) -> dict[int, Optional[str]]:
    if not isinstance(ranked_ship_stats_rows, list):
        return {}

    top_ship_ids_by_season: dict[int, int] = {}
    top_ship_battles_by_season: dict[int, int] = {}

    for row in ranked_ship_stats_rows:
        if not isinstance(row, dict):
            continue

        try:
            ship_id = int(row.get('ship_id'))
        except (TypeError, ValueError):
            continue

        seasons_payload = row.get('seasons')
        if isinstance(seasons_payload, dict):
            season_items = seasons_payload.items()
        elif len(requested_season_ids) == 1:
            season_items = [(requested_season_ids[0], row)]
        else:
            season_items = []

        for season_id_raw, season_stats in season_items:
            try:
                season_id = int(season_id_raw)
            except (TypeError, ValueError):
                continue

            battles = _extract_ranked_ship_battles(season_stats)
            if battles <= 0:
                continue

            current_best_battles = top_ship_battles_by_season.get(season_id, -1)
            current_best_ship_id = top_ship_ids_by_season.get(season_id)
            if battles > current_best_battles or (battles == current_best_battles and (current_best_ship_id is None or ship_id < current_best_ship_id)):
                top_ship_battles_by_season[season_id] = battles
                top_ship_ids_by_season[season_id] = ship_id

    ship_names_by_id: dict[int, Optional[str]] = {}
    for ship_id in set(top_ship_ids_by_season.values()):
        ship = _fetch_ship_info(str(ship_id))
        ship_names_by_id[ship_id] = ship.name if ship else None

    return {
        season_id: ship_names_by_id.get(ship_id)
        for season_id, ship_id in top_ship_ids_by_season.items()
    }

def _extract_randoms_rows(battles_json: Any, limit: Optional[int] = 20) -> list[dict]:
    if not isinstance(battles_json, list):
        return []

    rows = []
    for row in battles_json:
        if not isinstance(row, dict):
            continue

        ship_name = row.get('ship_name')
        ship_type = row.get('ship_type')
        ship_tier = row.get('ship_tier')
        if ship_name is None or ship_type is None or ship_tier is None:
            continue

        rows.append({
            'pvp_battles': int(row.get('pvp_battles', 0) or 0),
            'ship_name': ship_name,
            'ship_type': ship_type,
            'ship_tier': ship_tier,
            'win_ratio': float(row.get('win_ratio', 0) or 0),
            'wins': int(row.get('wins', 0) or 0),
        })

    rows.sort(key=lambda row: row['pvp_battles'], reverse=True)
    return rows if limit is None else rows[:limit]


def _aggregate_battles_by_key(battles_json: Any, group_key: str) -> list[dict]:
    if not isinstance(battles_json, list):
        return []

    aggregates: dict[Any, dict[str, int]] = {}
    for row in battles_json:
        if not isinstance(row, dict):
            continue

        key = row.get(group_key)
        if key is None:
            continue

        aggregate = aggregates.setdefault(key, {'pvp_battles': 0, 'wins': 0})
        aggregate['pvp_battles'] += int(row.get('pvp_battles', 0) or 0)
        aggregate['wins'] += int(row.get('wins', 0) or 0)

    result = []
    for key, aggregate in aggregates.items():
        battles = aggregate['pvp_battles']
        wins = aggregate['wins']
        result.append({
            group_key: key,
            'pvp_battles': battles,
            'wins': wins,
            'win_ratio': round(wins / battles, 2) if battles > 0 else 0,
        })

    result.sort(key=lambda row: row['pvp_battles'], reverse=True)
    return result


def update_battle_data(player_id: str) -> None:
    """
    Updates the battle data for a given player.

    This function fetches the latest battle data for a player from an external API if the cached data is older than 15 minutes.
    The fetched data is then processed and saved back to the player's record in the database.

    Args:
        player_id (str): The ID of the player whose battle data needs to be updated.

    Returns:
        None
    """
    player = Player.objects.get(player_id=player_id)

    # Check if the cached data is less than 15 minutes old
    if player.battles_json and player.battles_updated_at and datetime.now() - player.battles_updated_at < timedelta(minutes=15):
        logging.debug(
            f'Cache exists and is fresh: returning cached data')
        return player.battles_json

    logging.info(
        f'Battles data empty or outdated: fetching new data for {player.name}')

    # Fetch ship stats for the player
    ship_data = _fetch_ship_stats_for_player(player_id)
    prepared_data = []

    for ship in ship_data:
        ship_model = _fetch_ship_info(ship['ship_id'])

        if not ship_model or not ship_model.name:
            continue

        pvp_battles = ship['pvp']['battles']
        wins = ship['pvp']['wins']
        losses = ship['pvp']['losses']
        frags = ship['pvp']['frags']
        battles = ship['battles']
        distance = ship['distance']

        ship_info = {
            'ship_name': ship_model.name,
            'ship_tier': ship_model.tier,
            'all_battles': battles,
            'distance': distance,
            'wins': wins,
            'losses': losses,
            'ship_type': ship_model.ship_type,
            'pve_battles': battles - (wins + losses),
            'pvp_battles': pvp_battles,
            'win_ratio': round(wins / pvp_battles, 2) if pvp_battles > 0 else 0,
            'kdr': round(frags / pvp_battles, 2) if pvp_battles > 0 else 0
        }

        prepared_data.append(ship_info)

    # Sort the data by "pvp_battles" in descending order
    sorted_data = sorted(prepared_data, key=lambda x: x.get(
        'pvp_battles', 0), reverse=True)

    player.battles_updated_at = datetime.now()
    player.battles_json = sorted_data
    player.save()
    logging.info(f"Updated battles_json data: {player.name}")


def fetch_tier_data(player_id: str) -> list:
    """
    Fetches and processes tier data for a given player. Tier data is a subset of battle data.

    This function updates the battle data for a player and then processes it to calculate the number of battles,
    wins, and win ratio for each ship tier. The processed data is saved back to the player's record in the database.

    Args:
        player_id (str): The ID of the player whose tier data needs to be fetched.

    Returns:
        str: A JSON response containing the processed tier data.
    """
    try:
        player = Player.objects.get(player_id=player_id)
        if not player.battles_json:
            update_battle_data(player_id)
    except Player.DoesNotExist:
        return []

    player = Player.objects.get(player_id=player_id)

    if player.tiers_json:
        if not player.tiers_updated_at or datetime.now() - player.tiers_updated_at > timedelta(days=1):
            update_tiers_data_task.delay(player_id)
        return player.tiers_json
    else:
        update_tiers_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return player.tiers_json


def update_tiers_data(player_id: str) -> list:
    player = Player.objects.get(player_id=player_id)
    tier_aggregates = {tier: {'pvp_battles': 0, 'wins': 0} for tier in range(1, 12)}
    for row in player.battles_json or []:
        if not isinstance(row, dict):
            continue

        tier = row.get('ship_tier')
        if not isinstance(tier, int) or tier not in tier_aggregates:
            continue

        tier_aggregates[tier]['pvp_battles'] += int(row.get('pvp_battles', 0) or 0)
        tier_aggregates[tier]['wins'] += int(row.get('wins', 0) or 0)

    data = []
    for tier in range(11, 0, -1):
        battles = tier_aggregates[tier]['pvp_battles']
        wins = tier_aggregates[tier]['wins']
        data.append({
            'ship_tier': tier,
            'pvp_battles': battles,
            'wins': wins,
            'win_ratio': round(wins / battles, 2) if battles > 0 else 0,
        })

    player.tiers_json = data
    player.tiers_updated_at = datetime.now()
    player.save()


def update_snapshot_data(player_id: int) -> None:
    """
    Records today's cumulative PvP stats as a Snapshot and computes
    daily interval_battles / interval_wins from successive snapshots.

    The WoWS account/statsbydate endpoint no longer returns pvp data,
    so we use the Player model's pvp_battles / pvp_wins (kept current
    by update_player_data via account/info) as today's cumulative values.
    """
    player = Player.objects.get(player_id=player_id)
    player.last_lookup = datetime.now()
    player.save()

    # Ensure the player model has fresh stats
    from warships.data import update_player_data
    update_player_data(player, force_refresh=True)
    player.refresh_from_db()

    today = datetime.now().date()
    start_date = today - timedelta(days=28)

    # Purge stale zero-value snapshots left by the broken statsbydate API
    Snapshot.objects.filter(
        player=player, battles=0, wins=0
    ).exclude(date=today).delete()

    # Upsert today's snapshot with current cumulative totals
    snapshot, _ = Snapshot.objects.get_or_create(player=player, date=today)
    snapshot.battles = player.pvp_battles or 0
    snapshot.wins = player.pvp_wins or 0
    snapshot.last_fetch = datetime.now()
    snapshot.save()

    # Recompute intervals for the whole 28-day window
    snapshots = list(Snapshot.objects.filter(
        player=player, date__gte=start_date, date__lte=today).order_by('date'))

    previous_battles = None
    previous_wins = None
    for snap in snapshots:
        if previous_battles is None or previous_wins is None:
            snap.interval_battles = 0
            snap.interval_wins = 0
        else:
            snap.interval_battles = max(
                0, int(snap.battles or 0) - int(previous_battles or 0))
            snap.interval_wins = max(
                0, int(snap.wins or 0) - int(previous_wins or 0))

        snap.save(update_fields=['interval_battles', 'interval_wins'])
        previous_battles = snap.battles
        previous_wins = snap.wins

    logging.info(f'Updated snapshot data for player {player.name}')


def fetch_activity_data(player_id: str) -> list:
    player = Player.objects.get(player_id=player_id)

    def _is_empty_activity(activity_rows: Any) -> bool:
        if not isinstance(activity_rows, list) or not activity_rows:
            return True
        return all((row.get('battles', 0) or 0) == 0 for row in activity_rows)

    def _looks_like_cumulative_spike(activity_rows: Any) -> bool:
        if not isinstance(activity_rows, list) or not activity_rows:
            return False
        non_zero_days = [row for row in activity_rows if (
            row.get('battles', 0) or 0) > 0]
        total_battles = sum((row.get('battles', 0) or 0)
                            for row in activity_rows)
        return len(non_zero_days) == 1 and total_battles > 1000

    if player.activity_json:
        logging.info(f'Activity data exists for player {player.name}')
        is_stale = not player.activity_updated_at or datetime.now(
        ) - player.activity_updated_at > timedelta(minutes=15)
        is_empty = _is_empty_activity(player.activity_json)
        is_cumulative_spike = _looks_like_cumulative_spike(
            player.activity_json)

        if is_stale or is_empty or is_cumulative_spike:
            logging.info(
                f'Activity data refresh required (stale={is_stale}, empty={is_empty}, cumulative_spike={is_cumulative_spike}) for {player.name} : {player.player_id}')
            update_snapshot_data(player.player_id)
            update_activity_data(player.player_id)
            player.refresh_from_db()
        else:
            logging.info(
                f'Activity fetch datetime is fresh: returning cached data for player {player.name}')
        return player.activity_json
    else:
        update_snapshot_data(player_id)
        update_activity_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return player.activity_json


def update_activity_data(player_id: int) -> None:
    player = Player.objects.get(player_id=player_id)
    month = []
    snapshots = list(Snapshot.objects.filter(player=player).order_by('date'))

    latest_snapshot_by_date = {snap.date: snap for snap in snapshots}

    for i in range(29):
        date = (datetime.now() - timedelta(28) + timedelta(days=i)).date()

        snap = latest_snapshot_by_date.get(date)

        month.append({
            "date": date.strftime("%Y-%m-%d"),
            "battles": (snap.interval_battles if snap else 0) or 0,
            "wins": (snap.interval_wins if snap else 0) or 0
        })

    player.activity_json = month
    player.activity_updated_at = datetime.now()
    player.save()

    logging.info(f'Updated activity data for player {player.name}')


# ──────────────────────────────────────────────────────────
#  Ranked Battles data
# ──────────────────────────────────────────────────────────

LEAGUE_NAMES = {1: 'Gold', 2: 'Silver', 3: 'Bronze'}
RANKED_SEASONS_CACHE_KEY = 'ranked:seasons:metadata'
RANKED_SEASONS_CACHE_TTL = 86400  # 24 hours in seconds
CLAN_BATTLE_SEASONS_CACHE_KEY = 'clan_battles:seasons:metadata'
CLAN_BATTLE_SEASONS_CACHE_TTL = 86400
CLAN_BATTLE_PLAYER_STATS_CACHE_TTL = 21600
CLAN_BATTLE_SUMMARY_CACHE_TTL = 3600


def _get_clan_battle_summary_cache_key(clan_id: str) -> str:
    return f'clan_battles:summary:v2:{clan_id}'


def has_clan_battle_summary_cache(clan_id: str) -> bool:
    return cache.get(_get_clan_battle_summary_cache_key(clan_id)) is not None


def _invalidate_clan_battle_summary_cache(clan_id: str) -> None:
    cache.delete(_get_clan_battle_summary_cache_key(clan_id))


def _clan_battle_season_sort_key(summary: dict) -> tuple:
    start_date = summary.get('start_date')
    end_date = summary.get('end_date')

    parsed_start = datetime.min
    parsed_end = datetime.min

    if start_date:
        try:
            parsed_start = datetime.strptime(start_date, '%Y-%m-%d')
        except ValueError:
            pass

    if end_date:
        try:
            parsed_end = datetime.strptime(end_date, '%Y-%m-%d')
        except ValueError:
            pass

    return parsed_start, parsed_end, summary.get('season_id', 0)


def _get_ranked_seasons_metadata() -> dict:
    """Return season_id → {name, label, start_date, end_date}. Cached for 24h in Redis."""
    from warships.api.players import _fetch_ranked_seasons_info

    cached = cache.get(RANKED_SEASONS_CACHE_KEY)
    if cached is not None:
        return cached

    raw = _fetch_ranked_seasons_info()
    if not raw:
        return {}  # nothing to cache

    result = {}
    for sid, info in raw.items():
        sid_int = int(sid)
        season_name = info.get('season_name', f'Season {sid_int - 1000}')
        label = f'S{sid_int - 1000}'
        start_ts = info.get('start_at')
        close_ts = info.get('close_at')
        result[sid_int] = {
            'name': season_name,
            'label': label,
            'start_date': datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d') if start_ts else None,
            'end_date': datetime.fromtimestamp(close_ts).strftime('%Y-%m-%d') if close_ts else None,
        }

    cache.set(RANKED_SEASONS_CACHE_KEY, result, RANKED_SEASONS_CACHE_TTL)
    return result


def _get_clan_battle_seasons_metadata() -> dict:
    """Return season_id -> clan battle season metadata. Cached for 24h."""
    cached = cache.get(CLAN_BATTLE_SEASONS_CACHE_KEY)
    if cached is not None:
        return cached

    raw = _fetch_clan_battle_seasons_info()
    if not raw:
        return {}

    result = {}
    for sid, info in raw.items():
        sid_int = int(sid)
        start_ts = info.get('start_time')
        finish_ts = info.get('finish_time')
        result[sid_int] = {
            'name': info.get('name', f'Season {sid_int}'),
            'label': f'S{sid_int}',
            'start_date': datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d') if start_ts else None,
            'end_date': datetime.fromtimestamp(finish_ts).strftime('%Y-%m-%d') if finish_ts else None,
            'ship_tier_min': info.get('ship_tier_min'),
            'ship_tier_max': info.get('ship_tier_max'),
        }

    cache.set(CLAN_BATTLE_SEASONS_CACHE_KEY,
              result, CLAN_BATTLE_SEASONS_CACHE_TTL)
    return result


def _get_player_clan_battle_season_stats(account_id: int) -> list:
    """Return cached clan battle season stats for a player."""
    cache_key = f'clan_battles:player:{account_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    raw = _fetch_clan_battle_season_stats(account_id)
    seasons = raw.get('seasons', []) if raw else []
    cache.set(cache_key, seasons, CLAN_BATTLE_PLAYER_STATS_CACHE_TTL)
    return seasons


def fetch_clan_battle_seasons(clan_id: str) -> list:
    """Return cached clan battle summary, enqueueing background refresh on misses."""
    if not clan_id:
        return []

    cache_key = _get_clan_battle_summary_cache_key(clan_id)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from warships.tasks import update_clan_battle_summary_task

    update_clan_battle_summary_task.delay(clan_id=clan_id)
    return []


def refresh_clan_battle_seasons_cache(clan_id: str) -> list:
    """Aggregate clan battle season stats across the clan's current roster and cache them."""
    if not clan_id:
        return []

    cache_key = _get_clan_battle_summary_cache_key(clan_id)

    try:
        clan = Clan.objects.get(clan_id=clan_id)
    except Clan.DoesNotExist:
        return []

    members = list(
        clan.player_set.exclude(name='').exclude(
            player_id__isnull=True).values('player_id', 'name')
    )

    if not members and clan.members_count:
        update_clan_members(clan_id=clan_id)
        members = list(
            clan.player_set.exclude(name='').exclude(
                player_id__isnull=True).values('player_id', 'name')
        )

    if not members:
        cache.set(cache_key, [], CLAN_BATTLE_SUMMARY_CACHE_TTL)
        return []

    season_meta = _get_clan_battle_seasons_metadata()
    season_summaries = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_get_player_clan_battle_season_stats, member['player_id']): member
            for member in members
        }

        for future in as_completed(futures):
            member = futures[future]
            try:
                seasons = future.result()
            except Exception as error:
                logging.error(
                    'Failed clan battle stats fetch for %s (%s): %s',
                    member['name'],
                    member['player_id'],
                    error,
                )
                continue

            for season in seasons:
                battles = int(season.get('battles', 0) or 0)
                if battles <= 0:
                    continue

                sid = int(season.get('season_id', 0) or 0)
                if sid <= 0:
                    continue

                summary = season_summaries.setdefault(sid, {
                    'season_id': sid,
                    'season_name': season_meta.get(sid, {}).get('name', f'Season {sid}'),
                    'season_label': season_meta.get(sid, {}).get('label', f'S{sid}'),
                    'start_date': season_meta.get(sid, {}).get('start_date'),
                    'end_date': season_meta.get(sid, {}).get('end_date'),
                    'ship_tier_min': season_meta.get(sid, {}).get('ship_tier_min'),
                    'ship_tier_max': season_meta.get(sid, {}).get('ship_tier_max'),
                    'participants': 0,
                    'roster_battles': 0,
                    'roster_wins': 0,
                    'roster_losses': 0,
                })

                summary['participants'] += 1
                summary['roster_battles'] += battles
                summary['roster_wins'] += int(season.get('wins', 0) or 0)
                summary['roster_losses'] += int(season.get('losses', 0) or 0)

    result = []
    for summary in sorted(season_summaries.values(), key=_clan_battle_season_sort_key, reverse=True):
        battles = summary['roster_battles']
        summary['roster_win_rate'] = round(
            summary['roster_wins'] / battles * 100, 1) if battles > 0 else 0.0
        result.append(summary)

    cache.set(cache_key, result, CLAN_BATTLE_SUMMARY_CACHE_TTL)
    return result


def _aggregate_ranked_seasons(rank_info: dict, season_meta: dict, top_ship_names_by_season: Optional[dict[int, Optional[str]]] = None) -> list:
    """
    Transform the WG API rank_info structure into a flat list of per-season summaries.

    rank_info structure: {season_id: {sprint_key: {league_key: {battles, victories, rank, ...}}}}
    """
    seasons = []

    for sid_str, sprints in rank_info.items():
        if sprints is None:
            continue
        sid = int(sid_str)
        meta = season_meta.get(sid, {})

        total_battles = 0
        total_wins = 0
        highest_league = 99  # lower = better (1=Gold)
        best_sprint = None
        sprint_details = []

        for sprint_key, leagues in sprints.items():
            if leagues is None:
                continue
            sprint_battles = 0
            sprint_wins = 0
            sprint_best_league = 99
            sprint_best_rank = 99

            for league_key, sprint_data in leagues.items():
                if sprint_data is None or not isinstance(sprint_data, dict):
                    continue

                # Skip mode-style keys (rank_solo, rank_div2, etc.) — use only numeric league keys
                try:
                    lk = int(league_key)
                except (ValueError, TypeError):
                    continue

                b = sprint_data.get('battles', 0) or 0
                w = sprint_data.get('victories', 0) or 0
                rank = sprint_data.get('rank', 99)
                best_rank_in_sprint = sprint_data.get(
                    'best_rank_in_sprint', sprint_data.get('rank_best', 99))

                sprint_battles += b
                sprint_wins += w

                if lk < sprint_best_league or (lk == sprint_best_league and best_rank_in_sprint < sprint_best_rank):
                    sprint_best_league = lk
                    sprint_best_rank = best_rank_in_sprint

                if lk < highest_league:
                    highest_league = lk

            total_battles += sprint_battles
            total_wins += sprint_wins

            sprint_detail = {
                'sprint_number': int(sprint_key) if sprint_key.isdigit() else 0,
                'league': sprint_best_league if sprint_best_league < 99 else 3,
                'league_name': LEAGUE_NAMES.get(sprint_best_league, 'Bronze'),
                'rank': sprint_best_rank if sprint_best_rank < 99 else 10,
                'best_rank': sprint_best_rank if sprint_best_rank < 99 else 10,
                'battles': sprint_battles,
                'wins': sprint_wins,
            }
            sprint_details.append(sprint_detail)

            # Determine best sprint (highest league, then lowest rank, then most wins)
            if best_sprint is None or \
                    sprint_best_league < best_sprint['league'] or \
                    (sprint_best_league == best_sprint['league'] and sprint_best_rank < best_sprint['best_rank']):
                best_sprint = sprint_detail

        if total_battles == 0:
            continue

        if highest_league > 3:
            highest_league = 3

        win_rate = round(total_wins / total_battles,
                         4) if total_battles > 0 else 0.0

        seasons.append({
            'season_id': sid,
            'season_name': meta.get('name', f'Season {sid - 1000}'),
            'season_label': meta.get('label', f'S{sid - 1000}'),
            'start_date': meta.get('start_date'),
            'end_date': meta.get('end_date'),
            'highest_league': highest_league,
            'highest_league_name': LEAGUE_NAMES.get(highest_league, 'Bronze'),
            'total_battles': total_battles,
            'total_wins': total_wins,
            'win_rate': win_rate,
            'top_ship_name': (top_ship_names_by_season or {}).get(sid),
            'best_sprint': best_sprint,
            'sprints': sorted(sprint_details, key=lambda x: x['sprint_number']),
        })

    # Sort by season_id ascending, take last 10
    seasons.sort(key=lambda x: x['season_id'])
    return seasons[-10:]


def fetch_ranked_data(player_id: str) -> list:
    """Fetch ranked battles data for a player. Caches as ranked_json."""
    try:
        player = Player.objects.get(player_id=player_id)
    except Player.DoesNotExist:
        return []

    # Return cached if fresh
    if player.ranked_json and player.ranked_updated_at and \
            datetime.now() - player.ranked_updated_at < timedelta(hours=1):
        if _ranked_rows_have_top_ship(player.ranked_json):
            logging.info(f'Ranked data cache fresh for {player.name}')
            return player.ranked_json

    logging.info(f'Fetching ranked data for {player.name}')
    update_ranked_data(player_id)
    player.refresh_from_db()
    return player.ranked_json or []


def update_ranked_data(player_id) -> None:
    """Fetch ranked data from WG API, aggregate, and cache on Player model."""
    player = Player.objects.get(player_id=player_id)

    # Get season metadata (cached globally)
    season_meta = _get_ranked_seasons_metadata()

    # Get player's rank_info
    account_data = _fetch_ranked_account_info(int(player_id))
    rank_info = account_data.get('rank_info') if account_data else None

    if not rank_info:
        logging.info(f'No ranked data for {player.name}')
        player.ranked_json = []
        player.ranked_updated_at = datetime.now()
        player.save()
        return

    requested_season_ids = sorted(
        int(season_id) for season_id in rank_info.keys() if str(season_id).isdigit()
    )
    top_ship_names_by_season = _build_top_ranked_ship_names_by_season(
        _fetch_ranked_ship_stats_for_player(int(player_id), season_ids=requested_season_ids),
        requested_season_ids,
    )

    # Aggregate into per-season summaries
    result = _aggregate_ranked_seasons(
        rank_info, season_meta, top_ship_names_by_season=top_ship_names_by_season)

    player.ranked_json = result
    player.ranked_updated_at = datetime.now()
    player.save()
    logging.info(
        f'Updated ranked data for {player.name}: {len(result)} seasons')


def fetch_type_data(player_id: str) -> list:
    try:
        player = Player.objects.get(player_id=player_id)
        if not player.battles_json:
            update_battle_data(player_id)
    except Player.DoesNotExist:
        return []

    if player.type_json:
        if not player.type_updated_at or datetime.now() - player.type_updated_at > timedelta(days=1):
            update_type_data_task.delay(player_id)
        return player.type_json
    else:
        update_type_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return player.type_json


def update_type_data(player_id: str) -> list:
    player = Player.objects.get(player_id=player_id)
    player.type_json = _aggregate_battles_by_key(player.battles_json, 'ship_type')
    player.type_updated_at = datetime.now()
    player.save()

    logging.info(f'Updated type data for player {player.name}')


def fetch_randoms_data(player_id: str) -> list:
    try:
        player = Player.objects.get(player_id=player_id)
        if not player.battles_json:
            update_battle_data(player_id)
    except Player.DoesNotExist:
        return []

    if player.randoms_json:
        has_required_fields = isinstance(player.randoms_json, list) and all(
            isinstance(row, dict) and 'ship_type' in row and 'ship_tier' in row
            for row in player.randoms_json
        )

        if not has_required_fields:
            update_randoms_data(player_id)
            player = Player.objects.get(player_id=player_id)
            return player.randoms_json

        randoms_stale = not player.randoms_updated_at or datetime.now(
        ) - player.randoms_updated_at > timedelta(days=1)
        battles_stale = not player.battles_updated_at or datetime.now(
        ) - player.battles_updated_at > timedelta(minutes=15)
        if randoms_stale or battles_stale:
            update_battle_data(player_id)
            update_randoms_data(player_id)
            player = Player.objects.get(player_id=player_id)
        return player.randoms_json
    else:
        update_randoms_data(player_id)
        player = Player.objects.get(player_id=player_id)
        return player.randoms_json


def fetch_clan_plot_data(clan_id: str, filter_type: str = 'active') -> list:
    try:
        clan = Clan.objects.get(clan_id=clan_id)
    except Clan.DoesNotExist:
        return []

    if not clan.members_count:
        update_clan_data(clan_id)
        clan.refresh_from_db()

    members = clan.player_set.exclude(name='').all()

    if not members.exists() or (clan.members_count and members.count() < clan.members_count):
        update_clan_members(clan_id)
        members = clan.player_set.exclude(name='').all()

    data = []
    for member in members:
        battles = member.pvp_battles or 0
        if filter_type != 'all' and battles < 100:
            continue

        data.append({
            'player_name': member.name,
            'pvp_battles': battles,
            'pvp_ratio': member.pvp_ratio or 0
        })

    return sorted(data, key=lambda row: row.get('pvp_battles', 0), reverse=True)


def update_randoms_data(player_id: str) -> None:
    player = Player.objects.get(player_id=player_id)
    player.randoms_json = _extract_randoms_rows(player.battles_json, limit=20)
    player.randoms_updated_at = datetime.now()
    player.save()

    logging.info(f'Updated randoms data for player {player.name}')


def update_clan_data(clan_id: str) -> None:

    # return if no clan_id is provided
    if not clan_id:
        return

    try:
        clan = Clan.objects.get(clan_id=clan_id)
    except Clan.DoesNotExist:
        logging.info(
            f"Clan {clan_id} not found\n")
        return

    if clan.last_fetch and datetime.now() - clan.last_fetch < timedelta(minutes=1440):
        logging.debug(
            f'{clan.name}: Clan data is fresh')
        return

    data = _fetch_clan_data(clan_id)
    if not data:
        logging.warning(
            "Skipping clan update because upstream returned no data for clan_id=%s", clan_id)
        return

    clan.members_count = data.get('members_count', 0)
    clan.tag = data.get('tag', '')
    clan.name = data.get('name', '')
    clan.description = data.get('description', '')
    clan.leader_id = data.get('leader_id', None)
    clan.leader_name = data.get('leader_name', '')
    clan.last_fetch = datetime.now()
    clan.save()
    cache.delete('landing:clans')
    _invalidate_clan_battle_summary_cache(clan_id)
    logging.info(
        f"Updated clan data: {clan.name} [{clan.tag}]: {clan.members_count} members")

    for member_id in _fetch_clan_member_ids(clan_id):
        player, created = Player.objects.get_or_create(player_id=member_id)
        if created:
            player.player_id = member_id
            player.save()
            logging.info(
                f"Created new player: {player.player_id}\nPopulating data...")
            update_player_data(player)
        else:
            if player.clan != clan:
                player.clan = clan
                player.save()


def update_clan_members(clan_id: str) -> None:
    clan = Clan.objects.get(clan_id=clan_id)
    member_ids = _fetch_clan_member_ids(clan_id)

    if not member_ids and clan.members_count:
        logging.warning(
            "Skipping clan member refresh because upstream returned no member ids for clan_id=%s",
            clan_id,
        )
        return

    for member_id in member_ids:
        player, created = Player.objects.get_or_create(player_id=member_id)
        if created:
            player.player_id = member_id
            player.save()
            logging.info(
                f"Created new player: {player.player_id}")
            update_player_data(player)
            update_battle_data(player.player_id)

        else:
            if player.clan != clan:
                player.clan = clan
                player.save()

        update_player_data(player)

    cache.delete('landing:clans')
    _invalidate_clan_battle_summary_cache(clan_id)


def update_player_data(player: Player, force_refresh: bool = False) -> None:
    if not force_refresh and player.last_fetch and datetime.now() - player.last_fetch < timedelta(minutes=1400):
        logging.debug(
            f'Player data is fresh')
        return

    player_data = _fetch_player_personal_data(player.player_id)
    if not player_data:
        logging.warning(
            "Skipping player update because upstream returned no data for player_id=%s",
            player.player_id,
        )
        return

    # Map basic fields
    player.name = player_data.get("nickname", "")
    player.player_id = player_data.get("account_id", player.player_id)

    clan_membership = _fetch_clan_membership_for_player(player.player_id)
    clan_id = clan_membership.get("clan_id") or player_data.get("clan_id")
    if clan_id:
        clan, _ = Clan.objects.get_or_create(clan_id=clan_id)
        player.clan = clan
    else:
        player.clan = None

    created_at = player_data.get("created_at")
    player.creation_date = datetime.fromtimestamp(
        created_at, tz=timezone.utc) if created_at else None

    last_battle_time = player_data.get("last_battle_time")
    player.last_battle_date = datetime.fromtimestamp(
        last_battle_time, tz=timezone.utc).date() if last_battle_time else None

    # Calculate days since last battle
    if player.last_battle_date:
        player.days_since_last_battle = (datetime.now(
            timezone.utc).date() - player.last_battle_date).days

    # Check if the player's profile is hidden
    player.is_hidden = bool(player_data.get('hidden_profile'))

    # If the player is not hidden, map additional statistics
    if not player.is_hidden:
        stats_updated_at = player_data.get("stats_updated_at")
        player.battles_updated_at = datetime.fromtimestamp(
            stats_updated_at, tz=timezone.utc) if stats_updated_at else None
        stats = player_data.get("statistics", {})
        player.total_battles = stats.get("battles", 0)
        pvp_stats = stats.get("pvp", {})
        player.pvp_battles = pvp_stats.get("battles", 0)
        player.pvp_wins = pvp_stats.get("wins", 0)
        player.pvp_losses = pvp_stats.get("losses", 0)

        # Calculate PvP ratios
        player.pvp_ratio = round(
            (player.pvp_wins / player.pvp_battles * 100), 2) if player.pvp_battles else 0
        player.pvp_survival_rate = round((pvp_stats.get(
            "survived_battles", 0) / player.pvp_battles) * 100, 2) if player.pvp_battles else 0
        player.wins_survival_rate = round((pvp_stats.get(
            "survived_wins", 0) / player.pvp_wins) * 100, 2) if player.pvp_wins else 0
    else:
        player.total_battles = 0
        player.pvp_battles = 0
        player.pvp_wins = 0
        player.pvp_losses = 0
        player.pvp_ratio = None
        player.pvp_survival_rate = None
        player.wins_survival_rate = None
        player.battles_json = None
        player.battles_updated_at = None
        player.tiers_json = None
        player.tiers_updated_at = None
        player.activity_json = None
        player.activity_updated_at = None
        player.type_json = None
        player.type_updated_at = None
        player.randoms_json = None
        player.randoms_updated_at = None
        player.ranked_json = None
        player.ranked_updated_at = None

    player.last_fetch = datetime.now()
    player.save()
    cache.delete('landing:players')
    logging.info(f"Updated player personal data: {player.name}")


def preload_battles_json() -> None:
    logging.info("Preloading battles_json data for all players")
    players = Player.objects.all()
    for player in players:
        if not player.battles_json:
            update_battle_data(player.player_id)
        logging.info(f"Preloaded battles json for player: {player.name}")
    logging.info("Preloading complete")


def preload_activity_data() -> None:
    # because this function isn't calling update_snapshot_data, it's just creating
    # an empty data structure for the player's activity_json field, which helps the
    # front end to render the activity faster, while it loads the actual data in the background
    logging.info("Preloading activity data for all players")
    players = Player.objects.all()
    for player in players:
        if not player.activity_json:
            update_activity_data(player.player_id)
        logging.info(f"Preloaded activity data for player: {player.name}")
    logging.info("Preloading complete")

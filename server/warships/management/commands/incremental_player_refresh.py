import json
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q
from django.utils import timezone

from warships.clan_crawl import fetch_players_bulk, save_player
from warships.data import (
    clan_battle_summary_is_stale,
    fetch_player_clan_battle_seasons,
    player_achievements_need_refresh,
    player_efficiency_needs_refresh,
    refresh_player_explorer_summary,
    refresh_player_detail_payloads,
    update_achievements_data,
    update_player_efficiency_data,
)
from warships.models import DEFAULT_REALM, Player, PlayerExplorerSummary
from warships.tasks import _clan_crawl_lock_key


DEFAULT_STATE_FILE = Path(settings.BASE_DIR) / 'logs' / \
    'incremental_player_refresh_state.json'


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _default_state() -> dict:
    return {
        'version': 1,
        'pending_player_ids': [],
        'next_index': 0,
        'processed_total': 0,
        'succeeded_total': 0,
        'error_total': 0,
        'failed_player_ids': [],
        'last_error': None,
        'cycle_started_at': None,
        'cycle_completed_at': None,
        'updated_at': None,
        'tier_counts': {
            'hot': 0,
            'active': 0,
            'warm': 0,
        },
    }


def _load_state(state_path: Path, reset_state: bool = False) -> dict:
    if reset_state or not state_path.exists():
        return _default_state()

    try:
        loaded = json.loads(state_path.read_text())
    except json.JSONDecodeError as error:
        raise CommandError(
            f'Unable to parse state file {state_path}: {error}') from error

    state = _default_state()
    if isinstance(loaded, dict):
        state.update(loaded)

    state['pending_player_ids'] = [
        int(pid) for pid in state.get('pending_player_ids', [])
        if isinstance(pid, int) or str(pid).isdigit()
    ]
    state['failed_player_ids'] = [
        int(pid) for pid in state.get('failed_player_ids', [])
        if isinstance(pid, int) or str(pid).isdigit()
    ]
    state['next_index'] = max(int(state.get('next_index') or 0), 0)
    return state


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**state, 'updated_at': timezone.now().isoformat()}
    temp_path = state_path.with_suffix(f'{state_path.suffix}.tmp')
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    temp_path.replace(state_path)


def _build_candidate_queue(
    *,
    hot_stale_hours: int,
    active_stale_hours: int,
    warm_stale_hours: int,
    active_limit: int,
    warm_limit: int,
    hot_lookback_days: int,
    active_lookback_days: int,
    warm_lookback_days: int,
    realm: str = DEFAULT_REALM,
) -> tuple[list[int], dict[str, int]]:
    """Build a prioritized candidate queue across Hot, Active, and Warm tiers.

    Returns (ordered_ids, tier_counts).
    """
    now = timezone.now()
    today = now.date()

    base_qs = Player.objects.exclude(
        player_id__isnull=True).filter(realm=realm)

    # -- Hot tier: site visitors within lookback, stale > hot_stale_hours --
    hot_stale_cutoff = now - timedelta(hours=hot_stale_hours)
    hot_lookup_cutoff = now - timedelta(days=hot_lookback_days)
    hot_ids = list(
        base_qs.filter(
            last_lookup__gte=hot_lookup_cutoff,
        ).filter(
            Q(last_fetch__isnull=True) | Q(last_fetch__lt=hot_stale_cutoff),
        ).order_by(
            F('last_lookup').desc(nulls_last=True),
            F('last_fetch').asc(nulls_first=True),
        ).values_list('id', flat=True)
    )
    hot_set = set(hot_ids)

    # -- Active tier: battled within lookback, stale > active_stale_hours --
    active_stale_cutoff = now - timedelta(hours=active_stale_hours)
    active_battle_cutoff = today - timedelta(days=active_lookback_days)
    active_ids = list(
        base_qs.filter(
            last_battle_date__gte=active_battle_cutoff,
        ).filter(
            Q(last_fetch__isnull=True) | Q(last_fetch__lt=active_stale_cutoff),
        ).exclude(
            id__in=hot_set,
        ).order_by(
            F('last_battle_date').desc(nulls_last=True),
            F('pvp_battles').desc(nulls_last=True),
        ).values_list('id', flat=True)[:active_limit]
    )

    # -- Warm tier: battled within warm lookback but not active, stale > warm_stale_hours --
    warm_stale_cutoff = now - timedelta(hours=warm_stale_hours)
    warm_battle_cutoff = today - timedelta(days=warm_lookback_days)
    warm_ids = list(
        base_qs.filter(
            last_battle_date__gte=warm_battle_cutoff,
            last_battle_date__lt=active_battle_cutoff,
        ).filter(
            Q(last_fetch__isnull=True) | Q(last_fetch__lt=warm_stale_cutoff),
        ).order_by(
            F('last_battle_date').desc(nulls_last=True),
        ).values_list('id', flat=True)[:warm_limit]
    )

    tier_counts = {
        'hot': len(hot_ids),
        'active': len(active_ids),
        'warm': len(warm_ids),
    }

    ordered = hot_ids + active_ids + warm_ids
    return ordered, tier_counts


def _refresh_player(player_id: int, realm: str = DEFAULT_REALM) -> None:
    """Refresh a single player through the durable crawler pipeline."""
    player = Player.objects.filter(id=player_id).select_related('clan').first()
    if player is None:
        return

    # Fetch fresh data from WG API (bulk fetch with single ID)
    player_map = fetch_players_bulk([player.player_id], realm=realm)
    player_data = player_map.get(str(player.player_id))
    if player_data is None:
        return

    # save_player handles: core stats, clan FK, hidden clearing, verdict,
    # explorer summary, inline efficiency + achievements
    save_player(player_data, clan=player.clan, realm=realm)

    # Reload to pick up save_player changes
    player.refresh_from_db()

    # Conditional: if save_player's inline calls didn't refresh stale data
    # (e.g. player was already fetched recently but badges are old),
    # explicitly check and refresh
    if not player.is_hidden:
        if player_efficiency_needs_refresh(player):
            update_player_efficiency_data(player, realm=realm)
        if player_achievements_need_refresh(player):
            update_achievements_data(player.player_id, realm=realm)
        refresh_player_detail_payloads(
            player,
            force_refresh=False,
            refresh_core=False,
        )
        player.refresh_from_db()
        if clan_battle_summary_is_stale(player):
            fetch_player_clan_battle_seasons(player.player_id, realm=realm)


class Command(BaseCommand):
    help = (
        'Incrementally refresh player data (core stats, derived detail payloads, efficiency, achievements) '
        'for active players using a tiered priority queue with durable checkpoints.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int,
            default=_env_int('PLAYER_REFRESH_TOTAL_LIMIT', 1200),
            help='Maximum number of player attempts per run.',
        )
        parser.add_argument(
            '--batch-size', type=int, default=50,
            help='Progress-report interval while processing.',
        )
        parser.add_argument(
            '--state-file', default=str(DEFAULT_STATE_FILE),
            help='Path to JSON checkpoint file.',
        )
        parser.add_argument(
            '--reset-state', action='store_true',
            help='Ignore existing checkpoint and rebuild queue.',
        )
        parser.add_argument(
            '--hot-stale-hours', type=int,
            default=_env_int('PLAYER_REFRESH_HOT_STALE_HOURS', 12),
            help='Staleness threshold for Hot tier (site visitors).',
        )
        parser.add_argument(
            '--active-stale-hours', type=int,
            default=_env_int('PLAYER_REFRESH_ACTIVE_STALE_HOURS', 24),
            help='Staleness threshold for Active tier.',
        )
        parser.add_argument(
            '--warm-stale-hours', type=int,
            default=_env_int('PLAYER_REFRESH_WARM_STALE_HOURS', 72),
            help='Staleness threshold for Warm tier.',
        )
        parser.add_argument(
            '--active-limit', type=int,
            default=_env_int('PLAYER_REFRESH_ACTIVE_LIMIT', 500),
            help='Max Active-tier players per cycle.',
        )
        parser.add_argument(
            '--warm-limit', type=int,
            default=_env_int('PLAYER_REFRESH_WARM_LIMIT', 200),
            help='Max Warm-tier players per cycle.',
        )
        parser.add_argument(
            '--hot-lookback-days', type=int,
            default=_env_int('PLAYER_REFRESH_HOT_LOOKBACK_DAYS', 14),
            help='Hot tier: last_lookup recency window.',
        )
        parser.add_argument(
            '--active-lookback-days', type=int,
            default=_env_int('PLAYER_REFRESH_ACTIVE_LOOKBACK_DAYS', 30),
            help='Active tier: last_battle_date recency window.',
        )
        parser.add_argument(
            '--warm-lookback-days', type=int,
            default=_env_int('PLAYER_REFRESH_WARM_LOOKBACK_DAYS', 90),
            help='Warm tier: last_battle_date recency window.',
        )
        parser.add_argument(
            '--max-errors', type=int,
            default=_env_int('PLAYER_REFRESH_MAX_ERRORS', 25),
            help='Error budget before aborting the run.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print candidate queue without refreshing any players.',
        )
        parser.add_argument(
            '--realm', type=str, default=DEFAULT_REALM,
            help='Realm to refresh players for (default: na).',
        )

    def handle(self, *args, **options):
        limit = max(int(options['limit']), 0)
        batch_size = max(int(options['batch_size']), 1)
        max_errors = max(int(options['max_errors']), 1)
        dry_run = bool(options['dry_run'])
        realm = options.get('realm', DEFAULT_REALM) or DEFAULT_REALM
        state_path = Path(options['state_file']).expanduser().resolve()

        # Lock exclusion: skip if legacy clan crawl is running
        if not dry_run and cache.get(_clan_crawl_lock_key(realm)) is not None:
            self.stdout.write(self.style.WARNING(
                'Clan crawl in progress — skipping this cycle.'))
            return

        state = _load_state(
            state_path, reset_state=bool(options['reset_state']))

        pending_player_ids = state.get('pending_player_ids', [])
        next_index = state.get('next_index', 0)
        failed_player_ids = state.get('failed_player_ids', [])

        # Rebuild queue if exhausted or forced
        if not pending_player_ids or (
            next_index >= len(pending_player_ids) and not failed_player_ids
        ):
            pending_player_ids, tier_counts = _build_candidate_queue(
                hot_stale_hours=max(int(options['hot_stale_hours']), 0),
                active_stale_hours=max(int(options['active_stale_hours']), 0),
                warm_stale_hours=max(int(options['warm_stale_hours']), 0),
                active_limit=max(int(options['active_limit']), 0),
                warm_limit=max(int(options['warm_limit']), 0),
                hot_lookback_days=max(int(options['hot_lookback_days']), 0),
                active_lookback_days=max(
                    int(options['active_lookback_days']), 0),
                warm_lookback_days=max(int(options['warm_lookback_days']), 0),
                realm=realm,
            )
            state['pending_player_ids'] = pending_player_ids
            state['next_index'] = 0
            state['failed_player_ids'] = []
            state['last_error'] = None
            state['tier_counts'] = tier_counts
            state['cycle_started_at'] = timezone.now().isoformat()
            state['cycle_completed_at'] = None
            _save_state(state_path, state)

            self.stdout.write(
                f'Built candidate queue: '
                f'hot={tier_counts["hot"]}, '
                f'active={tier_counts["active"]}, '
                f'warm={tier_counts["warm"]}, '
                f'total={len(pending_player_ids)}'
            )

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'Dry run — {len(pending_player_ids)} candidates queued. No data refreshed.'
            ))
            return

        attempted_this_run = 0
        succeeded_this_run = 0
        errors_this_run = 0

        def record_success(*, is_retry: bool = False) -> None:
            nonlocal attempted_this_run, succeeded_this_run
            attempted_this_run += 1
            succeeded_this_run += 1
            state['processed_total'] += 1
            state['succeeded_total'] += 1
            state['last_error'] = None
            if not is_retry:
                state['next_index'] += 1
            _save_state(state_path, state)

        def record_error(player_id: int, error: Exception, *, is_retry: bool = False) -> None:
            nonlocal attempted_this_run, errors_this_run
            attempted_this_run += 1
            errors_this_run += 1
            state['processed_total'] += 1
            state['error_total'] += 1
            state['last_error'] = f'{player_id}:{error}'
            if not is_retry:
                state['next_index'] += 1
            failed_ids = [
                cid for cid in state['failed_player_ids'] if cid != player_id]
            failed_ids.append(player_id)
            state['failed_player_ids'] = failed_ids
            _save_state(state_path, state)

        def should_stop() -> bool:
            return (limit and attempted_this_run >= limit) or errors_this_run >= max_errors

        # Retry previously failed players first
        failed_retry_ids = list(dict.fromkeys(
            int(pid) for pid in state.get('failed_player_ids', [])))
        if failed_retry_ids:
            self.stdout.write(
                f'Retrying {len(failed_retry_ids)} previously failed player(s).')

        for player_id in failed_retry_ids:
            if should_stop():
                break
            try:
                _refresh_player(player_id, realm=realm)
                state['failed_player_ids'] = [
                    cid for cid in state['failed_player_ids'] if cid != player_id]
                record_success(is_retry=True)
            except Exception as error:
                self.stderr.write(
                    f'Failed player refresh retry for id={player_id}: {error}')
                record_error(player_id, error, is_retry=True)

        # Process main queue
        for player_id in pending_player_ids[state['next_index']:]:
            if should_stop():
                break

            try:
                _refresh_player(player_id, realm=realm)
                state['failed_player_ids'] = [
                    cid for cid in state['failed_player_ids'] if cid != player_id]
                record_success()
            except Exception as error:
                self.stderr.write(
                    f'Failed player refresh for id={player_id}: {error}')
                record_error(player_id, error)

            if attempted_this_run and attempted_this_run % batch_size == 0:
                self.stdout.write(
                    f'Progress: {attempted_this_run} attempted, '
                    f'queue index {state["next_index"]}/{len(pending_player_ids)}.'
                )

        # Mark cycle complete if queue exhausted
        if state['next_index'] >= len(state['pending_player_ids']) and not state['failed_player_ids']:
            state['cycle_completed_at'] = timezone.now().isoformat()
            state['pending_player_ids'] = []
            state['next_index'] = 0
            _save_state(state_path, state)

        if errors_this_run >= max_errors:
            self.stderr.write(self.style.WARNING(
                f'Aborting after {errors_this_run} errors in this run. '
                f'Resume with the same --state-file.'
            ))

        self.stdout.write(self.style.SUCCESS(
            'Player refresh run complete: '
            f'attempted={attempted_this_run}, '
            f'succeeded={succeeded_this_run}, '
            f'errors={errors_this_run}, '
            f'queue_remaining={max(len(state["pending_player_ids"]) - state["next_index"], 0)}, '
            f'failed_pending={len(state["failed_player_ids"])}'
        ))

import json
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q
from django.utils import timezone

from warships.data import update_ranked_data
from warships.models import DEFAULT_REALM, Player


DEFAULT_STATE_FILE = Path(settings.BASE_DIR) / 'logs' / \
    'incremental_ranked_data_state.json'


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
        int(player_id) for player_id in state.get('pending_player_ids', [])
        if isinstance(player_id, int) or str(player_id).isdigit()
    ]
    state['failed_player_ids'] = [
        int(player_id) for player_id in state.get('failed_player_ids', [])
        if isinstance(player_id, int) or str(player_id).isdigit()
    ]
    state['next_index'] = max(int(state.get('next_index') or 0), 0)
    return state


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**state, 'updated_at': timezone.now().isoformat()}
    temp_path = state_path.with_suffix(f'{state_path.suffix}.tmp')
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    temp_path.replace(state_path)


def _interleave_candidate_ids(known_ranked_ids: list[int], discovery_ids: list[int]) -> list[int]:
    if not known_ranked_ids:
        return discovery_ids
    if not discovery_ids:
        return known_ranked_ids

    seen: set[int] = set()
    ordered: list[int] = []
    known_index = 0
    discovery_index = 0
    known_per_discovery = max(
        1, (len(known_ranked_ids) + len(discovery_ids) - 1) // len(discovery_ids))

    while known_index < len(known_ranked_ids) or discovery_index < len(discovery_ids):
        for _ in range(known_per_discovery):
            if known_index >= len(known_ranked_ids):
                break
            player_id = known_ranked_ids[known_index]
            known_index += 1
            if player_id in seen:
                continue
            seen.add(player_id)
            ordered.append(player_id)

        if discovery_index >= len(discovery_ids):
            continue

        player_id = discovery_ids[discovery_index]
        discovery_index += 1
        if player_id in seen:
            continue
        seen.add(player_id)
        ordered.append(player_id)

    return ordered


def _build_candidate_queue(
    *,
    include_hidden: bool,
    skip_fresh_hours: int,
    known_limit: int,
    discovery_limit: int,
    recent_lookup_days: int,
    recent_battle_days: int,
    min_discovery_pvp_battles: int,
    realm: str = DEFAULT_REALM,
) -> list[int]:
    now = timezone.now()
    today = now.date()
    fresh_cutoff = now - timedelta(hours=skip_fresh_hours)
    recent_lookup_cutoff = now - timedelta(days=recent_lookup_days)
    recent_battle_cutoff = today - timedelta(days=recent_battle_days)

    base_queryset = Player.objects.exclude(player_id__isnull=True).filter(realm=realm)
    if not include_hidden:
        base_queryset = base_queryset.filter(is_hidden=False)

    stale_known_filter = Q(ranked_updated_at__isnull=True) | Q(
        ranked_updated_at__lt=fresh_cutoff)

    known_ranked_ids = list(
        base_queryset.exclude(ranked_json__isnull=True)
        .exclude(ranked_json=[])
        .filter(stale_known_filter)
        .order_by(
            F('last_lookup').desc(nulls_last=True),
            F('last_battle_date').desc(nulls_last=True),
            F('ranked_updated_at').asc(nulls_first=True),
            F('pvp_battles').desc(nulls_last=True),
            'id',
        )
        .values_list('id', flat=True)[:known_limit]
    )

    discovery_ids = list(
        base_queryset.filter(Q(ranked_json__isnull=True) | Q(ranked_json=[]))
        .filter(pvp_battles__gte=min_discovery_pvp_battles)
        .filter(
            Q(last_lookup__gte=recent_lookup_cutoff)
            | Q(last_battle_date__gte=recent_battle_cutoff)
        )
        .order_by(
            F('last_lookup').desc(nulls_last=True),
            F('last_battle_date').desc(nulls_last=True),
            F('pvp_battles').desc(nulls_last=True),
            'id',
        )
        .values_list('id', flat=True)[:discovery_limit]
    )

    return _interleave_candidate_ids(known_ranked_ids, discovery_ids)


class Command(BaseCommand):
    help = 'Refresh ranked data incrementally with a durable checkpoint and prioritized queue.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=_env_int('RANKED_INCREMENTAL_LIMIT', 150),
                            help='Maximum number of player attempts to process in this run.')
        parser.add_argument('--batch-size', type=int, default=50,
                            help='Progress-report interval while processing the queue.')
        parser.add_argument('--state-file', default=str(DEFAULT_STATE_FILE),
                            help='Path to the JSON checkpoint file used for resumable progress.')
        parser.add_argument('--reset-state', action='store_true',
                            help='Ignore any existing checkpoint and rebuild the queue.')
        parser.add_argument('--rebuild-queue', action='store_true',
                            help='Recompute the ranked incremental queue before processing.')
        parser.add_argument('--include-hidden', action='store_true',
                            help='Include hidden players instead of only visible players.')
        parser.add_argument('--skip-fresh-hours', type=int, default=_env_int('RANKED_INCREMENTAL_SKIP_FRESH_HOURS', 24),
                            help='Do not requeue ranked rows updated within this many hours.')
        parser.add_argument('--known-limit', type=int, default=_env_int('RANKED_INCREMENTAL_KNOWN_LIMIT', 300),
                            help='Maximum number of stale known-ranked players to queue in one cycle.')
        parser.add_argument('--discovery-limit', type=int, default=_env_int('RANKED_INCREMENTAL_DISCOVERY_LIMIT', 75),
                            help='Maximum number of discovery candidates without ranked data to queue in one cycle.')
        parser.add_argument('--recent-lookup-days', type=int, default=14,
                            help='Discovery candidates must have been looked up within this many days.')
        parser.add_argument('--recent-battle-days', type=int, default=30,
                            help='Discovery candidates may also qualify via recent battle date.')
        parser.add_argument('--min-discovery-pvp-battles', type=int, default=1000,
                            help='Minimum PvP battles for discovery candidates without ranked data.')
        parser.add_argument('--max-errors', type=int, default=25,
                            help='Abort the run after this many errors in a single invocation.')
        parser.add_argument('--realm', type=str, default=DEFAULT_REALM,
                            help='Realm to refresh ranked data for (default: na).')

    def handle(self, *args, **options):
        limit = max(int(options['limit']), 0)
        batch_size = max(int(options['batch_size']), 1)
        max_errors = max(int(options['max_errors']), 1)
        include_hidden = bool(options['include_hidden'])
        realm = options.get('realm', DEFAULT_REALM) or DEFAULT_REALM
        skip_fresh_hours = max(int(options['skip_fresh_hours']), 0)
        known_limit = max(int(options['known_limit']), 0)
        discovery_limit = max(int(options['discovery_limit']), 0)
        recent_lookup_days = max(int(options['recent_lookup_days']), 0)
        recent_battle_days = max(int(options['recent_battle_days']), 0)
        min_discovery_pvp_battles = max(
            int(options['min_discovery_pvp_battles']), 0)
        state_path = Path(options['state_file']).expanduser().resolve()

        state = _load_state(
            state_path, reset_state=bool(options['reset_state']))

        pending_player_ids = state.get('pending_player_ids', [])
        next_index = state.get('next_index', 0)
        failed_player_ids = state.get('failed_player_ids', [])
        if bool(options['rebuild_queue']) or not pending_player_ids or (
            next_index >= len(pending_player_ids) and not failed_player_ids
        ):
            pending_player_ids = _build_candidate_queue(
                include_hidden=include_hidden,
                skip_fresh_hours=skip_fresh_hours,
                known_limit=known_limit,
                discovery_limit=discovery_limit,
                recent_lookup_days=recent_lookup_days,
                recent_battle_days=recent_battle_days,
                min_discovery_pvp_battles=min_discovery_pvp_battles,
                realm=realm,
            )
            state['pending_player_ids'] = pending_player_ids
            state['next_index'] = 0
            state['failed_player_ids'] = []
            state['last_error'] = None
            state['cycle_started_at'] = timezone.now().isoformat()
            state['cycle_completed_at'] = None
            _save_state(state_path, state)

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
                candidate_id for candidate_id in state['failed_player_ids'] if candidate_id != player_id]
            failed_ids.append(player_id)
            state['failed_player_ids'] = failed_ids
            _save_state(state_path, state)

        def should_stop() -> bool:
            return (limit and attempted_this_run >= limit) or errors_this_run >= max_errors

        failed_retry_ids = list(dict.fromkeys(int(player_id)
                                for player_id in state.get('failed_player_ids', [])))
        if failed_retry_ids:
            self.stdout.write(
                f'Retrying {len(failed_retry_ids)} failed ranked incremental player(s) before continuing.')

        retry_players = {
            player.id: player
            for player in Player.objects.filter(id__in=failed_retry_ids).only('id', 'player_id', 'name')
        }
        for player_id in failed_retry_ids:
            if should_stop():
                break
            player = retry_players.get(player_id)
            if player is None:
                state['failed_player_ids'] = [
                    candidate_id for candidate_id in state['failed_player_ids'] if candidate_id != player_id]
                _save_state(state_path, state)
                continue
            try:
                update_ranked_data(player.player_id, realm=realm)
                state['failed_player_ids'] = [
                    candidate_id for candidate_id in state['failed_player_ids'] if candidate_id != player.id]
                record_success(is_retry=True)
            except Exception as error:
                self.stderr.write(
                    f'Failed ranked incremental retry for {player.name} ({player.player_id}): {error}')
                record_error(player.id, error, is_retry=True)

        for player_id in pending_player_ids[state['next_index']:]:
            if should_stop():
                break

            player = Player.objects.filter(id=player_id).only(
                'id', 'player_id', 'name').first()
            if player is None:
                record_success()
                continue

            try:
                update_ranked_data(player.player_id, realm=realm)
                state['failed_player_ids'] = [
                    candidate_id for candidate_id in state['failed_player_ids'] if candidate_id != player.id]
                record_success()
            except Exception as error:
                self.stderr.write(
                    f'Failed ranked incremental refresh for {player.name} ({player.player_id}): {error}')
                record_error(player.id, error)

            if attempted_this_run and attempted_this_run % batch_size == 0:
                self.stdout.write(
                    f'Attempted {attempted_this_run} players in this run; queue index {state["next_index"]}/{len(pending_player_ids)}.'
                )

        if state['next_index'] >= len(state['pending_player_ids']) and not state['failed_player_ids']:
            state['cycle_completed_at'] = timezone.now().isoformat()
            state['pending_player_ids'] = []
            state['next_index'] = 0
            _save_state(state_path, state)

        if errors_this_run >= max_errors:
            self.stderr.write(self.style.WARNING(
                f'Aborting after {errors_this_run} errors in this run. Resume with the same --state-file.'
            ))

        self.stdout.write(self.style.SUCCESS(
            'Ranked incremental run complete: '
            f'attempted={attempted_this_run}, '
            f'succeeded={succeeded_this_run}, '
            f'errors={errors_this_run}, '
            f'queue_remaining={max(len(state["pending_player_ids"]) - state["next_index"], 0)}, '
            f'failed_pending={len(state["failed_player_ids"])}'
        ))

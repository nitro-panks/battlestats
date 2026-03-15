import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from warships.data import update_ranked_data
from warships.models import Player


DEFAULT_STATE_FILE = Path(settings.BASE_DIR) / 'logs' / \
    'repair_ranked_overcount_state.json'


def _default_state() -> dict:
    return {
        'version': 1,
        'last_player_id': 0,
        'scanned_total': 0,
        'affected_total': 0,
        'repaired_total': 0,
        'error_total': 0,
        'failed_player_ids': [],
        'last_error': None,
        'updated_at': None,
        'completed_at': None,
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
    state['failed_player_ids'] = [
        int(player_id) for player_id in state.get('failed_player_ids', [])
        if isinstance(player_id, int) or str(player_id).isdigit()
    ]
    state['last_player_id'] = int(state.get('last_player_id') or 0)
    return state


def _save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**state, 'updated_at': timezone.now().isoformat()}
    temp_path = state_path.with_suffix(f'{state_path.suffix}.tmp')
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    temp_path.replace(state_path)


def _row_has_impossible_ranked_totals(row: dict) -> bool:
    battles = int(row.get('total_battles', 0) or 0)
    wins = int(row.get('total_wins', 0) or 0)
    win_rate = float(row.get('win_rate', 0) or 0)

    return (battles > 0 and wins > battles) or win_rate > 1.0


def _player_has_impossible_ranked_rows(player: Player) -> bool:
    ranked_rows = player.ranked_json or []
    if not isinstance(ranked_rows, list):
        return False

    return any(
        isinstance(row, dict) and _row_has_impossible_ranked_totals(row)
        for row in ranked_rows
    )


class Command(BaseCommand):
    help = 'Repair players whose stored ranked_json contains impossible wins/battles totals.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Optional maximum number of affected players to process in this run.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Progress-report interval while scanning ranked players.',
        )
        parser.add_argument(
            '--state-file',
            default=str(DEFAULT_STATE_FILE),
            help='Path to the JSON checkpoint file used for resumable progress.',
        )
        parser.add_argument(
            '--reset-state',
            action='store_true',
            help='Ignore any existing checkpoint and rescan from the beginning.',
        )
        parser.add_argument(
            '--include-hidden',
            action='store_true',
            help='Include hidden players instead of only visible players.',
        )
        parser.add_argument(
            '--audit-only',
            action='store_true',
            help='Scan and report affected players without calling update_ranked_data.',
        )
        parser.add_argument(
            '--max-errors',
            type=int,
            default=25,
            help='Abort the run after this many errors in a single invocation.',
        )

    def handle(self, *args, **options):
        limit = max(int(options['limit']), 0)
        batch_size = max(int(options['batch_size']), 1)
        max_errors = max(int(options['max_errors']), 1)
        include_hidden = bool(options['include_hidden'])
        audit_only = bool(options['audit_only'])
        state_path = Path(options['state_file']).expanduser().resolve()

        state = _load_state(
            state_path, reset_state=bool(options['reset_state']))
        affected_this_run = 0
        repaired_this_run = 0
        errors_this_run = 0
        scanned_this_run = 0

        def should_stop() -> bool:
            return (limit and affected_this_run >= limit) or errors_this_run >= max_errors

        def persist() -> None:
            state['completed_at'] = None
            _save_state(state_path, state)

        def record_scan(player: Player, affected: bool) -> None:
            nonlocal scanned_this_run, affected_this_run
            scanned_this_run += 1
            state['scanned_total'] += 1
            state['last_player_id'] = player.id
            if affected:
                affected_this_run += 1
                state['affected_total'] += 1

        def record_repair_success() -> None:
            nonlocal repaired_this_run
            repaired_this_run += 1
            state['repaired_total'] += 1
            state['last_error'] = None

        def record_error(player: Player, error: Exception) -> None:
            nonlocal errors_this_run
            errors_this_run += 1
            state['error_total'] += 1
            state['last_error'] = f'{player.id}:{player.player_id}:{error}'
            failed_ids = [
                player_id for player_id in state['failed_player_ids'] if player_id != player.id]
            failed_ids.append(player.id)
            state['failed_player_ids'] = failed_ids

        pending_failures = list(dict.fromkeys(int(player_id)
                                for player_id in state.get('failed_player_ids', [])))
        if pending_failures:
            self.stdout.write(
                f'Retrying {len(pending_failures)} previously failed player(s) from {state_path} before continuing.'
            )

        for player in Player.objects.filter(id__in=pending_failures).order_by('id'):
            if should_stop():
                break

            try:
                update_ranked_data(player.player_id)
                record_repair_success()
                state['failed_player_ids'] = [
                    player_id for player_id in state['failed_player_ids'] if player_id != player.id]
                persist()
            except Exception as error:
                self.stderr.write(
                    f'Failed repair retry for {player.name} ({player.player_id}): {error}')
                record_error(player, error)
                persist()

        if not should_stop():
            queryset = Player.objects.exclude(ranked_json__isnull=True).exclude(
                ranked_json=[]).order_by('id')
            if not include_hidden:
                queryset = queryset.filter(is_hidden=False)
            queryset = queryset.filter(id__gt=state['last_player_id'])

            for player in queryset.iterator(chunk_size=batch_size):
                if should_stop():
                    break

                affected = _player_has_impossible_ranked_rows(player)
                record_scan(player, affected)

                if not affected:
                    if scanned_this_run % batch_size == 0:
                        persist()
                        self.stdout.write(
                            f'Scanned {scanned_this_run} ranked players in this run...')
                    continue

                if audit_only:
                    self.stdout.write(
                        f'Affected ranked cache: {player.name} ({player.player_id})')
                    persist()
                    if scanned_this_run % batch_size == 0:
                        self.stdout.write(
                            f'Scanned {scanned_this_run} ranked players in this run...')
                    continue

                try:
                    update_ranked_data(player.player_id)
                    record_repair_success()
                    state['failed_player_ids'] = [
                        player_id for player_id in state['failed_player_ids'] if player_id != player.id]
                except Exception as error:
                    self.stderr.write(
                        f'Failed ranked repair for {player.name} ({player.player_id}): {error}')
                    record_error(player, error)

                persist()
                if scanned_this_run % batch_size == 0:
                    self.stdout.write(
                        f'Scanned {scanned_this_run} ranked players in this run...')

        if errors_this_run >= max_errors:
            self.stderr.write(self.style.WARNING(
                f'Aborting after {errors_this_run} errors in this run. Resume with the same --state-file.'
            ))

        if not should_stop() and not state['failed_player_ids']:
            state['completed_at'] = timezone.now().isoformat()
            _save_state(state_path, state)

        mode = 'audit' if audit_only else 'repair'
        self.stdout.write(self.style.SUCCESS(
            f'Ranked overcount {mode} complete: '
            f'scanned_this_run={scanned_this_run}, '
            f'affected_this_run={affected_this_run}, '
            f'repaired_this_run={repaired_this_run}, '
            f'errors_this_run={errors_this_run}, '
            f'scanned_total={state["scanned_total"]}, '
            f'affected_total={state["affected_total"]}, '
            f'repaired_total={state["repaired_total"]}, '
            f'failed_pending={len(state["failed_player_ids"])}'
        ))

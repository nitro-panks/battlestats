#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Run or inspect the durable ranked-data backfill command.'
    )
    parser.add_argument(
        '--state-file',
        default='logs/backfill_ranked_data_state.json',
        help='Checkpoint file used by the backfill command.',
    )
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--batch-size', type=int, default=100)
    parser.add_argument('--refresh-older-than-hours', type=int, default=0)
    parser.add_argument('--max-errors', type=int, default=25)
    parser.add_argument('--include-hidden', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--reset-state', action='store_true')
    parser.add_argument(
        '--status-only',
        action='store_true',
        help='Print the current checkpoint JSON and exit without running the backfill.',
    )
    return parser.parse_args()


def _print_state(state_path: Path) -> int:
    if not state_path.exists():
        print(json.dumps({
            'state_file': str(state_path),
            'exists': False,
        }, indent=2))
        return 0

    print(state_path.read_text())
    return 0


def main() -> int:
    args = _parse_args()
    base_dir = Path(__file__).resolve().parents[1]
    os.chdir(base_dir)
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))

    from battlestats.env import load_default_env_files

    loaded_paths = load_default_env_files(base_dir)
    loaded_names = ', '.join(
        path.name for path in loaded_paths) or 'no env files found'
    print(f'Loading environment variables from {loaded_names}')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'battlestats.settings')

    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = (base_dir / state_path).resolve()

    if args.status_only:
        return _print_state(state_path)

    import django
    django.setup()

    from django.core.management import call_command

    command_kwargs = {
        'state_file': str(state_path),
        'limit': args.limit,
        'batch_size': args.batch_size,
        'refresh_older_than_hours': args.refresh_older_than_hours,
        'max_errors': args.max_errors,
        'include_hidden': args.include_hidden,
        'force': args.force,
        'reset_state': args.reset_state,
    }
    call_command('backfill_ranked_data', **command_kwargs)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Run or inspect the durable ranked incremental refresh command.'
    )
    parser.add_argument(
        '--state-file', default='logs/incremental_ranked_data_state.json')
    parser.add_argument('--limit', type=int,
                        default=_env_int('RANKED_INCREMENTAL_LIMIT', 150))
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--skip-fresh-hours', type=int,
                        default=_env_int('RANKED_INCREMENTAL_SKIP_FRESH_HOURS', 24))
    parser.add_argument('--known-limit', type=int,
                        default=_env_int('RANKED_INCREMENTAL_KNOWN_LIMIT', 300))
    parser.add_argument('--discovery-limit', type=int,
                        default=_env_int('RANKED_INCREMENTAL_DISCOVERY_LIMIT', 75))
    parser.add_argument('--recent-lookup-days', type=int, default=14)
    parser.add_argument('--recent-battle-days', type=int, default=30)
    parser.add_argument('--min-discovery-pvp-battles', type=int, default=1000)
    parser.add_argument('--max-errors', type=int, default=25)
    parser.add_argument('--include-hidden', action='store_true')
    parser.add_argument('--reset-state', action='store_true')
    parser.add_argument('--rebuild-queue', action='store_true')
    parser.add_argument('--status-only', action='store_true')
    return parser.parse_args()


def _print_state(state_path: Path) -> int:
    if not state_path.exists():
        print(json.dumps(
            {'state_file': str(state_path), 'exists': False}, indent=2))
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

    call_command(
        'incremental_ranked_data',
        state_file=str(state_path),
        limit=args.limit,
        batch_size=args.batch_size,
        skip_fresh_hours=args.skip_fresh_hours,
        known_limit=args.known_limit,
        discovery_limit=args.discovery_limit,
        recent_lookup_days=args.recent_lookup_days,
        recent_battle_days=args.recent_battle_days,
        min_discovery_pvp_battles=args.min_discovery_pvp_battles,
        max_errors=args.max_errors,
        include_hidden=args.include_hidden,
        reset_state=args.reset_state,
        rebuild_queue=args.rebuild_queue,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

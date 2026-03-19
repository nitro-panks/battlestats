#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path

from battlestats.env import load_default_env_files


def main():
    base_dir = Path(__file__).resolve().parent
    loaded_paths = load_default_env_files(base_dir)
    loaded_names = ', '.join(
        path.name for path in loaded_paths) or 'no env files found'
    print(f'Loading environment variables from {loaded_names}')
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'battlestats.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()

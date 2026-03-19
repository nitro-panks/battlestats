import os
from pathlib import Path

import dotenv


DEFAULT_ENV_FILE_NAMES = ('.env', '.env.secrets')


def load_env_file(path: str) -> None:
    if hasattr(dotenv, 'read_dotenv'):
        dotenv.read_dotenv(path)
        return

    if hasattr(dotenv, 'load_dotenv'):
        dotenv.load_dotenv(path)


def load_default_env_files(base_dir: str | Path) -> list[Path]:
    resolved_base_dir = Path(base_dir)
    loaded_paths: list[Path] = []

    for file_name in DEFAULT_ENV_FILE_NAMES:
        candidate = resolved_base_dir / file_name
        if not candidate.exists():
            continue
        load_env_file(str(candidate))
        loaded_paths.append(candidate)

    return loaded_paths


def running_in_container() -> bool:
    return Path('/.dockerenv').exists()


def resolve_db_user() -> str:
    return (os.getenv('DB_USERNAME') or os.getenv('DB_USER') or 'django').strip()


def resolve_db_host() -> str:
    host = (os.getenv('DB_HOST') or '127.0.0.1').strip()
    if host != 'db' or running_in_container():
        return host
    return '127.0.0.1'

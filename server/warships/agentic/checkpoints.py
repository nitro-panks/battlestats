from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Literal
from urllib.parse import quote_plus, urlencode

from langgraph.checkpoint.memory import MemorySaver

try:
    from langgraph.checkpoint.postgres import PostgresSaver
except ImportError:
    PostgresSaver = None


CheckpointBackend = Literal['memory', 'postgres']


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def get_langgraph_checkpoint_postgres_url() -> str | None:
    explicit_url = os.getenv('LANGGRAPH_CHECKPOINT_POSTGRES_URL', '').strip()
    if explicit_url:
        return explicit_url

    db_engine = os.getenv('DB_ENGINE', 'postgresql_psycopg2').strip().lower()
    if not db_engine.startswith('postgresql'):
        return None

    db_name = os.getenv('DB_NAME', 'battlestats').strip()
    db_user = (os.getenv('DB_USERNAME') or os.getenv(
        'DB_USER') or 'django').strip()
    db_password = os.getenv('DB_PASSWORD', '').strip()
    db_host = os.getenv('DB_HOST', '127.0.0.1').strip()
    db_port = os.getenv('DB_PORT', '5432').strip()
    db_sslmode = os.getenv('DB_SSLMODE', '').strip()
    db_sslrootcert = os.getenv('DB_SSLROOTCERT', '').strip()

    if not all([db_name, db_user, db_host, db_port]):
        return None

    quoted_user = quote_plus(db_user)
    quoted_password = quote_plus(db_password)
    password_segment = f':{quoted_password}' if db_password else ''
    query_params: dict[str, str] = {}
    if db_sslmode:
        query_params['sslmode'] = db_sslmode
    if db_sslrootcert:
        query_params['sslrootcert'] = db_sslrootcert
    query_string = f'?{urlencode(query_params)}' if query_params else ''
    return f'postgresql://{quoted_user}{password_segment}@{db_host}:{db_port}/{db_name}{query_string}'


def get_checkpoint_backend_name(context: dict[str, Any] | None = None) -> CheckpointBackend:
    context = context or {}
    backend_override = str(context.get(
        'checkpoint_backend', '')).strip().lower()

    if backend_override == 'memory':
        return 'memory'

    if backend_override == 'postgres':
        return 'postgres'

    if PostgresSaver is not None and get_langgraph_checkpoint_postgres_url():
        return 'postgres'

    return 'memory'


@contextmanager
def get_graph_checkpointer(
    context: dict[str, Any] | None = None,
) -> Iterator[MemorySaver | Any]:
    backend = get_checkpoint_backend_name(context=context)

    if backend == 'memory' or PostgresSaver is None:
        yield MemorySaver()
        return

    conn_string = get_langgraph_checkpoint_postgres_url()
    if not conn_string:
        if context and str(context.get('checkpoint_backend', '')).strip().lower() == 'postgres':
            raise RuntimeError(
                'checkpoint_backend=postgres requested but no Postgres checkpoint URL could be resolved'
            )
        yield MemorySaver()
        return

    with PostgresSaver.from_conn_string(conn_string) as saver:
        if _env_flag('LANGGRAPH_CHECKPOINT_AUTO_SETUP', True):
            saver.setup()
        yield saver

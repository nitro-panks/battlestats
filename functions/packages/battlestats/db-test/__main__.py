"""
Connectivity test: verifies the function can reach managed Postgres
and query the battlestats database.
"""

import os
import base64
import tempfile
import time

import psycopg2


_conn = None
_ca_path = None


def _get_ca_path():
    global _ca_path
    if _ca_path:
        return _ca_path

    ca_b64 = os.environ.get("DB_CA_CERT_B64", "")
    if not ca_b64:
        return None

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    tmp.write(base64.b64decode(ca_b64))
    tmp.flush()
    tmp.close()
    _ca_path = tmp.name
    return _ca_path


def _get_connection():
    global _conn
    if _conn is not None and not _conn.closed:
        try:
            _conn.cursor().execute("SELECT 1")
            return _conn
        except Exception:
            _conn = None

    params = {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "25060")),
        "dbname": os.environ.get("DB_NAME", "defaultdb"),
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "sslmode": "require",
        "connect_timeout": 10,
    }

    ca_path = _get_ca_path()
    if ca_path:
        params["sslmode"] = "verify-full"
        params["sslrootcert"] = ca_path

    _conn = psycopg2.connect(**params)
    _conn.autocommit = True
    return _conn


def main(event, context):
    start = time.time()

    try:
        conn = _get_connection()
        cur = conn.cursor()

        # Basic connectivity
        cur.execute("SELECT version()")
        pg_version = cur.fetchone()[0]

        # App-level query: count enriched players
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE battles_json IS NOT NULL) AS has_battles
            FROM warships_player
            WHERE realm = 'na' AND is_hidden = false
                AND pvp_battles >= 500 AND pvp_ratio >= 48.0
        """)
        total, has_battles = cur.fetchone()

        cur.close()
        elapsed = round(time.time() - start, 3)

        return {
            "body": {
                "status": "ok",
                "pg_version": pg_version,
                "na_eligible_total": total,
                "na_enriched_battles_json": has_battles,
                "na_remaining": total - has_battles,
                "elapsed_seconds": elapsed,
            }
        }

    except Exception as exc:
        elapsed = round(time.time() - start, 3)
        return {
            "statusCode": 500,
            "body": {
                "status": "error",
                "error": str(exc),
                "elapsed_seconds": elapsed,
            }
        }

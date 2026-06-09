from django.db import migrations


def create_last_fetch_index(apps, schema_editor):
    # Postgres-only; the release gate / local subsets build the SQLite test DB
    # with `--nomigrations`. Mirror the guarded pattern in 0063 (BRIN) / 0019
    # (pg_trgm GIN) — the index lives in this migration, not in Player.Meta, so
    # the syncdb test-DB build never emits it.
    #
    # CONCURRENTLY (with atomic=False below) avoids a write lock on the large
    # ~1M-row warships_player table. A plain btree on last_fetch backs the
    # incremental enrichment reclassify's `last_fetch >= now - N hours` range
    # filter (enrichment_pool_maintenance) so the daily drift-rescue pass scans
    # only the recently-fetched set instead of the full catalog.
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS player_last_fetch_idx "
        "ON warships_player (last_fetch)"
    )


def drop_last_fetch_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS player_last_fetch_idx"
    )


class Migration(migrations.Migration):
    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("warships", "0066_playeractivityhourly"),
    ]

    operations = [
        migrations.RunPython(create_last_fetch_index, reverse_code=drop_last_fetch_index),
    ]

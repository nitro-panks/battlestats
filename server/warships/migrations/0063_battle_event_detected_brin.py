from django.db import migrations


def create_brin_index(apps, schema_editor):
    # Postgres-only; the release gate / local subsets build the SQLite test DB
    # with `--nomigrations` and SQLite has no BRIN. Mirror the guarded pattern
    # in 0019 (pg_trgm GIN index) — the index lives in this migration, not in
    # BattleEvent.Meta, so the syncdb test-DB build never emits `USING brin`.
    #
    # CONCURRENTLY (with atomic=False below) avoids a disruptive lock on the
    # large, high-write BattleEvent table. detected_at is monotonic, so a BRIN
    # index range-prunes the per-day rollup + reconciliation scans cheaply.
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS battle_event_detected_brin "
        "ON warships_battleevent USING brin (detected_at)"
    )


def drop_brin_index(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS battle_event_detected_brin"
    )


class Migration(migrations.Migration):
    # CREATE/DROP INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("warships", "0062_shipaward"),
    ]

    operations = [
        migrations.RunPython(create_brin_index, reverse_code=drop_brin_index),
    ]

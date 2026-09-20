"""Drop two indexes on PlayerDailyShipStats that nothing plans on.

`warships_playerdailyshipstats` is the highest-write table in the schema
(~20.5M rows) and carried 4,158 MB of index against 4,282 MB of heap. Two of
those indexes were dead weight, measured on production 2026-09-19/20 with
`pg_stat_user_indexes` counters that have **never been reset** (so the numbers
run back to the cluster's creation in March):

  warships_playerdailyshipstats_mode_5a941e36       197 MB,  1 scan
  warships_playerdailyshipstats_player_id_daed36c5  191 MB, 10 scans (546 tuples)

The single `mode` scan was the read-only probe run to decide this. Two values
across 20.5M rows is far too low a cardinality for the planner to pick it, and
every real query pairs mode with a player or a date.

The `player_id` index is the ForeignKey's automatic one. Every query filtering
on player here also carries a date or a ship, so `dly_ship_player_date_idx`
(player, -date) and `dly_ship_player_shipdt_idx` (player, ship_id, -date)
already serve them — both lead with this column. Postgres does not require an
index on the referencing side, and Django emulates the cascade in Python with a
query those composites serve.

Plans verified on production before writing this, with EXPLAIN ANALYZE:
  * player-timeline payload (player + date window) -> dly_ship_player_date_idx
  * clan-roster active-PvP probe (player IN + date) -> dly_ship_player_date_idx
  * rollup realm-day scan (date + mode)            -> the date index, parallel

Rollback is `db_index=True` and a new migration; recreating either index on this
table is minutes of CPU, not a data risk.

Runbook: agents/runbooks/runbook-db-capacity-remediation-2026-09-19.md, Step 4.
"""

import django.db.models.deletion
from django.db import migrations, models


def _bound_lock_wait(apps, schema_editor):
    """Cap how long the DROP INDEX statements will wait for their lock.

    Guarded on the vendor the way migration 0073 does it: the sqlite test
    harness has no `lock_timeout`, and this is a production-safety measure
    rather than a schema change, so it is a no-op everywhere else.
    """
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("SET lock_timeout = '5s'")


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0086_add_feedback'),
    ]

    operations = [
        # Bound the lock wait. DROP INDEX needs ACCESS EXCLUSIVE on the table,
        # and a blocked lock request queues every subsequent query behind it —
        # on this table that is the player timeline, the clan roster and the
        # nightly rollup. Failing fast and retrying in a quieter minute is much
        # cheaper than holding that queue. The drops themselves are metadata
        # plus a file unlink; they do not rewrite the table.
        migrations.RunPython(_bound_lock_wait, _bound_lock_wait),
        migrations.AlterField(
            model_name='playerdailyshipstats',
            name='mode',
            field=models.CharField(
                choices=[('random', 'Random'), ('ranked', 'Ranked')],
                default='random', max_length=8),
        ),
        migrations.AlterField(
            model_name='playerdailyshipstats',
            name='player',
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='daily_ship_stats', to='warships.player'),
        ),
    ]

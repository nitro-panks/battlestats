"""Drop three indexes nothing plans on — and deliberately NOT two others.

Step 3 of runbook-db-table-shape-remediation-2026-09-20.md. The H-series audit
proposed five removals from lifetime `idx_scan` counts (the cluster's counters
have never been reset). Before writing this, every reader of the three tables
was EXPLAINed on production, 20 queries in all, and that check changed the
answer: two of the five are load-bearing.

DROPPED HERE (~437 MB, returned to the OS — index files unlink):

  warships_battleevent_player_id_1f7bf48a            202 MB, 2,518 scans
      The ForeignKey's automatic index. Every scan is a bare `player_id = X`
      lookup. `battle_event_player_time_idx` (player, -detected_at) leads with
      the same column and serves it identically; the plan changes index, not
      shape.

  warships_playerdailyshipstats_season_id_69e0cf16   197 MB, 2,043 scans
      94.1% NULL. Its single reader is the ranked-season timeline, already
      scoped to one player and narrowed to ~230 rows by
      `dly_ship_player_date_idx`. The season index only ever appeared as an
      optional BitmapAnd arm on top of that. The runbook proposed a PARTIAL
      replacement; a plain drop is what the evidence supports, and it avoids a
      concurrent, non-atomic index build on a 20.9M-row table for no gain.

  explorer_eff_rank_idx                               38 MB, 0 scans
      Zero over the cluster's lifetime. Nothing orders or ranges on
      `efficiency_rank_percentile` in SQL.

NOT DROPPED, against the audit's recommendation:

  warships_playerdailyshipstats_ship_id_16c96227     198 MB, 225 scans — KEEP
      Carries the ship combat-profile population query
      (`_ship_population_brackets_30d`), the legacy per-ship avg-damage scan
      (`_ship_pop_avg_damage_raw`) and the trailing-days arm of the rollup
      path. All three are per-ship across every player, which no other index
      serves. The audit said "the rollup scans by date, not by ship": true of
      the rollup, and it had not looked at anything else.

  warships_battleevent_mode_983942c4                 174 MB, 106 scans — HOLD
      The ranked treemap (`compute_realm_top_ships`, mode='ranked') plans a
      Parallel Index Scan on it. This table's readers span 90 of its 105 days,
      so the date predicate barely narrows anything and for ~5% of rows `mode`
      is the selective one. Droppable once Step 6 moves that reader off this
      table; not before.

The lesson is the one `player_last_fetch_idx` taught the same day: a scan count
is frequency, not value. 225 scans that each spare a 36-second aggregation
outrank three million that each spare a millisecond.

Rollback is `db_index=True` / re-adding the Meta index and a new migration;
rebuilding any of the three is minutes of CPU, not a data risk.
"""

import django.db.models.deletion
from django.db import migrations, models


def _bound_lock_wait(apps, schema_editor):
    """Cap how long each DROP INDEX will wait for its lock.

    DROP INDEX needs ACCESS EXCLUSIVE on the table, and a blocked lock request
    queues every later statement behind it. These three tables are the floor's
    write path, the player timeline and the player page's summary read, so
    failing fast and retrying in a quieter minute beats holding that queue.
    Vendor-guarded like 0087: the sqlite harness has no `lock_timeout`.
    """
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("SET lock_timeout = '5s'")


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0088_truncate_playerachievementstat'),
    ]

    operations = [
        migrations.RunPython(_bound_lock_wait, _bound_lock_wait),
        migrations.RemoveIndex(
            model_name='playerexplorersummary',
            name='explorer_eff_rank_idx',
        ),
        migrations.AlterField(
            model_name='battleevent',
            name='player',
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='battle_events', to='warships.player'),
        ),
        migrations.AlterField(
            model_name='playerdailyshipstats',
            name='season_id',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]

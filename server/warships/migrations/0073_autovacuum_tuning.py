"""Per-table autovacuum tuning for the three highest-churn tables.

Tightens autovacuum scale-factors / thresholds on the churn tables called
out in agents/runbooks/runbook-db-growth-analysis-2026-06-15.md (step 1).
On the PG18 global defaults (scale_factor=0.2) a 3.3M-row table only
vacuums after ~650K dead tuples accumulate, which is why
warships_playerdailyshipstats carried ~12% dead. Tighter per-table
reloptions trigger smaller, more frequent vacuums to hold dead-tuple
hygiene and keep planner stats fresh.

reloptions are catalog-only metadata — the ALTER is a fast metadata lock
with no table rewrite and triggers no VACUUM. Postgres-only; a NO-OP on
sqlite (the test harness) so the migration applies cleanly everywhere.
"""

from django.db import migrations


# (table, scale_factor, threshold, analyze_scale_factor)
_TUNING = [
    ("warships_playerdailyshipstats", 0.02, 5000, 0.01),
    ("warships_player", 0.02, 5000, 0.01),
    ("warships_snapshot", 0.05, 5000, 0.02),
]


def forward(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table, vac_sf, vac_thresh, an_sf in _TUNING:
        schema_editor.execute(
            f"ALTER TABLE {table} SET ("
            f"autovacuum_vacuum_scale_factor = {vac_sf}, "
            f"autovacuum_vacuum_threshold = {vac_thresh}, "
            f"autovacuum_analyze_scale_factor = {an_sf}"
            f")"
        )


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table, _vac_sf, _vac_thresh, _an_sf in _TUNING:
        schema_editor.execute(
            f"ALTER TABLE {table} RESET ("
            f"autovacuum_vacuum_scale_factor, "
            f"autovacuum_vacuum_threshold, "
            f"autovacuum_analyze_scale_factor"
            f")"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0072_hotplayer_source_backfill'),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]

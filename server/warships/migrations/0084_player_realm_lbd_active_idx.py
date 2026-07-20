# Partial index for recency-ordered active-pool candidate scans (DB audit
# F9.1): snapshot engine, observation floor, and benchmark all filter
# realm + is_hidden=false + last_battle_date and order by -last_battle_date;
# warships_player had NO last_battle_date index at all, so each was a ~1M-row
# seq scan + sort (31-55 s mean on prod). Built CONCURRENTLY (atomic=False +
# RunSQL) so the hot-write table is never locked; the state_operations block
# keeps Django's model state in sync with the Meta declaration.

from django.db import migrations, models


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('warships', '0083_drop_snapshot_dead_columns'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name='player',
                    index=models.Index(
                        condition=models.Q(
                            ('last_battle_date__isnull', False),
                            ('is_hidden', False)),
                        fields=['realm', '-last_battle_date'],
                        name='player_realm_lbd_active_idx'),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'CREATE INDEX CONCURRENTLY IF NOT EXISTS '
                        'player_realm_lbd_active_idx ON warships_player '
                        '(realm, last_battle_date DESC) '
                        'WHERE last_battle_date IS NOT NULL '
                        'AND NOT is_hidden;'
                    ),
                    reverse_sql=(
                        'DROP INDEX CONCURRENTLY IF EXISTS '
                        'player_realm_lbd_active_idx;'
                    ),
                ),
            ],
        ),
    ]

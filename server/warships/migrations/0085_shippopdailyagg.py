# ShipPopDailyAgg — DB-audit lever F9.2 (runbook-db-table-audit-2026-07-19.md).
# Per-(realm, mode, ship, day) population aggregate over PlayerDailyShipStats,
# maintained by data.rollup_ship_pop_daily; replaces the nightly ~34s/realm
# full grouped PDSS scan behind compute_all_ship_pop_avg_damage with a sum
# over ~30 tiny rows per ship. Self-bounding: rows older than
# SHIP_POP_ROLLUP_RETENTION_DAYS (100) are pruned inside the rollup.
#
# NOTE: `makemigrations` also proposes a Remove/AddIndex churn on
# player_realm_lbd_active_idx — a pre-existing Q-condition-ordering drift
# between 0084's SeparateDatabaseAndState state and the Player Meta. That is
# deliberately NOT included here: regenerating that index non-CONCURRENTLY
# would lock the hot player table for nothing.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0084_player_realm_lbd_active_idx'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShipPopDailyAgg',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('realm', models.CharField(choices=[('na', 'NA'), ('eu', 'EU'), ('asia', 'ASIA')], default='na', max_length=4)),
                ('mode', models.CharField(choices=[('random', 'Random'), ('ranked', 'Ranked')], default='random', max_length=8)),
                ('ship_id', models.BigIntegerField()),
                ('date', models.DateField()),
                ('battles', models.BigIntegerField(default=0)),
                ('wins', models.BigIntegerField(default=0)),
                ('frags', models.BigIntegerField(default=0)),
                ('damage_sum', models.BigIntegerField(default=0)),
                ('xp', models.BigIntegerField(default=0)),
                ('main_shots', models.BigIntegerField(default=0)),
                ('main_hits', models.BigIntegerField(default=0)),
                ('secondary_shots', models.BigIntegerField(default=0)),
                ('secondary_hits', models.BigIntegerField(default=0)),
                ('torpedo_shots', models.BigIntegerField(default=0)),
                ('torpedo_hits', models.BigIntegerField(default=0)),
            ],
        ),
        migrations.AddIndex(
            model_name='shippopdailyagg',
            index=models.Index(fields=['realm', 'date'], name='shippop_realm_date_idx'),
        ),
        migrations.AddConstraint(
            model_name='shippopdailyagg',
            constraint=models.UniqueConstraint(fields=('realm', 'mode', 'ship_id', 'date'), name='unique_ship_pop_daily_agg'),
        ),
    ]

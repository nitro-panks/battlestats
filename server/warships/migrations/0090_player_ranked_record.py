"""Materialise the ranked record (total battles, win rate) on Player.

Derived from ranked_json at every write; backfilled once by
`backfill_ranked_record`. Reason and measurements:
runbook-ranked-correlation-materialized-record-2026-09-23.md.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0089_drop_three_unused_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='player',
            name='ranked_total_battles',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='player',
            name='ranked_win_rate',
            field=models.FloatField(blank=True, null=True),
        ),
    ]

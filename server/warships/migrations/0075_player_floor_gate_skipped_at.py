# Gate-skip cooldown for the observation floor (default-off). Nullable add →
# metadata-only on PostgreSQL.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0074_drop_period_rollup_tables'),
    ]

    operations = [
        migrations.AddField(
            model_name='player',
            name='floor_gate_skipped_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

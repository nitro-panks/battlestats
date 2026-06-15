from django.db import migrations, models


class Migration(migrations.Migration):
    """Add the 'backfill' choice to HotPlayer.source (validation-only; no DB change)."""

    dependencies = [
        ('warships', '0071_delete_shipaward'),
    ]

    operations = [
        migrations.AlterField(
            model_name='hotplayer',
            name='source',
            field=models.CharField(
                choices=[
                    ('engagement', 'Engagement'),
                    ('pinned', 'Pinned'),
                    ('backfill', 'Backfill'),
                ],
                default='engagement',
                max_length=16,
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0024_playerexplorersummary_player_score_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='player',
            name='efficiency_json',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='player',
            name='efficiency_updated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

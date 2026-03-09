from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0019_add_player_name_trigram_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='playerexplorersummary',
            name='kill_ratio',
            field=models.FloatField(blank=True, null=True),
        ),
    ]

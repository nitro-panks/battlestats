from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0032_player_last_fetch_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='clan',
            name='cached_total_wins',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='clan',
            name='cached_total_battles',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='clan',
            name='cached_active_member_count',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='clan',
            name='cached_clan_wr',
            field=models.FloatField(blank=True, null=True),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0038_add_realm_to_entity_visits'),
    ]

    operations = [
        migrations.CreateModel(
            name='LandingPlayerBestSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True,
                 primary_key=True, serialize=False, verbose_name='ID')),
                ('realm', models.CharField(choices=[
                 ('na', 'NA'), ('eu', 'EU')], db_index=True, default='na', max_length=4)),
                ('sort', models.CharField(db_index=True, max_length=16)),
                ('payload_json', models.JSONField(blank=True, default=list)),
                ('generated_at', models.DateTimeField(auto_now=True)),
            ],
            options={},
        ),
        migrations.AddConstraint(
            model_name='landingplayerbestsnapshot',
            constraint=models.UniqueConstraint(fields=(
                'realm', 'sort'), name='unique_landing_player_best_snapshot_per_realm_sort'),
        ),
    ]

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0047_alter_player_enrichment_status'),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE INDEX IF NOT EXISTS clan_name_trgm_idx ON warships_clan USING gin (name gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS clan_name_trgm_idx;",
        ),
        migrations.RunSQL(
            sql="CREATE INDEX IF NOT EXISTS clan_tag_trgm_idx ON warships_clan USING gin (tag gin_trgm_ops);",
            reverse_sql="DROP INDEX IF EXISTS clan_tag_trgm_idx;",
        ),
    ]

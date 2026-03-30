"""Drop unused indexes (341 MB), create distribution materialized view, tune statistics targets."""

from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0033_clan_cached_aggregations'),
    ]

    operations = [
        # ── Drop unused Player indexes (31 MB) ──────────────────────────
        migrations.RemoveIndex(
            model_name='player',
            name='player_hidden_battle_idx',
        ),
        migrations.RemoveIndex(
            model_name='player',
            name='player_clan_battle_idx',
        ),
        migrations.RemoveIndex(
            model_name='player',
            name='player_last_fetch_idx',
        ),

        # ── Drop unused PlayerExplorerSummary indexes (51 MB) ───────────
        migrations.RemoveIndex(
            model_name='playerexplorersummary',
            name='explorer_score_idx',
        ),
        migrations.RemoveIndex(
            model_name='playerexplorersummary',
            name='explorer_battles29_idx',
        ),
        migrations.RemoveIndex(
            model_name='playerexplorersummary',
            name='explorer_active29_idx',
        ),
        migrations.RemoveIndex(
            model_name='playerexplorersummary',
            name='explorer_ships_idx',
        ),
        migrations.RemoveIndex(
            model_name='playerexplorersummary',
            name='explorer_ranked_idx',
        ),

        # ── Drop unused PlayerAchievementStat indexes (131 MB) ──────────
        migrations.RemoveIndex(
            model_name='playerachievementstat',
            name='player_ach_slug_idx',
        ),
        migrations.RemoveIndex(
            model_name='playerachievementstat',
            name='achievement_slug_idx',
        ),

        # ── Create materialized view for distribution queries ───────────
        migrations.RunSQL(
            sql="""
                CREATE MATERIALIZED VIEW IF NOT EXISTS mv_player_distribution_stats AS
                SELECT
                    id,
                    pvp_ratio,
                    pvp_survival_rate,
                    pvp_battles,
                    is_hidden
                FROM warships_player
                WHERE is_hidden = FALSE
                  AND pvp_battles >= 100;

                CREATE UNIQUE INDEX IF NOT EXISTS mv_player_dist_id_idx
                    ON mv_player_distribution_stats (id);
                CREATE INDEX IF NOT EXISTS mv_player_dist_ratio_idx
                    ON mv_player_distribution_stats (pvp_ratio)
                    WHERE pvp_ratio IS NOT NULL;
                CREATE INDEX IF NOT EXISTS mv_player_dist_survival_idx
                    ON mv_player_distribution_stats (pvp_survival_rate)
                    WHERE pvp_survival_rate IS NOT NULL;
                CREATE INDEX IF NOT EXISTS mv_player_dist_battles_idx
                    ON mv_player_distribution_stats (pvp_battles);
            """,
            reverse_sql="""
                DROP MATERIALIZED VIEW IF EXISTS mv_player_distribution_stats;
            """,
        ),

        # ── Register unmanaged model for the materialized view ──────────
        migrations.CreateModel(
            name='MvPlayerDistributionStats',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pvp_ratio', models.FloatField(null=True)),
                ('pvp_survival_rate', models.FloatField(null=True)),
                ('pvp_battles', models.IntegerField()),
                ('is_hidden', models.BooleanField()),
            ],
            options={
                'db_table': 'mv_player_distribution_stats',
                'managed': False,
            },
        ),

        # ── Tune statistics targets for skewed columns ──────────────────
        migrations.RunSQL(
            sql="""
                ALTER TABLE warships_player ALTER COLUMN pvp_ratio SET STATISTICS 200;
                ALTER TABLE warships_player ALTER COLUMN pvp_battles SET STATISTICS 200;
                ALTER TABLE warships_player ALTER COLUMN pvp_survival_rate SET STATISTICS 200;
                ALTER TABLE warships_playerexplorersummary ALTER COLUMN player_score SET STATISTICS 200;
                ANALYZE warships_player (pvp_ratio, pvp_battles, pvp_survival_rate);
                ANALYZE warships_playerexplorersummary (player_score);
            """,
            reverse_sql="""
                ALTER TABLE warships_player ALTER COLUMN pvp_ratio SET STATISTICS -1;
                ALTER TABLE warships_player ALTER COLUMN pvp_battles SET STATISTICS -1;
                ALTER TABLE warships_player ALTER COLUMN pvp_survival_rate SET STATISTICS -1;
                ALTER TABLE warships_playerexplorersummary ALTER COLUMN player_score SET STATISTICS -1;
            """,
        ),
    ]

from django.db import migrations


MIN_PVP_BATTLES = 500
MAX_INACTIVE_DAYS = 365


def backfill_enrichment_status(apps, schema_editor):
    Player = apps.get_model('warships', 'Player')

    # Real enrichments first (most specific) — anything with non-empty battles_json.
    Player.objects.filter(
        battles_json__isnull=False,
    ).exclude(battles_json=[]).update(enrichment_status='enriched')

    # Genuinely empty (WG confirmed no ships).
    Player.objects.filter(battles_json=[]).update(enrichment_status='empty')

    # Hidden takes precedence over the rest of the skip reasons.
    Player.objects.filter(
        is_hidden=True,
        enrichment_status='pending',
    ).update(enrichment_status='skipped_hidden')

    # Low-battle accounts (below the eligibility floor).
    Player.objects.filter(
        enrichment_status='pending',
        pvp_battles__lt=MIN_PVP_BATTLES,
    ).update(enrichment_status='skipped_low_battles')

    # Long-dormant accounts (no battles in > 1 year).
    Player.objects.filter(
        enrichment_status='pending',
        days_since_last_battle__gt=MAX_INACTIVE_DAYS,
    ).update(enrichment_status='skipped_inactive')


def reverse_backfill(apps, schema_editor):
    Player = apps.get_model('warships', 'Player')
    Player.objects.all().update(enrichment_status='pending')


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0045_player_enrichment_status'),
    ]

    operations = [
        migrations.RunPython(
            backfill_enrichment_status,
            reverse_backfill,
            elidable=True,
        ),
    ]

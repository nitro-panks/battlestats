from django.core.management.base import BaseCommand

from warships.data import _extract_tier_type_battle_rows, warm_player_correlations
from warships.models import DEFAULT_REALM, Player, VALID_REALMS


class Command(BaseCommand):
    help = "Report per-realm readiness for player profile charts backed by battles_json."

    def add_arguments(self, parser):
        parser.add_argument(
            "--realm",
            choices=sorted(VALID_REALMS),
            help="Scope the audit to one realm. Defaults to all realms.",
        )
        parser.add_argument(
            "--warm-correlations",
            action="store_true",
            help="Force-warm player correlations and include tracked_population output.",
        )

    def handle(self, *args, **options):
        requested_realm = options.get("realm")
        warm_correlations = bool(options.get("warm_correlations"))
        realms = [requested_realm] if requested_realm else sorted(VALID_REALMS)

        for realm in realms:
            visible_qs = Player.objects.filter(realm=realm, is_hidden=False)
            total_players = Player.objects.filter(realm=realm).count()
            visible_players = visible_qs.count()
            visible_with_battles = visible_qs.filter(
                battles_json__isnull=False).count()
            visible_missing_battles = visible_qs.filter(
                pvp_battles__gt=0,
                battles_json__isnull=True,
            ).count()

            visible_with_tier_type_rows = 0
            for battles_json in visible_qs.filter(
                battles_json__isnull=False,
            ).values_list("battles_json", flat=True).iterator(chunk_size=1000):
                if _extract_tier_type_battle_rows(battles_json):
                    visible_with_tier_type_rows += 1

            battles_pct = (visible_with_battles /
                           visible_players * 100.0) if visible_players else 0.0
            tier_type_pct = (visible_with_tier_type_rows /
                             visible_players * 100.0) if visible_players else 0.0

            self.stdout.write(self.style.SUCCESS(
                f"\n=== {realm.upper()} Profile Chart Readiness ==="))
            self.stdout.write(
                f"  Total players:                 {total_players:,}")
            self.stdout.write(
                f"  Visible players:               {visible_players:,}")
            self.stdout.write(
                f"  Visible with battles_json:     {visible_with_battles:,} ({battles_pct:.2f}%)")
            self.stdout.write(
                f"  Visible with tier-type rows:   {visible_with_tier_type_rows:,} ({tier_type_pct:.2f}%)")
            self.stdout.write(
                f"  Visible missing battles_json:  {visible_missing_battles:,}")

            if warm_correlations:
                correlation_result = warm_player_correlations(realm=realm)
                self.stdout.write(
                    f"  Correlations:                  {correlation_result}")

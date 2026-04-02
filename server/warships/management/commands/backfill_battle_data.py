from django.core.management.base import BaseCommand
from django.db.models import F

from warships.data import update_battle_data
from warships.models import DEFAULT_REALM, Player, VALID_REALMS
from warships.tasks import update_battle_data_task


DEFAULT_LIMIT = 250
DEFAULT_PREVIEW = 10


class Command(BaseCommand):
    help = "Backfill missing battles_json for players in bounded, priority-ordered batches."

    def add_arguments(self, parser):
        parser.add_argument(
            "--realm",
            choices=sorted(VALID_REALMS),
            default=DEFAULT_REALM,
            help="Realm to backfill (default: na).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help="Max players to process in this run.",
        )
        parser.add_argument(
            "--dispatch",
            choices=["queue", "sync"],
            default="queue",
            help="Dispatch mode: enqueue Celery tasks or run synchronously.",
        )
        parser.add_argument(
            "--min-pvp-battles",
            type=int,
            default=1,
            help="Only target players at or above this PvP battle count.",
        )
        parser.add_argument(
            "--preview",
            type=int,
            default=DEFAULT_PREVIEW,
            help="Number of candidate rows to print before processing.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report candidate counts and ordering without dispatching work.",
        )

    def handle(self, *args, **options):
        realm = options["realm"] or DEFAULT_REALM
        limit = max(0, int(options["limit"]))
        dispatch = options["dispatch"]
        min_pvp_battles = max(0, int(options["min_pvp_battles"]))
        preview = max(0, int(options["preview"]))
        dry_run = bool(options["dry_run"])

        queryset = (
            Player.objects.filter(
                realm=realm,
                is_hidden=False,
                pvp_battles__gte=min_pvp_battles,
                battles_json__isnull=True,
            )
            .exclude(name="")
            .select_related("explorer_summary")
            .order_by(
                F("last_lookup").desc(nulls_last=True),
                F("explorer_summary__player_score").desc(nulls_last=True),
                F("pvp_battles").desc(nulls_last=True),
                F("last_fetch").desc(nulls_last=True),
                "name",
            )
        )

        total_candidates = queryset.count()
        selected_players = list(queryset[:limit]) if limit else list(queryset)

        self.stdout.write(self.style.SUCCESS(
            f"\n=== {realm.upper()} Battle Data Backfill ==="))
        self.stdout.write(f"  Total candidates:   {total_candidates:,}")
        self.stdout.write(f"  Selected this run:  {len(selected_players):,}")
        self.stdout.write(f"  Dispatch mode:      {dispatch}")
        self.stdout.write(f"  Min PvP battles:    {min_pvp_battles:,}")

        if preview:
            self.stdout.write("  Preview:")
            for player in selected_players[:preview]:
                player_score = None
                if hasattr(player, "explorer_summary") and player.explorer_summary:
                    player_score = player.explorer_summary.player_score
                self.stdout.write(
                    f"    - {player.player_id} {player.name} "
                    f"lookup={player.last_lookup or 'never'} score={player_score} pvp_battles={player.pvp_battles}"
                )

        if dry_run or not selected_players:
            return

        dispatched = 0
        failed = 0
        for player in selected_players:
            try:
                if dispatch == "sync":
                    update_battle_data(player.player_id, realm=realm)
                else:
                    update_battle_data_task.delay(
                        player.player_id, realm=realm)
                dispatched += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(
                    f"Failed to dispatch player_id={player.player_id} realm={realm}: {exc}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Completed backfill dispatch. dispatched={dispatched} failed={failed} realm={realm}"
            )
        )

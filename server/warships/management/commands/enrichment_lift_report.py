"""Quantify the *broader* ranking lift from an enrichment backfill.

While their ``battles_json`` was empty/NULL, recovered players contributed
nothing to any ranked surface (high-tier win-rate = 0 → excluded from every
landing Best board and from the efficiency ranking). This command measures, for
the players enriched since a cutoff, how many now clear the gates they were
previously excluded from:

* **High-tier rankable** — ≥50 T5–T10 PvP battles (the landing board floor).
* **Landing-Best board-eligible** — also ``pvp_battles > LANDING_PLAYER_BEST_MIN_PVP_BATTLES``
  and active within 180 days (the actual candidate gate).
* **Clear the live top-25 bar** — high-tier WR ≥ the board's current entry bar
  (context for why the headline top-25 did/didn't move).
* **Efficiency ranking** — now carry a non-null ``efficiency_rank_percentile``.

Read-only. Pairs with ``retry_empty_enrichments`` (which re-queues the empty
false-negatives). See ``agents/work-items/player-enrichment-map-2026-06-08.md`` §12.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from warships.data import calculate_tier_filtered_pvp_record
from warships.landing import LANDING_PLAYER_BEST_MIN_PVP_BATTLES
from warships.models import Player, PlayerExplorerSummary

HIGH_TIER_MIN_BATTLES = 50   # landing board high-tier floor
ACTIVE_DAYS = 180            # landing board active window
MINIMUM_TIER = 5             # T5-T10 = "high tier"

# (label, low_inclusive, high_exclusive) over high-tier win rate %
HT_WR_BANDS = [
    (">=73", 73.0, None),
    ("70-73", 70.0, 73.0),
    ("65-70", 65.0, 70.0),
    ("60-65", 60.0, 65.0),
    ("<60", None, 60.0),
]


class Command(BaseCommand):
    help = "Quantify ranking lift from an enrichment backfill (players enriched since --since)."

    def add_arguments(self, parser):
        parser.add_argument('--since', required=True,
                            help='ISO datetime (naive, UTC). Cohort = enriched players with '
                                 'battles_updated_at >= this.')
        parser.add_argument('--realm', default=None, help='Limit to one realm (na/eu/asia).')
        parser.add_argument('--min-battles', type=int, default=500,
                            help='Cohort floor on pvp_battles (default 500).')
        parser.add_argument('--min-wr', type=float, default=48.0,
                            help='Cohort floor on career pvp_ratio (default 48.0).')
        parser.add_argument('--board-bar', type=float, default=73.0,
                            help='High-tier WR entry bar of the live Best-by-WR top-25 (for context).')

    def _cohort(self, opts):
        qs = Player.objects.filter(
            enrichment_status=Player.ENRICHMENT_ENRICHED,
            battles_updated_at__gte=opts['_since'],
            pvp_battles__gte=opts['min_battles'],
            pvp_ratio__gte=opts['min_wr'],
            is_hidden=False,
        )
        if opts['realm']:
            qs = qs.filter(realm=opts['realm'])
        return qs

    def handle(self, *args, **opts):
        try:
            opts['_since'] = datetime.datetime.fromisoformat(opts['since'])
        except ValueError as exc:
            raise CommandError(f"--since is not a valid ISO datetime: {exc}")

        cohort = self._cohort(opts)
        board_floor = LANDING_PLAYER_BEST_MIN_PVP_BATTLES
        board_bar = opts['board_bar']

        total = ht_rankable = board_eligible = clear_bar = 0
        bands = {label: 0 for label, *_ in HT_WR_BANDS}

        for p in cohort.values(
            'pvp_battles', 'days_since_last_battle', 'battles_json',
        ).iterator(chunk_size=500):
            total += 1
            ht_battles, ht_wr = calculate_tier_filtered_pvp_record(
                p['battles_json'], minimum_tier=MINIMUM_TIER)
            if ht_battles < HIGH_TIER_MIN_BATTLES or ht_wr is None:
                continue
            ht_rankable += 1
            for label, lo, hi in HT_WR_BANDS:
                if (lo is None or ht_wr >= lo) and (hi is None or ht_wr < hi):
                    bands[label] += 1
                    break
            if p['pvp_battles'] > board_floor and p['days_since_last_battle'] <= ACTIVE_DAYS:
                board_eligible += 1
                if ht_wr >= board_bar:
                    clear_bar += 1

        eff = PlayerExplorerSummary.objects.filter(player__in=cohort)
        eff_agg = eff.aggregate(
            with_pct=Count('id', filter=Q(efficiency_rank_percentile__isnull=False)),
            p99=Count('id', filter=Q(efficiency_rank_percentile__gte=99.0)),
            p95=Count('id', filter=Q(efficiency_rank_percentile__gte=95.0)),
            p90=Count('id', filter=Q(efficiency_rank_percentile__gte=90.0)),
        )

        out = self.stdout.write
        out("=== enrichment_lift_report ===")
        out(f"Cohort: enriched, battles_updated_at >= {opts['_since']}, "
            f"pvp_battles>={opts['min_battles']}, WR>={opts['min_wr']}, visible"
            + (f", realm={opts['realm']}" if opts['realm'] else ""))
        out("")
        out(f"Recovered profiles (now complete): {total}")
        out(f"  High-tier rankable (>= {HIGH_TIER_MIN_BATTLES} T{MINIMUM_TIER}-10 battles): {ht_rankable}")
        out(f"  Landing-Best board-eligible (> {board_floor} battles + ht>= {HIGH_TIER_MIN_BATTLES} "
            f"+ active<= {ACTIVE_DAYS}d): {board_eligible}")
        out(f"    ...clearing the live top-25 bar (high-tier WR >= {board_bar}%): {clear_bar}")
        out("  High-tier WR distribution (rankable cohort):")
        for label, *_ in HT_WR_BANDS:
            out(f"    {label:>6}: {bands[label]}")
        out(f"  Efficiency ranking: {eff_agg['with_pct']} now carry a percentile "
            f"(>=99th: {eff_agg['p99']}, >=95th: {eff_agg['p95']}, >=90th: {eff_agg['p90']})")

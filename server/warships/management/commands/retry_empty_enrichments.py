"""One-shot: re-queue players parked as ``empty`` that are almost certainly
false negatives.

An ``empty`` row (``battles_json == []``) is recorded when WG ``ships/stats``
returns no ships — but for a high-battle account that is overwhelmingly a
*transient* failure or a profile that was **private at fetch time**, not a
player who genuinely owns no ships. Those rows are then parked permanently:
``status='empty'`` is excluded from ``_candidates()``, and a ``reclassify`` pass
keeps them ``empty`` (its rule is ``battles_json == [] -> empty``). So nothing
ever retries them, even after the player goes public.

This command sets ``enrichment_status -> pending`` and ``battles_json -> NULL``
on the false-negative set, so they re-enter the ``_candidates()`` pool and the
enrichment crawler re-fetches them. It does **not** fetch anything itself — the
self-chaining crawler (or a manual ``enrich_player_data`` run) drains the
re-queued backlog at its own rate.

Default is a **DRY RUN** that sizes the backlog across WR bands. Pass ``--apply``
to write. Thresholds read the same env vars as the crawler so the re-queue set
matches what ``_candidates()`` will actually pick up.

See ``agents/work-items/player-enrichment-map-2026-06-08.md`` §12.
"""
import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from warships.models import Player

MIN_PVP_BATTLES = int(os.getenv("ENRICH_MIN_PVP_BATTLES", "500"))
MIN_WR = float(os.getenv("ENRICH_MIN_WR", "48.0"))
MAX_INACTIVE_DAYS = int(os.getenv("ENRICH_MAX_INACTIVE_DAYS", "365"))

# (label, low_inclusive, high_exclusive)
WR_BANDS = [
    (">=60", 60.0, None),
    ("55-60", 55.0, 60.0),
    ("50-55", 50.0, 55.0),
    ("48-50", 48.0, 50.0),
    ("45-48", 45.0, 48.0),
    ("<45", None, 45.0),
]


class Command(BaseCommand):
    help = "Re-queue `empty` enrichment false-negatives (battles_json=[]) for re-fetch."

    def add_arguments(self, parser):
        parser.add_argument('--realm', default=None,
                            help='Limit to a single realm (na/eu/asia). Default: all.')
        parser.add_argument('--min-battles', type=int, default=MIN_PVP_BATTLES,
                            help=f'Only empties with pvp_battles >= this (default {MIN_PVP_BATTLES}).')
        parser.add_argument('--min-wr', type=float, default=MIN_WR,
                            help=f'Re-queue only empties with pvp_ratio >= this (default ENRICH_MIN_WR={MIN_WR}). '
                                 'Pass 0 to re-queue all WR bands.')
        parser.add_argument('--max-inactive-days', type=int, default=MAX_INACTIVE_DAYS)
        parser.add_argument('--retry-after-days', type=int, default=0,
                            help='Convergence guard for scheduled use: only re-queue empties whose '
                                 'last enrichment attempt (battles_updated_at) is older than this many '
                                 'days, or has never been attempted. Default 0 = no cooldown (one-shot '
                                 'behavior). A genuinely-empty row is then re-fetched at most once per '
                                 'N days instead of every run, bounding WG-budget burn.')
        parser.add_argument('--include-hidden', action='store_true',
                            help='Also re-queue currently-hidden empties (they will likely re-empty).')
        parser.add_argument('--include-inactive', action='store_true',
                            help='Ignore the active <= max-inactive-days gate.')
        parser.add_argument('--apply', action='store_true',
                            help='Actually write. Default is a dry run.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Explicit dry run (this is the default; forces dry even with --apply).')

    def handle(self, *args, **opts):
        realm = opts['realm']
        min_battles = opts['min_battles']
        min_wr = opts['min_wr']
        max_inactive = opts['max_inactive_days']
        include_hidden = opts['include_hidden']
        include_inactive = opts['include_inactive']
        retry_after_days = opts['retry_after_days']
        apply = opts['apply'] and not opts['dry_run']

        scope = Player.objects.filter(
            enrichment_status=Player.ENRICHMENT_EMPTY,
            pvp_battles__gte=min_battles,
        )
        if realm:
            scope = scope.filter(realm=realm)

        # Gates that define the re-queue set (matched to _candidates()).
        requeue_q = Q()
        gate_labels = []
        if not include_hidden:
            requeue_q &= Q(is_hidden=False)
            gate_labels.append("visible")
        if not include_inactive:
            requeue_q &= Q(days_since_last_battle__lte=max_inactive)
            gate_labels.append(f"active<={max_inactive}d")
        if min_wr > 0:
            requeue_q &= Q(pvp_ratio__gte=min_wr)
            gate_labels.append(f"WR>={min_wr}")
        if retry_after_days > 0:
            # Convergence guard: a row re-emptied by enrichment has its
            # battles_updated_at bumped to that attempt time (see
            # enrich_player_data.py:_mark_empty). Re-queue only rows whose last
            # attempt is older than the cooldown (or never attempted), so a
            # persistently-empty account is re-fetched at most once per cooldown
            # instead of every scheduled run.
            cutoff = timezone.now() - timedelta(days=retry_after_days)
            requeue_q &= (Q(battles_updated_at__lte=cutoff)
                          | Q(battles_updated_at__isnull=True))
            gate_labels.append(f"last-attempt>{retry_after_days}d-ago")

        # One pass for all sizing.
        band_aggs = {}
        for label, lo, hi in WR_BANDS:
            q = Q()
            if lo is not None:
                q &= Q(pvp_ratio__gte=lo)
            if hi is not None:
                q &= Q(pvp_ratio__lt=hi)
            band_aggs[f"band_{label}"] = Count('id', filter=q)

        agg = scope.aggregate(
            total=Count('id'),
            visible=Count('id', filter=Q(is_hidden=False)),
            active=Count('id', filter=Q(days_since_last_battle__lte=max_inactive)),
            wr_null=Count('id', filter=Q(pvp_ratio__isnull=True)),
            requeue=Count('id', filter=requeue_q),
            **band_aggs,
        )
        by_realm = list(scope.values('realm').annotate(n=Count('id')).order_by('realm'))

        mode = 'APPLY' if apply else 'DRY RUN'
        out = self.stdout.write
        out(f"=== retry_empty_enrichments — {mode} ===")
        out(f"Sizing scope: enrichment_status='empty' AND pvp_battles>={min_battles}"
            + (f" AND realm={realm}" if realm else ""))
        out("")
        out(f"Total empties in scope: {agg['total']}")
        out("  By realm: " + ", ".join(f"{r['realm']}={r['n']}" for r in by_realm))
        out(f"  Visible (is_hidden=False): {agg['visible']}   Hidden: {agg['total'] - agg['visible']}")
        out(f"  Active (<= {max_inactive}d): {agg['active']}")
        out("  WR bands (full scope):")
        for label, *_ in WR_BANDS:
            out(f"    {label:>6}: {agg[f'band_{label}']}")
        out(f"     NULL: {agg['wr_null']}")
        out("")
        out(f"Re-queue set ({', '.join(gate_labels) or 'no gates'}): {agg['requeue']}")

        if not apply:
            out("\nDRY RUN — no rows changed. Re-run with --apply to set "
                "enrichment_status=pending, battles_json=NULL on the re-queue set.")
            return

        updated = scope.filter(requeue_q).update(
            enrichment_status=Player.ENRICHMENT_PENDING,
            battles_json=None,
        )
        out(f"\nAPPLIED — re-queued {updated} rows (status=pending, battles_json=NULL). "
            "The enrichment crawler will re-fetch them on its next run.")

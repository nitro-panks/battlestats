"""Snapshot retention: downsample daily rows older than the retention window.

``warships_snapshot`` is the one genuinely unbounded growth vector with no
retention policy (~190K rows/day, see the 2026-06-21 data-lifecycle
assessment). Every product read path consumes only the **last ~28-29 days** of
snapshots (``data.update_snapshot_data`` recomputes a 28-day interval window;
``data.update_activity_data`` materializes a 29-day ``activity_json``). Rows
older than that are pure history/audit — no chart reads them.

Policy (product decision 2026-06-21): keep **full daily granularity for
``retention_days`` (default 90)**, then **downsample older rows to one row per
player per ISO-week** — the latest (max-``date``) row in each week is kept,
the rest are deleted. The kept row's cumulative ``battles``/``wins`` preserve
the long-range trajectory at week granularity for any future long-window chart;
the intra-week rows (unread beyond 29 days) are dropped.

Notes:
- ``interval_battles`` / ``interval_wins`` on kept >90d rows are left as-is
  (stale week-over-week), because no read path consumes intervals beyond the
  29-day window — recomputing them would be dead work.
- Account-merge (``player_records``) iterates a player's snapshots ordered by
  date; downsampled rows keep distinct dates, so the merge is unaffected.
- Fully portable: uses ORM ``ExtractIsoYear``/``ExtractWeek`` annotations
  (Postgres + sqlite), streams candidate ids with ``.iterator()`` so memory
  stays flat on a large first-run backlog, and deletes by primary key in
  batches. ``dry_run`` writes nothing.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from django.db.models import Max
from django.db.models.functions import ExtractIsoYear, ExtractWeek
from django.utils import timezone

from warships.models import Snapshot

RETENTION_DAYS_DEFAULT = 90
BATCH_SIZE_DEFAULT = 5000


def _today() -> date:
    return timezone.now().date()


def downsample_snapshots(
    retention_days: int = RETENTION_DAYS_DEFAULT,
    batch_size: int = BATCH_SIZE_DEFAULT,
    max_rows: int = 0,
    dry_run: bool = False,
    sleep_between_batches: float = 0.0,
) -> dict:
    """Collapse >retention_days Snapshot rows to one-per-player-per-ISO-week.

    Returns a summary dict with the cutoff, candidate/keeper/deletable counts,
    and (when not dry-run) the number of rows actually deleted.
    """
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if max_rows < 0:
        raise ValueError("max_rows must be >= 0")

    cutoff = _today() - timedelta(days=retention_days)

    old = Snapshot.objects.filter(date__lt=cutoff)

    # The weekly keeper for each (player, ISO-year, ISO-week) group is the row
    # with the latest date. player+date is unique, so the (player_id, keep_date)
    # pair set uniquely identifies the keepers — a small, bounded set
    # (#players x #weeks beyond the window).
    keeper_rows = (
        old.annotate(
            iso_year=ExtractIsoYear("date"),
            iso_week=ExtractWeek("date"),
        )
        .values("player_id", "iso_year", "iso_week")
        .annotate(keep_date=Max("date"))
    )
    keep_pairs = {(row["player_id"], row["keep_date"]) for row in keeper_rows}

    # Stream candidate (id, player_id, date) and select the non-keepers to drop.
    deletable_ids: list[int] = []
    candidates = 0
    for pk, player_id, row_date in (
        old.values_list("id", "player_id", "date").iterator(chunk_size=10000)
    ):
        candidates += 1
        if (player_id, row_date) not in keep_pairs:
            deletable_ids.append(pk)

    summary = {
        "cutoff": cutoff.isoformat(),
        "retention_days": retention_days,
        "candidates": candidates,
        "keepers": len(keep_pairs),
        "deletable": len(deletable_ids),
        "dry_run": dry_run,
        "deleted": 0,
        "batches": 0,
    }

    if dry_run or not deletable_ids:
        return summary

    if max_rows:
        deletable_ids = deletable_ids[:max_rows]

    deleted = 0
    batches = 0
    for start in range(0, len(deletable_ids), batch_size):
        chunk = deletable_ids[start:start + batch_size]
        n, _ = Snapshot.objects.filter(id__in=chunk).delete()
        deleted += n
        batches += 1
        if sleep_between_batches:
            time.sleep(sleep_between_batches)

    summary["deleted"] = deleted
    summary["batches"] = batches
    return summary

"""Storage tuning for warships_playerexplorersummary (the bloat hotspot).

The 2026-06-21 data-lifecycle assessment found this table at 1527 MB heap
holding only ~120 MB of live tuples — ``pgstattuple`` reported **90.7% free
space** (~1.38 GB reclaimable) plus ~700 MB of index bloat. Cause: the nightly
efficiency-rank refresh rewrites ~174K rows/day, and the changed columns
(``efficiency_rank_percentile``, ``player_score``) are **indexed**, so the
updates can never be HOT — every update writes a new heap tuple + new index
entries, and the file's high-water mark never shrinks under plain autovacuum.

This migration does two metadata-only things (no table rewrite, fast lock):

1. **Autovacuum tuning** — the table was omitted from 0073. Tighten its
   reloptions so dead tuples are reclaimed to the free-space map promptly,
   capping the steady-state bloat rate.
2. **fillfactor = 90** — leave per-page headroom so a non-HOT update's new
   tuple can more often land in the same page, reducing the rate of fresh-page
   allocation (heap growth). NOTE: fillfactor does **not** enable HOT here —
   HOT requires *no indexed column to change*, which this rewrite violates — so
   it only slows re-bloat, it does not stop it. fillfactor applies to pages
   written *after* this migration; the existing ~1.4 GB of bloat is reclaimed
   by a separate, gated one-time ``pg_repack``/windowed ``VACUUM FULL`` op (see
   runbook-data-lifecycle-architecture-2026-06-21.md, gated next-steps).

reloptions + fillfactor are catalog-only metadata — the ALTERs are fast
metadata locks with no table rewrite and trigger no VACUUM. Postgres-only; a
NO-OP on sqlite (the test harness) so the migration applies cleanly everywhere.
"""

from django.db import migrations


_TABLE = "warships_playerexplorersummary"
# (scale_factor, threshold, analyze_scale_factor) — same shape as 0073's
# Snapshot tuning; this table's churn (~174K/day on ~723K rows) is comparable.
_VAC_SF, _VAC_THRESH, _AN_SF = 0.05, 5000, 0.02
_FILLFACTOR = 90


def forward(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"ALTER TABLE {_TABLE} SET ("
        f"autovacuum_vacuum_scale_factor = {_VAC_SF}, "
        f"autovacuum_vacuum_threshold = {_VAC_THRESH}, "
        f"autovacuum_analyze_scale_factor = {_AN_SF}, "
        f"fillfactor = {_FILLFACTOR}"
        f")"
    )


def reverse(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"ALTER TABLE {_TABLE} RESET ("
        f"autovacuum_vacuum_scale_factor, "
        f"autovacuum_vacuum_threshold, "
        f"autovacuum_analyze_scale_factor, "
        f"fillfactor"
        f")"
    )


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0075_player_floor_gate_skipped_at'),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]

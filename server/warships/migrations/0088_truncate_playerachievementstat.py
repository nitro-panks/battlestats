"""Empty `warships_playerachievementstat`: 1,434 MB of a second copy.

Operator-approved 2026-09-20, as Step 5's second half. Migration 0087's
sibling change (v5.11.2) stopped writing this table; this returns what it had
already accumulated.

WHAT IT IS. One row per (player, achievement), derived by
`normalize_player_achievement_rows()` from the raw Wargaming payload that is
still stored in `Player.achievements_json`. It has no user-facing reader: no
serializer field, no view, and zero occurrences of "achievement" under
`client/`.

WHY IT IS SAFE. Measured on production 2026-09-20: of 3,000 sampled players
holding rows here, **3,000 still carry the source payload** — zero exceptions.
Nothing in this table exists only in this table. The two remaining readers
degrade correctly against an empty table: `_merge_achievement_rows`
(`player_records.py`) iterates two empty querysets and no-ops, and
`purge_deleted_accounts.py` reports `achievement_count: 0`, which is
informational. No foreign key points at this table, so TRUNCATE neither fails
nor cascades.

WHY TRUNCATE AND NOT DELETE. TRUNCATE unlinks the files, returning all 1,434 MB
to the operating system. A DELETE of 5.36M rows would produce a large WAL burst
and leave the space as reusable pages *inside* the table — no smaller volume,
which is the distinction that governs every lever in the capacity runbook. The
database sits at 78.57% of a volume that will never autoscale, so an OS-level
return is worth markedly more than reusable space.

THE MEASURED SHAPE, for anyone reading this later:
    total 1,434 MB = 612 MB heap + 822 MB index, 5,361,115 rows
    unique_player_achievement_source  442 MB, 0 lifetime scans
    ..._pkey                          306 MB, 0 lifetime scans
    ..._player_id_f628b3a9             74 MB, scans from the reader now removed
The normalized copy was more than twice the size of the ~660 MB payload it was
derived from.

REVERSIBILITY. Irreversible as written, but re-derivable: the rows can be
rebuilt for any player from `achievements_json`, and for a player missing that,
refetched from the WG achievements endpoint. Nothing would rebuild them
automatically, because the write is gone by design.

Runbook: agents/runbooks/runbook-db-capacity-remediation-2026-09-19.md, Step 5.
"""

from django.db import migrations


def _empty_the_table(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        # sqlite has no TRUNCATE and the harness starts from an empty table.
        return
    # Bound the lock wait for the same reason migration 0087 does: a blocked
    # request queues every later statement on the table behind it.
    schema_editor.execute("SET lock_timeout = '5s'")
    schema_editor.execute("TRUNCATE TABLE warships_playerachievementstat")


def _noop_reverse(apps, schema_editor):
    """Reversing restores an empty table, not the rows.

    Deliberately not an error: rolling the migration back should not be
    blocked, but the rows are re-derived from Player.achievements_json rather
    than recovered from here.
    """
    return


class Migration(migrations.Migration):

    dependencies = [
        ('warships', '0087_drop_unused_pdss_indexes'),
    ]

    operations = [
        migrations.RunPython(_empty_the_table, _noop_reverse),
    ]

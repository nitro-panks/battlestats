"""Achievements are stored ONCE, in Player.achievements_json.

`warships_playerachievementstat` was a normalized second copy of the same
upstream payload. Measured 2026-09-19 it held 1.50 GB across 5.34M rows, with
821 MB of index against 610 MB of heap, and it was rebuilt
delete-then-bulk_create on every refresh of every player — so it also produced
a continuous stream of dead tuples and WAL. Nothing read it: no serializer
field, no view, and zero occurrences of "achievement" anywhere under client/.

The two call sites that remain are maintenance-only (the duplicate-account
merge and a pre-purge count), which is why the model stays even though the
write does not.
"""
from __future__ import annotations

from unittest import mock

from django.test import TestCase

from warships.data import (
    _stored_player_achievement_rows,
    normalize_player_achievement_rows,
    update_achievements_data,
)
from warships.models import DEFAULT_REALM, Player, PlayerAchievementStat

# Two combat achievements in the shape the WG endpoint returns. The codes are
# real entries from ACHIEVEMENT_CATALOG — invented ones normalize to [] (they
# are logged as "unknown combat-like" and dropped), which would make every
# assertion below pass vacuously.
RAW_PAYLOAD = {
    "battle": {
        "PCH001_DoubleKill": 3,
        "PCH003_MainCaliber": 7,
    },
}


class AchievementsSingleStoreTests(TestCase):
    def setUp(self):
        self.player = Player.objects.create(
            player_id=555001, name="achiever", realm=DEFAULT_REALM,
        )

    def _refresh(self):
        with mock.patch("warships.data._fetch_player_achievements",
                        return_value=RAW_PAYLOAD):
            return update_achievements_data(
                self.player.player_id, force_refresh=True, realm=DEFAULT_REALM)

    def test_refresh_writes_the_json_and_no_stat_rows(self):
        rows = self._refresh()

        self.player.refresh_from_db()
        self.assertEqual(self.player.achievements_json, RAW_PAYLOAD)
        self.assertIsNotNone(self.player.achievements_updated_at)

        # The whole point: the second copy is not written.
        self.assertEqual(
            PlayerAchievementStat.objects.filter(player=self.player).count(), 0)

        # ...and the caller's data is unaffected by that.
        self.assertEqual(rows, normalize_player_achievement_rows(RAW_PAYLOAD))
        self.assertTrue(rows, "the fixture should normalize to at least one row")

    def test_stored_rows_derive_from_json_not_from_the_table(self):
        """A player refreshed after the cutover has no stat rows at all.

        Reading the table would return [] for them, so the no-refresh-needed,
        hidden-player and upstream-empty paths all have to read the JSON.
        """
        self._refresh()
        self.player.refresh_from_db()

        derived = _stored_player_achievement_rows(self.player)
        self.assertEqual(derived, normalize_player_achievement_rows(RAW_PAYLOAD))
        self.assertEqual(
            [r["achievement_slug"] for r in derived],
            sorted(r["achievement_slug"] for r in derived),
            "rows stay sorted by slug, as the old table query ordered them",
        )

    def test_stored_rows_tolerate_a_player_with_no_payload(self):
        """Never fetched, or fetched and empty: no crash, no rows."""
        blank = Player.objects.create(
            player_id=555002, name="blank", realm=DEFAULT_REALM)
        self.assertEqual(_stored_player_achievement_rows(blank), [])

        blank.achievements_json = {}
        blank.save(update_fields=["achievements_json"])
        self.assertEqual(_stored_player_achievement_rows(blank), [])

    def test_hidden_player_is_not_refreshed_and_still_reports_stored_rows(self):
        """The hidden path returns early, before any upstream call."""
        self._refresh()
        self.player.is_hidden = True
        self.player.save(update_fields=["is_hidden"])

        with mock.patch("warships.data._fetch_player_achievements") as fetch:
            rows = update_achievements_data(
                self.player.player_id, force_refresh=True, realm=DEFAULT_REALM)
        fetch.assert_not_called()
        self.assertEqual(rows, normalize_player_achievement_rows(RAW_PAYLOAD))

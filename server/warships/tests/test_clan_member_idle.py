from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.models import Clan, Player, realm_cache_key
from warships.tasks import refresh_clan_member_idle_task


class RefreshClanMemberIdleTaskTests(TestCase):
    """The bulk roster idle refresh corrects last_battle_date (and the derived
    days_since_last_battle) for the whole roster in one account/info call,
    without bumping last_fetch (which would suppress the real per-player full
    refresh for ~23h) and without clobbering stored values on a WG error."""

    def _make_clan_with_member(self, clan_id, player_id, days_idle):
        clan = Clan.objects.create(
            clan_id=clan_id, name=f"Clan{clan_id}", realm="na", members_count=1)
        member = Player.objects.create(
            name=f"Member{player_id}",
            player_id=player_id,
            clan=clan,
            realm="na",
            is_hidden=False,
            last_battle_date=timezone.now().date() - timedelta(days=days_idle),
            days_since_last_battle=days_idle,
            last_fetch=timezone.now() - timedelta(days=2),
        )
        return clan, member

    @patch("warships.api.players._bulk_fetch_account_info")
    def test_updates_idle_fields_without_touching_last_fetch(self, mock_bulk):
        clan, member = self._make_clan_with_member(900, 5001, days_idle=73)
        member.refresh_from_db()
        fetch_before = member.last_fetch

        five_days_ago = timezone.now() - timedelta(days=5)
        mock_bulk.return_value = (
            {"5001": {"last_battle_time": int(five_days_ago.timestamp())}}, None)
        # Pre-populate the clan_members cache to verify it gets invalidated so
        # the next poll re-derives idle from the fresh last_battle_date.
        cache.set(realm_cache_key("na", "clan:members:v3:900"), [{"stale": True}])

        result = refresh_clan_member_idle_task.apply(
            kwargs={"clan_id": 900, "realm": "na"}).get()

        member.refresh_from_db()
        self.assertEqual(member.last_battle_date, five_days_ago.date())
        self.assertEqual(member.days_since_last_battle, 5)
        self.assertEqual(member.last_fetch, fetch_before)
        self.assertIsNone(cache.get(realm_cache_key("na", "clan:members:v3:900")))
        self.assertEqual(result["updated"], 1)

    @patch("warships.api.players._bulk_fetch_account_info")
    def test_transient_error_leaves_stored_values_untouched(self, mock_bulk):
        clan, member = self._make_clan_with_member(901, 5002, days_idle=73)
        mock_bulk.return_value = (None, "REQUEST_LIMIT_EXCEEDED")

        result = refresh_clan_member_idle_task.apply(
            kwargs={"clan_id": 901, "realm": "na"}).get()

        member.refresh_from_db()
        self.assertEqual(member.days_since_last_battle, 73)
        self.assertEqual(result["updated"], 0)

    @patch("warships.api.players._per_player_account_fallback")
    @patch("warships.api.players._bulk_fetch_account_info")
    def test_poison_batch_falls_back_to_per_player(self, mock_bulk, mock_fallback):
        clan, member = self._make_clan_with_member(902, 5003, days_idle=73)
        mock_bulk.return_value = ({}, "INVALID_ACCOUNT_ID")
        three_days_ago = timezone.now() - timedelta(days=3)
        mock_fallback.return_value = {
            "5003": {"last_battle_time": int(three_days_ago.timestamp())}}

        result = refresh_clan_member_idle_task.apply(
            kwargs={"clan_id": 902, "realm": "na"}).get()

        mock_fallback.assert_called_once()
        member.refresh_from_db()
        self.assertEqual(member.last_battle_date, three_days_ago.date())
        self.assertEqual(member.days_since_last_battle, 3)
        self.assertEqual(result["updated"], 1)

"""Tests for the Hot-Players engagement-capture queue.

Covers the promotion/eviction heuristic (spike vs sustained vs single-devoted-fan),
hysteresis no-flap, the cap/trim, the kill switch, per-realm isolation, and the
capture sweep's skip-if-fresh behaviour.

See agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md.
"""
from datetime import datetime, time, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.hot_players import (
    capture_hot_players,
    compute_hot_score,
    evaluate_realm_engagement,
    maintain_hot_players,
    refresh_hot_player_freshness,
)
from warships.models import (
    BattleObservation,
    EntityVisitDaily,
    EntityVisitEvent,
    HotPlayer,
    Player,
    Snapshot,
)


def _mk_player(pid, realm="na", **kw):
    d = dict(
        realm=realm, is_hidden=False, pvp_battles=1000, pvp_wins=550,
        last_battle_date=timezone.now().date(), days_since_last_battle=0,
    )
    d.update(kw)
    return Player.objects.create(name=f"P{pid}", player_id=pid, **d)


def _visit_day(pid, day, *, views=1, sessions=1, realm="na"):
    """Create an EntityVisitDaily row for a player on a given day."""
    return EntityVisitDaily.objects.create(
        date=day,
        entity_type=EntityVisitEvent.ENTITY_TYPE_PLAYER,
        entity_id=pid,
        realm=realm,
        entity_name_snapshot=f"P{pid}",
        views_raw=views,
        views_deduped=views,
        unique_visitors=1,
        unique_sessions=sessions,
        last_view_at=datetime.combine(day, time(12, 0)),
    )


def _spread_days(pid, n_days, *, sessions_per_day=2, views_per_day=2, realm="na",
                 end_offset=0):
    """Visit `pid` on `n_days` distinct recent days (most recent = today-end_offset)."""
    today = timezone.now().date()
    for i in range(n_days):
        _visit_day(pid, today - timedelta(days=i + end_offset),
                   views=views_per_day, sessions=sessions_per_day, realm=realm)


# Pin the heuristic env to the documented defaults so a host env override
# (HOT_PROMOTE_*, HOT_EVICT_*, etc.) can't perturb these assertions.
HOT_DEFAULTS = {
    "HOT_PLAYERS_ENABLED": "1",
    "HOT_PLAYERS_WINDOW_DAYS": "14",
    "HOT_PROMOTE_MIN_ACTIVE_DAYS": "3",
    "HOT_PROMOTE_MAX_RECENCY_DAYS": "3",
    "HOT_PROMOTE_MIN_SESSIONS": "2",
    "HOT_EVICT_INACTIVITY_DAYS": "14",
    "HOT_EVICT_MIN_ACTIVE_DAYS": "2",
    "HOT_PLAYERS_MAX": "500",
}


def _hot_env(**overrides):
    env = dict(HOT_DEFAULTS)
    env.update({k: str(v) for k, v in overrides.items()})
    return patch.dict("os.environ", env)


class EngagementHeuristicTests(TestCase):
    def test_score_orders_active_days_primary(self):
        # Higher active_days always outranks more sessions/views at fewer days.
        self.assertGreater(
            compute_hot_score(active_days=4, sessions=1, views=1),
            compute_hot_score(active_days=3, sessions=999, views=999),
        )
        # Within the same active_days, sessions break the tie before views.
        self.assertGreater(
            compute_hot_score(active_days=3, sessions=5, views=1),
            compute_hot_score(active_days=3, sessions=4, views=999),
        )

    def test_active_days_counts_distinct_days_not_views(self):
        _mk_player(7001)
        # ONE viral day with many deduped views — a spike, not recurrence.
        _visit_day(7001, timezone.now().date(), views=500, sessions=400)
        eng = evaluate_realm_engagement("na")
        self.assertEqual(eng[7001]["active_days"], 1)
        self.assertEqual(eng[7001]["views"], 500)


class PromotionEvictionTests(TestCase):
    def test_spike_does_not_promote(self):
        # A one-time crowd: 1 active day, huge views — must NOT promote.
        _mk_player(7101)
        _visit_day(7101, timezone.now().date(), views=300, sessions=250)
        with _hot_env():
            maintain_hot_players("na")
        self.assertFalse(HotPlayer.objects.filter(player__player_id=7101).exists())

    def test_sustained_interest_promotes(self):
        # Visited a little on 5 separate recent days — sustained, must promote.
        _mk_player(7201)
        _spread_days(7201, 5, sessions_per_day=2, views_per_day=2)
        with _hot_env():
            maintain_hot_players("na")
        hp = HotPlayer.objects.get(player__player_id=7201)
        self.assertEqual(hp.source, HotPlayer.SOURCE_ENGAGEMENT)
        self.assertEqual(hp.active_days_window, 5)

    def test_single_devoted_fan_promotes(self):
        # The motivating case: ONE returning person (unique_visitors == 1) across
        # many days with multiple sessions. Must promote — we do NOT gate on
        # visitor breadth.
        _mk_player(7301)
        # 4 distinct days, 1 visitor, 2 sessions/day => sessions>=2 satisfied.
        _spread_days(7301, 4, sessions_per_day=2, views_per_day=3)
        # Sanity: this fan is a single visitor.
        self.assertEqual(
            EntityVisitDaily.objects.filter(entity_id=7301).first().unique_visitors, 1)
        with _hot_env():
            maintain_hot_players("na")
        self.assertTrue(HotPlayer.objects.filter(player__player_id=7301).exists())

    def test_low_total_sessions_does_not_promote(self):
        # Enough active days + fresh recency, but the windowed session total is
        # below HOT_PROMOTE_MIN_SESSIONS (2) -> the anti-single-reload floor
        # blocks it. Sessions sum to 1 across the window here.
        _mk_player(7401)
        today = timezone.now().date()
        _visit_day(7401, today, views=4, sessions=1)
        _visit_day(7401, today - timedelta(days=1), views=4, sessions=0)
        _visit_day(7401, today - timedelta(days=2), views=4, sessions=0)
        with _hot_env():
            maintain_hot_players("na")
        self.assertFalse(HotPlayer.objects.filter(player__player_id=7401).exists())

    def test_stale_recency_does_not_promote(self):
        # 4 active days but the most recent was 10 days ago -> recency blocks it.
        _mk_player(7501)
        _spread_days(7501, 4, end_offset=10)
        with _hot_env():
            maintain_hot_players("na")
        self.assertFalse(HotPlayer.objects.filter(player__player_id=7501).exists())

    def test_eviction_on_inactivity(self):
        # Existing member with no views at all in the window -> evicted.
        p = _mk_player(7601)
        HotPlayer.objects.create(
            player=p, realm="na", source=HotPlayer.SOURCE_ENGAGEMENT,
            active_days_window=5, unique_sessions_window=8, hot_score=5e6,
            last_engaged_at=timezone.now() - timedelta(days=20))
        with _hot_env():
            maintain_hot_players("na")
        self.assertFalse(HotPlayer.objects.filter(player__player_id=7601).exists())

    def test_pinned_member_never_evicted(self):
        # A pinned override with zero engagement survives maintenance.
        p = _mk_player(7651)
        HotPlayer.objects.create(
            player=p, realm="na", source=HotPlayer.SOURCE_PINNED,
            hot_score=0.0)
        with _hot_env():
            maintain_hot_players("na")
        self.assertTrue(
            HotPlayer.objects.filter(player__player_id=7651,
                                     source=HotPlayer.SOURCE_PINNED).exists())


class HysteresisTests(TestCase):
    def test_hover_at_two_active_days_stays_put(self):
        # An incumbent hovering at exactly 2 active-days/W: below the PROMOTE
        # threshold (3) but at/above the EVICT threshold (2) -> must NOT churn.
        p = _mk_player(7701)
        HotPlayer.objects.create(
            player=p, realm="na", source=HotPlayer.SOURCE_ENGAGEMENT,
            active_days_window=3, unique_sessions_window=4, hot_score=3e6,
            last_engaged_at=timezone.now())
        _spread_days(7701, 2, sessions_per_day=2)  # 2 active days now
        with _hot_env():
            maintain_hot_players("na")
        hp = HotPlayer.objects.get(player__player_id=7701)  # still present
        self.assertEqual(hp.active_days_window, 2)  # re-scored, not evicted

    def test_drop_below_evict_threshold_evicts(self):
        # Same incumbent but now only 1 active day (< EVICT 2) -> evicted.
        p = _mk_player(7751)
        HotPlayer.objects.create(
            player=p, realm="na", source=HotPlayer.SOURCE_ENGAGEMENT,
            active_days_window=3, unique_sessions_window=4, hot_score=3e6,
            last_engaged_at=timezone.now())
        _spread_days(7751, 1, sessions_per_day=2)
        with _hot_env():
            maintain_hot_players("na")
        self.assertFalse(HotPlayer.objects.filter(player__player_id=7751).exists())


class CapTrimTests(TestCase):
    def test_trims_to_cap_by_hot_score(self):
        # Promote 4 qualifying players but cap at 2 -> the 2 lowest-score trimmed.
        for idx, pid in enumerate((7801, 7802, 7803, 7804)):
            _mk_player(pid)
            # More active days -> higher score; pid order ascends with days so
            # 7804 is hottest.
            _spread_days(pid, 3 + idx, sessions_per_day=2)
        with _hot_env(HOT_PLAYERS_MAX="2"):
            maintain_hot_players("na")
        survivors = set(
            HotPlayer.objects.filter(realm="na").values_list("player__player_id", flat=True))
        self.assertEqual(len(survivors), 2)
        self.assertEqual(survivors, {7803, 7804})  # the two hottest


class KillSwitchTests(TestCase):
    def test_disabled_task_no_ops(self):
        from warships import tasks
        _mk_player(7901)
        _spread_days(7901, 5, sessions_per_day=2)
        with _hot_env(HOT_PLAYERS_ENABLED="0"):
            res = tasks.maintain_hot_players_task("na")
        self.assertEqual(res["status"], "skipped")
        self.assertFalse(HotPlayer.objects.filter(player__player_id=7901).exists())

    def test_capture_disabled_task_no_ops(self):
        from warships import tasks
        with _hot_env(HOT_PLAYERS_ENABLED="0"):
            res = tasks.capture_hot_player_observations_task("na")
        self.assertEqual(res["status"], "skipped")


class PerRealmIsolationTests(TestCase):
    def test_engagement_scoped_to_realm(self):
        # Same numeric account engaged in NA only; EU maintenance must not see it.
        _mk_player(8001, realm="na")
        _spread_days(8001, 5, sessions_per_day=2, realm="na")
        with _hot_env():
            maintain_hot_players("eu")
        self.assertFalse(HotPlayer.objects.filter(realm="eu").exists())
        with _hot_env():
            maintain_hot_players("na")
        self.assertTrue(
            HotPlayer.objects.filter(realm="na", player__player_id=8001).exists())


class CaptureSweepTests(TestCase):
    def _hot_member(self, pid, realm="na"):
        p = _mk_player(pid, realm=realm)
        return HotPlayer.objects.create(
            player=p, realm=realm, source=HotPlayer.SOURCE_ENGAGEMENT,
            hot_score=5e6, last_engaged_at=timezone.now())

    @patch("warships.incremental_battles.record_observation_and_diff")
    @patch("warships.data.update_snapshot_data")
    def test_skip_if_fresh_observation(self, mock_snap, mock_obs):
        hp = self._hot_member(8101)
        # A fresh observation (now) -> capture must SKIP the WG observation.
        BattleObservation.objects.create(player=hp.player, pvp_battles=1000)
        with _hot_env(HOT_OBSERVE_FLOOR_HOURS="20", HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na")
        mock_obs.assert_not_called()
        self.assertEqual(res["obs_skipped_fresh"], 1)

    @patch("warships.incremental_battles.record_observation_and_diff")
    @patch("warships.data.update_snapshot_data")
    def test_stale_observation_triggers_capture(self, mock_snap, mock_obs):
        hp = self._hot_member(8201)
        mock_obs.return_value = {"status": "completed", "observation_id": 1}
        obs = BattleObservation.objects.create(player=hp.player, pvp_battles=1000)
        # Backdate beyond the floor window (observed_at is auto_now_add).
        BattleObservation.objects.filter(pk=obs.pk).update(
            observed_at=timezone.now() - timedelta(hours=30))
        with _hot_env(HOT_OBSERVE_FLOOR_HOURS="20", HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na")
        mock_obs.assert_called_once_with(8201, "na")
        self.assertEqual(res["observed"], 1)

    @patch("warships.incremental_battles.record_observation_and_diff")
    @patch("warships.data.update_snapshot_data")
    def test_snapshot_written_when_missing_skipped_when_present(self, mock_snap, mock_obs):
        hp = self._hot_member(8301)
        mock_obs.return_value = {"status": "completed"}
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na")
        mock_snap.assert_called_once_with(8301, "na", refresh_player=False)
        self.assertEqual(res["snapshotted"], 1)

        # Now today's snapshot exists -> the next sweep skips the snapshot path.
        Snapshot.objects.create(player=hp.player, date=timezone.now().date())
        mock_snap.reset_mock()
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na")
        mock_snap.assert_not_called()
        self.assertEqual(res["snap_skipped_present"], 1)

    @patch("warships.incremental_battles.record_observation_and_diff")
    @patch("warships.data.update_snapshot_data")
    def test_hidden_account_recorded_as_skip(self, mock_snap, mock_obs):
        self._hot_member(8401)
        # WG short-circuits hidden/failed fetches with a skipped status.
        mock_obs.return_value = {"status": "skipped",
                                 "reason": "wg-fetch-failed-or-hidden"}
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na")
        self.assertEqual(res["obs_skipped_hidden"], 1)
        self.assertEqual(res["observed"], 0)


class FreshnessSweepTests(TestCase):
    """Tier 3 freshness sweep: keep hot players inside the 15-min visit window.

    See agents/runbooks/runbook-player-refresh-latency-2026-06-10.md.
    """

    def _hot_member(self, pid, realm="na", **player_kw):
        p = _mk_player(pid, realm=realm, **player_kw)
        hp = HotPlayer.objects.create(
            player=p, realm=realm, source=HotPlayer.SOURCE_ENGAGEMENT,
            hot_score=5e6, last_engaged_at=timezone.now())
        return hp

    @patch("warships.data.update_battle_data")
    def test_stale_hot_player_refreshed(self, mock_upd):
        hp = self._hot_member(9101)
        # battles_updated_at older than the 12-min freshness threshold.
        Player.objects.filter(pk=hp.player.pk).update(
            battles_updated_at=timezone.now() - timedelta(minutes=14))
        with _hot_env(HOT_PLAYERS_FRESH_AFTER_MINUTES="12",
                      HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")
        mock_upd.assert_called_once_with(9101, realm="na", force_refresh=True)
        self.assertEqual(res["refreshed"], 1)
        self.assertEqual(res["skipped_fresh"], 0)

    @patch("warships.data.update_battle_data")
    def test_fresh_hot_player_skipped(self, mock_upd):
        hp = self._hot_member(9201)
        # Already inside the window -> skip-if-fresh, no WG call.
        Player.objects.filter(pk=hp.player.pk).update(
            battles_updated_at=timezone.now() - timedelta(minutes=5))
        with _hot_env(HOT_PLAYERS_FRESH_AFTER_MINUTES="12",
                      HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")
        mock_upd.assert_not_called()
        self.assertEqual(res["skipped_fresh"], 1)
        self.assertEqual(res["refreshed"], 0)

    @patch("warships.data.update_battle_data")
    def test_never_refreshed_player_refreshed(self, mock_upd):
        # battles_updated_at is NULL (never refreshed) -> treated as stale.
        hp = self._hot_member(9251)
        Player.objects.filter(pk=hp.player.pk).update(battles_updated_at=None)
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")
        mock_upd.assert_called_once_with(9251, realm="na", force_refresh=True)
        self.assertEqual(res["refreshed"], 1)

    @patch("warships.data.update_battle_data")
    def test_hidden_account_gated_up_front(self, mock_upd):
        hp = self._hot_member(9301, is_hidden=True)
        Player.objects.filter(pk=hp.player.pk).update(
            battles_updated_at=timezone.now() - timedelta(minutes=30))
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")
        mock_upd.assert_not_called()  # no wasted WG call
        self.assertEqual(res["skipped_hidden"], 1)
        self.assertEqual(res["refreshed"], 0)

    @patch("warships.data.update_battle_data")
    def test_failed_refresh_counted_not_raised(self, mock_upd):
        hp = self._hot_member(9351)
        Player.objects.filter(pk=hp.player.pk).update(
            battles_updated_at=timezone.now() - timedelta(minutes=30))
        mock_upd.side_effect = RuntimeError("WG hiccup")
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")  # must not raise
        self.assertEqual(res["errors"], 1)
        self.assertEqual(res["refreshed"], 0)

    @patch("warships.data.update_battle_data")
    def test_respects_cap(self, mock_upd):
        for pid, score in ((9401, 9e6), (9402, 8e6), (9403, 7e6)):
            p = _mk_player(pid)
            Player.objects.filter(pk=p.pk).update(
                battles_updated_at=timezone.now() - timedelta(minutes=30))
            HotPlayer.objects.create(
                player=p, realm="na", source=HotPlayer.SOURCE_ENGAGEMENT,
                hot_score=score, last_engaged_at=timezone.now())
        with _hot_env(HOT_PLAYERS_MAX="2", HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")
        # Only the 2 hottest are swept.
        self.assertEqual(res["hot_set_size"], 2)
        self.assertEqual(res["refreshed"], 2)
        called_ids = {c.args[0] for c in mock_upd.call_args_list}
        self.assertEqual(called_ids, {9401, 9402})

    @patch("warships.data.update_battle_data")
    def test_per_realm_isolation(self, mock_upd):
        hp = self._hot_member(9501, realm="na")
        Player.objects.filter(pk=hp.player.pk).update(
            battles_updated_at=timezone.now() - timedelta(minutes=30))
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("eu")  # different realm
        mock_upd.assert_not_called()
        self.assertEqual(res["hot_set_size"], 0)

    @patch("warships.data.update_battle_data")
    def test_disabled_no_ops(self, mock_upd):
        hp = self._hot_member(9601)
        Player.objects.filter(pk=hp.player.pk).update(
            battles_updated_at=timezone.now() - timedelta(minutes=30))
        with _hot_env(HOT_PLAYERS_ENABLED="0", HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")
        mock_upd.assert_not_called()
        self.assertEqual(res["status"], "disabled")

    @patch("warships.data.update_battle_data")
    def test_task_disabled_no_ops(self, mock_upd):
        from warships import tasks
        with _hot_env(HOT_PLAYERS_ENABLED="0"):
            res = tasks.refresh_hot_player_freshness_task("na")
        self.assertEqual(res["status"], "skipped")
        mock_upd.assert_not_called()

    @patch("warships.data._fetch_ship_stats_for_player", return_value=[])
    def test_real_update_battle_data_advances_timestamp(self, mock_fetch):
        """The bypass is real, not just mock-shaped.

        Mocks only the WG fetch (returns [] -> the empty-stats branch that still
        advances battles_updated_at), and runs the REAL update_battle_data
        through the sweep. Pins that force_refresh=True actually clears the
        internal 15-min guard for a player in the [12, 15) staleness band — the
        guard that would otherwise neuter the whole tier.
        """
        hp = self._hot_member(9701)
        before = timezone.now() - timedelta(minutes=13)  # inside the 15-min guard
        Player.objects.filter(pk=hp.player.pk).update(
            battles_updated_at=before, battles_json=[{"ship_id": 1}])
        with _hot_env(HOT_PLAYERS_FRESH_AFTER_MINUTES="12",
                      HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = refresh_hot_player_freshness("na")
        self.assertEqual(res["refreshed"], 1)
        hp.player.refresh_from_db()
        # battles_updated_at advanced past the 13-min-old value despite being
        # inside update_battle_data's own 15-min cache guard.
        self.assertGreater(hp.player.battles_updated_at, before)


class CommandErgonomicsTests(TestCase):
    def test_maintain_dry_run_writes_nothing(self):
        _mk_player(8501)
        _spread_days(8501, 5, sessions_per_day=2)
        with _hot_env():
            call_command("maintain_hot_players", "--realm", "na", "--dry-run",
                         stdout=StringIO())
        self.assertFalse(HotPlayer.objects.filter(player__player_id=8501).exists())

    def test_maintain_command_promotes(self):
        _mk_player(8601)
        _spread_days(8601, 5, sessions_per_day=2)
        with _hot_env():
            call_command("maintain_hot_players", "--realm", "na", stdout=StringIO())
        self.assertTrue(HotPlayer.objects.filter(player__player_id=8601).exists())

    def test_status_command_runs(self):
        p = _mk_player(8701)
        HotPlayer.objects.create(
            player=p, realm="na", source=HotPlayer.SOURCE_ENGAGEMENT,
            hot_score=5e6, last_engaged_at=timezone.now())
        buf = StringIO()
        call_command("hot_players_status", "--realm", "na", stdout=buf)
        self.assertIn("Hot-set size: 1", buf.getvalue())

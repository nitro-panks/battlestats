"""Tests for the Hot-Players engagement-capture queue.

Covers the promotion/eviction heuristic (spike vs sustained vs single-devoted-fan),
hysteresis no-flap, the cap/trim, the kill switch, per-realm isolation, and the
capture sweep's skip-if-fresh behaviour.

See agents/runbooks/runbook-hot-players-engagement-queue-2026-06-10.md.
"""
import os
from datetime import datetime, time, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from warships.hot_players import (
    backfill_hot_players,
    capture_hot_players,
    compute_hot_score,
    evaluate_realm_engagement,
    maintain_hot_players,
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


class BackfillSeedTests(TestCase):
    """One-time most-active seed: fills to the cap, ranks below engagement, is
    protected from inactivity-eviction, trimmed first, and graduates on real
    engagement."""

    def test_fills_to_cap_with_active_high_volume_excluding_hidden_inactive(self):
        # 4 candidates with descending battle volume; one hidden, one inactive.
        _mk_player(8001, pvp_battles=9000)
        _mk_player(8002, pvp_battles=5000)
        _mk_player(8003, pvp_battles=8000, is_hidden=True)           # excluded
        _mk_player(8004, pvp_battles=7000,
                   last_battle_date=timezone.now().date() - timedelta(days=30))  # inactive, excluded
        with _hot_env(HOT_PLAYERS_MAX="3"):
            res = backfill_hot_players("na")
        self.assertEqual(res['added'], 2)  # only the two active, non-hidden
        seeds = set(HotPlayer.objects.filter(
            realm="na", source=HotPlayer.SOURCE_BACKFILL)
            .values_list("player__player_id", flat=True))
        self.assertEqual(seeds, {8001, 8002})
        # Ordered by volume: 8001 (9000) outranks 8002 (5000).
        hp1 = HotPlayer.objects.get(player__player_id=8001)
        hp2 = HotPlayer.objects.get(player__player_id=8002)
        self.assertGreater(hp1.hot_score, hp2.hot_score)

    def test_excludes_players_the_floor_keeps_fresh(self):
        # The seed must skip players the capture sweep would skip-if-fresh: those
        # with a BattleObservation within HOT_OBSERVE_FLOOR_HOURS. Only the
        # floor-missed (stale) player should be seeded.
        fresh = _mk_player(8011, pvp_battles=9000)   # highest volume, but fresh
        stale = _mk_player(8012, pvp_battles=5000)   # lower volume, floor-missed
        BattleObservation.objects.create(player=fresh, pvp_battles=9000)  # now
        obs = BattleObservation.objects.create(player=stale, pvp_battles=5000)
        BattleObservation.objects.filter(pk=obs.pk).update(
            observed_at=timezone.now() - timedelta(hours=30))   # past the window
        with _hot_env(HOT_OBSERVE_FLOOR_HOURS="20", HOT_PLAYERS_MAX="5"):
            res = backfill_hot_players("na")
        self.assertEqual(res['added'], 1)            # only the stale player
        seeds = set(HotPlayer.objects.filter(
            realm="na", source=HotPlayer.SOURCE_BACKFILL)
            .values_list("player__player_id", flat=True))
        self.assertEqual(seeds, {8012})              # fresh 8011 excluded

    def test_seed_scores_below_engagement_floor(self):
        # Even a huge-battle seed ranks under the weakest surviving engaged member
        # (active_days=2 -> score 2_000_000).
        _mk_player(8101, pvp_battles=10_000_000)
        with _hot_env():
            backfill_hot_players("na")
        hp = HotPlayer.objects.get(player__player_id=8101)
        self.assertLess(hp.hot_score, 2_000_000.0)

    def test_idempotent_topup_does_not_duplicate(self):
        for pid in range(8201, 8211):
            _mk_player(pid, pvp_battles=1000 + pid)
        with _hot_env(HOT_PLAYERS_MAX="5"):
            first = backfill_hot_players("na")
            second = backfill_hot_players("na")
        self.assertEqual(first['added'], 5)
        self.assertEqual(second['added'], 0)       # already full
        self.assertEqual(HotPlayer.objects.filter(realm="na").count(), 5)

    def test_does_not_displace_existing_members(self):
        # An engaged member already in the queue is not double-counted; backfill
        # fills only the remaining headroom.
        p = _mk_player(8301, pvp_battles=4000)
        HotPlayer.objects.create(player=p, realm="na",
                                 source=HotPlayer.SOURCE_ENGAGEMENT, hot_score=3e6)
        _mk_player(8302, pvp_battles=9000)
        with _hot_env(HOT_PLAYERS_MAX="2"):
            res = backfill_hot_players("na")
        self.assertEqual(res['added'], 1)          # one open slot
        self.assertEqual(
            HotPlayer.objects.get(player__player_id=8301).source,
            HotPlayer.SOURCE_ENGAGEMENT)            # untouched

    def test_seed_survives_maintain_without_engagement(self):
        # A backfill seed with zero view-engagement must NOT be inactivity-evicted.
        p = _mk_player(8401, pvp_battles=6000)
        HotPlayer.objects.create(player=p, realm="na",
                                 source=HotPlayer.SOURCE_BACKFILL,
                                 hot_score=6000.0)
        with _hot_env():
            maintain_hot_players("na")
        self.assertTrue(HotPlayer.objects.filter(player__player_id=8401).exists())

    def test_seed_trimmed_before_engagement_when_over_cap(self):
        # One engaged member + one seed, cap=1 -> the seed is trimmed, engaged kept.
        _mk_player(8501)
        _spread_days(8501, 4, sessions_per_day=2)  # qualifies for promotion
        seed = _mk_player(8502, pvp_battles=9000)
        HotPlayer.objects.create(player=seed, realm="na",
                                 source=HotPlayer.SOURCE_BACKFILL, hot_score=9000.0)
        with _hot_env(HOT_PLAYERS_MAX="1"):
            maintain_hot_players("na")
        self.assertTrue(HotPlayer.objects.filter(player__player_id=8501).exists())
        self.assertFalse(HotPlayer.objects.filter(player__player_id=8502).exists())

    def test_seed_graduates_to_engagement_on_real_recurrence(self):
        # A seed that earns sustained view-recurrence is promoted to 'engagement'
        # (and thereafter lives by the normal rules).
        p = _mk_player(8601, pvp_battles=6000)
        HotPlayer.objects.create(player=p, realm="na",
                                 source=HotPlayer.SOURCE_BACKFILL, hot_score=6000.0)
        _spread_days(8601, 4, sessions_per_day=2)  # now genuinely engaged
        with _hot_env():
            maintain_hot_players("na")
        hp = HotPlayer.objects.get(player__player_id=8601)
        self.assertEqual(hp.source, HotPlayer.SOURCE_ENGAGEMENT)
        self.assertGreaterEqual(hp.hot_score, 3_000_000.0)


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

    def test_env_absent_defaults_disabled(self):
        # DB-audit item 10 (runbook-db-table-audit-2026-07-19.md): the code
        # default must match prod reality — queue OFF (disabled 2026-06-16).
        # With HOT_PLAYERS_ENABLED entirely ABSENT from the env, every gate
        # read site resolves to disabled.
        from warships import tasks
        from warships.hot_players import _enabled
        _mk_player(7902)
        _spread_days(7902, 5, sessions_per_day=2)
        env = {k: v for k, v in os.environ.items() if k != "HOT_PLAYERS_ENABLED"}
        with patch.dict("os.environ", env, clear=True):
            self.assertFalse(_enabled())
            res = tasks.maintain_hot_players_task("na")
            self.assertEqual(res["status"], "skipped")
            res = tasks.capture_hot_player_observations_task("na")
            self.assertEqual(res["status"], "skipped")
        self.assertFalse(HotPlayer.objects.filter(player__player_id=7902).exists())


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

    def _backfill_member(self, pid, *, last_observed_at=None, hot_score=1000.0,
                         realm="na"):
        p = _mk_player(pid, realm=realm)
        return HotPlayer.objects.create(
            player=p, realm=realm, source=HotPlayer.SOURCE_BACKFILL,
            hot_score=hot_score, last_observed_at=last_observed_at)

    @patch("warships.incremental_battles.record_observation_and_diff")
    @patch("warships.data.update_snapshot_data")
    def test_capture_budget_stops_early(self, mock_snap, mock_obs):
        # 10 floor-missed (no observation) backfill members, budget of 4 WG calls:
        # the sweep must pull exactly 4 and defer the rest, never the whole set.
        # This is the ceiling the 540s incident slipped past — assert it directly.
        mock_obs.return_value = {"status": "completed"}
        for pid in range(8701, 8711):
            self._backfill_member(pid)
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na", max_pulls=4)
        self.assertEqual(mock_obs.call_count, 4)
        self.assertEqual(res["wg_calls"], 4)
        self.assertTrue(res["stopped_early"])
        self.assertEqual(res["remaining"], 6)

    @patch("warships.incremental_battles.record_observation_and_diff")
    @patch("warships.data.update_snapshot_data")
    def test_capture_rotates_oldest_coverage_first(self, mock_snap, mock_obs):
        # Backfill members all floor-missed but with distinct coverage ages. With a
        # budget of 2, the sweep must spend it on the LEAST-recently-covered first
        # (NULL, then oldest) so the set drains round-robin instead of starving.
        mock_obs.return_value = {"status": "completed"}
        now = timezone.now()
        self._backfill_member(8801, last_observed_at=None)                       # oldest
        self._backfill_member(8802, last_observed_at=now - timedelta(hours=50))
        self._backfill_member(8803, last_observed_at=now - timedelta(hours=25))  # newest
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na", max_pulls=2)
        pulled_ids = [c.args[0] for c in mock_obs.call_args_list]
        self.assertEqual(pulled_ids, [8801, 8802])   # NULL first, then 50h-old
        self.assertEqual(res["wg_calls"], 2)

    @patch("warships.incremental_battles.record_observation_and_diff")
    @patch("warships.data.update_snapshot_data")
    def test_capture_priority_members_before_backfill(self, mock_snap, mock_obs):
        # An engagement member is sweep-priority over a backfill seed regardless of
        # coverage age — budget of 1 must spend on engagement first.
        mock_obs.return_value = {"status": "completed"}
        self._backfill_member(8901, last_observed_at=None)   # oldest by age
        self._hot_member(8902)                               # engagement, priority
        with _hot_env(HOT_PLAYERS_CAPTURE_DELAY="0"):
            res = capture_hot_players("na", max_pulls=1)
        pulled_ids = [c.args[0] for c in mock_obs.call_args_list]
        self.assertEqual(pulled_ids, [8902])   # engagement before the older backfill
        self.assertTrue(res["stopped_early"])


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

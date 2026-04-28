"""Tests for the incremental-battle orchestrator.

Covers two layers:

* Pure functions (`compute_battle_events`, `coerce_observation_payload`,
  `_snapshot_from_player_row`) — exercised without the DB.
* DB-touching orchestrator (`record_observation_from_payloads`,
  `record_observation_and_diff`) — exercised against the test DB with a
  fake `Ship` row + a synthesized `Player` row, no WG calls.
"""

from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as django_timezone

from warships.incremental_battles import (
    PlayerSnapshot,
    ShipSnapshot,
    _apply_event_to_daily_summary,
    _snapshot_from_player_row,
    coerce_observation_payload,
    compute_battle_events,
    rebuild_daily_ship_stats_for_date,
    rebuild_period_rollups_for_date,
    record_observation_and_diff,
    record_observation_from_payloads,
)
from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    PlayerDailyShipStats,
    PlayerMonthlyShipStats,
    PlayerWeeklyShipStats,
    PlayerYearlyShipStats,
    Ship,
)


def _make_snapshot(*, pvp_battles, pvp_wins=0, pvp_losses=0, pvp_frags=0,
                   pvp_survived=0, ships=None):
    return PlayerSnapshot(
        pvp_battles=pvp_battles,
        pvp_wins=pvp_wins,
        pvp_losses=pvp_losses,
        pvp_frags=pvp_frags,
        pvp_survived_battles=pvp_survived,
        last_battle_time=datetime(2026, 4, 28, 18, 0, tzinfo=timezone.utc),
        ships={
            ship_id: ShipSnapshot(
                ship_id=ship_id,
                battles=row["battles"],
                wins=row.get("wins", 0),
                losses=row.get("losses", 0),
                frags=row.get("frags", 0),
                damage_dealt=row.get("damage_dealt", 0),
                xp=row.get("xp", 0),
                planes_killed=row.get("planes_killed", 0),
                survived_battles=row.get("survived_battles", 0),
            )
            for ship_id, row in (ships or {}).items()
        },
    )


class ComputeBattleEventsTests(TestCase):
    """Pure diff function — no DB."""

    def test_no_advance_returns_empty(self):
        before = _make_snapshot(pvp_battles=100, ships={
            42: {"battles": 50}, 43: {"battles": 50},
        })
        same = _make_snapshot(pvp_battles=100, ships={
            42: {"battles": 50}, 43: {"battles": 50},
        })
        self.assertEqual(compute_battle_events(before, same), [])

    def test_single_win_one_ship(self):
        before = _make_snapshot(pvp_battles=100, pvp_wins=50, pvp_survived=60, ships={
            42: {"battles": 50, "wins": 25, "frags": 40, "damage_dealt": 1_000_000,
                 "xp": 50_000, "survived_battles": 30},
        })
        after = _make_snapshot(pvp_battles=101, pvp_wins=51, pvp_survived=61, ships={
            42: {"battles": 51, "wins": 26, "frags": 42, "damage_dealt": 1_048_000,
                 "xp": 51_500, "survived_battles": 31},
        })
        events = compute_battle_events(before, after)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["ship_id"], 42)
        self.assertEqual(e["battles_delta"], 1)
        self.assertEqual(e["wins_delta"], 1)
        self.assertEqual(e["losses_delta"], 0)
        self.assertEqual(e["frags_delta"], 2)
        self.assertEqual(e["damage_delta"], 48_000)
        self.assertEqual(e["xp_delta"], 1_500)
        self.assertTrue(e["survived"])

    def test_single_loss_with_death_uses_per_ship_survived(self):
        before = _make_snapshot(pvp_battles=100, pvp_survived=60, ships={
            42: {"battles": 50, "survived_battles": 30},
        })
        after = _make_snapshot(pvp_battles=101, pvp_survived=60, ships={
            42: {"battles": 51, "losses": 1, "damage_dealt": 18_000,
                 "survived_battles": 30},
        })
        events = compute_battle_events(before, after)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["survived"])
        self.assertEqual(events[0]["losses_delta"], 1)

    def test_new_ship_first_battle_attributed_correctly(self):
        before = _make_snapshot(pvp_battles=100, ships={
            42: {"battles": 100, "wins": 50},
        })
        after = _make_snapshot(pvp_battles=101, pvp_wins=51, ships={
            42: {"battles": 100, "wins": 50},
            999: {"battles": 1, "wins": 1, "damage_dealt": 30_000,
                  "survived_battles": 1},
        })
        events = compute_battle_events(before, after)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ship_id"], 999)
        self.assertEqual(events[0]["battles_delta"], 1)
        self.assertTrue(events[0]["survived"])

    def test_multi_match_collapsed_event_has_null_survived(self):
        before = _make_snapshot(pvp_battles=100, ships={
            42: {"battles": 50, "survived_battles": 30},
        })
        after = _make_snapshot(pvp_battles=103, pvp_wins=2, pvp_survived=62, ships={
            42: {"battles": 53, "wins": 2, "losses": 1, "damage_dealt": 120_000,
                 "survived_battles": 32},
        })
        events = compute_battle_events(before, after)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["battles_delta"], 3)
        self.assertIsNone(e["survived"], "ambiguous when delta_battles > 1")


class CoerceObservationPayloadTests(TestCase):
    def test_basic_payload_round_trip(self):
        player_data = {
            "hidden_profile": False,
            "last_battle_time": 1745000000,
            "statistics": {"pvp": {
                "battles": 250, "wins": 120, "losses": 130, "frags": 200,
                "survived_battles": 140,
            }},
        }
        ship_data = [
            {"ship_id": 42, "pvp": {
                "battles": 200, "wins": 100, "losses": 100, "frags": 180,
                "damage_dealt": 4_000_000, "xp": 200_000, "planes_killed": 5,
                "survived_battles": 120,
            }},
        ]
        snapshot = coerce_observation_payload(player_data, ship_data)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.pvp_battles, 250)
        self.assertEqual(snapshot.ships[42].damage_dealt, 4_000_000)
        self.assertEqual(snapshot.ships[42].planes_killed, 5)

    def test_hidden_profile_returns_none(self):
        self.assertIsNone(coerce_observation_payload({"hidden_profile": True}, []))

    def test_empty_payload_returns_none(self):
        self.assertIsNone(coerce_observation_payload({}, []))


class SnapshotFromPlayerRowTests(TestCase):
    """The piggyback hook reads aggregates straight off the Player row."""

    def test_reads_aggregates_from_player_columns(self):
        player = Player(
            name="rollout_test", player_id=1234567, realm="na",
            pvp_battles=500, pvp_wins=260, pvp_losses=240, pvp_frags=400,
            pvp_survived_battles=300,
        )
        ship_data = [
            {"ship_id": 42, "pvp": {"battles": 500, "wins": 260,
                                    "damage_dealt": 9_000_000, "xp": 400_000,
                                    "survived_battles": 300}},
        ]
        snapshot = _snapshot_from_player_row(player, ship_data)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.pvp_battles, 500)
        self.assertEqual(snapshot.ships[42].damage_dealt, 9_000_000)

    def test_hidden_player_returns_none(self):
        player = Player(name="hidden", player_id=9999, realm="na",
                        is_hidden=True)
        self.assertIsNone(_snapshot_from_player_row(player, []))


class RecordObservationFromPayloadsTests(TestCase):
    """Orchestrator: persist observation, diff, write events. No WG calls."""

    def setUp(self):
        self.player = Player.objects.create(
            name="testbench", player_id=4242424242, realm="na",
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )

    def _ship_payload(self, *, battles, wins=0, losses=0, frags=0, damage=0,
                      xp=0, planes=0, survived=0, ship_id=42):
        return [{"ship_id": ship_id, "pvp": {
            "battles": battles, "wins": wins, "losses": losses, "frags": frags,
            "damage_dealt": damage, "xp": xp, "planes_killed": planes,
            "survived_battles": survived,
        }}]

    def test_first_observation_is_baseline(self):
        result = record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=100, wins=50, frags=80,
                                         damage=1_000_000, xp=80_000,
                                         survived=60),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["events_created"], 0)
        self.assertEqual(result["reason"], "baseline")
        self.assertEqual(BattleObservation.objects.filter(player=self.player).count(), 1)
        self.assertEqual(BattleEvent.objects.filter(player=self.player).count(), 0)

    def test_second_observation_with_advance_emits_event(self):
        # Baseline.
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=100, wins=50, frags=80,
                                         damage=1_000_000, xp=80_000,
                                         survived=60),
        )
        # Player row reflects post-match state — the rollout hook would
        # have run update_player_data first.
        self.player.pvp_battles = 101
        self.player.pvp_wins = 51
        self.player.pvp_frags = 82
        self.player.pvp_survived_battles = 61
        self.player.save()

        result = record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=51, frags=82,
                                         damage=1_048_000, xp=81_500,
                                         survived=61),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["events_created"], 1)

        event = BattleEvent.objects.get(player=self.player)
        self.assertEqual(event.ship_id, 42)
        self.assertEqual(event.ship_name, "Yamato")
        self.assertEqual(event.battles_delta, 1)
        self.assertEqual(event.wins_delta, 1)
        self.assertEqual(event.frags_delta, 2)
        self.assertEqual(event.damage_delta, 48_000)
        self.assertEqual(event.xp_delta, 1_500)
        self.assertTrue(event.survived)

    def test_dedup_via_observation_pair_unique_key(self):
        # Baseline + advance.
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 101
        self.player.save()
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, damage=42_000,
                                         survived=1),
        )
        events_first = BattleEvent.objects.filter(player=self.player).count()

        # Re-running on identical totals adds an observation but no event,
        # because pvp_battles did not advance against the *previous*
        # observation.
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, damage=42_000,
                                         survived=1),
        )
        events_after = BattleEvent.objects.filter(player=self.player).count()
        self.assertEqual(events_first, events_after)

    def test_apply_event_to_daily_summary_is_called_per_event(self):
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 101
        self.player.save()
        with mock.patch(
            "warships.incremental_battles._apply_event_to_daily_summary",
        ) as stub:
            record_observation_from_payloads(
                self.player,
                ship_data=self._ship_payload(battles=101, wins=1, survived=1),
            )
        self.assertEqual(stub.call_count, 1)
        called_arg = stub.call_args.args[0]
        self.assertIsInstance(called_arg, BattleEvent)

    def test_apply_returns_none_when_rollup_flag_off(self):
        # When BATTLE_HISTORY_ROLLUP_ENABLED!=1 the writer short-circuits
        # before touching the event, so a sentinel object that lacks the
        # BattleEvent shape is acceptable.
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_ROLLUP_ENABLED": "0"},
            clear=False,
        ):
            self.assertIsNone(_apply_event_to_daily_summary(object()))


class RecordObservationAndDiffTests(TestCase):
    """End-to-end wrapper: stubs the WG client, exercises the full path."""

    def setUp(self):
        self.player = Player.objects.create(
            name="poc_player", player_id=1031615890, realm="na",
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )

    def test_wraps_record_observation_from_payloads(self):
        player_data = {
            "hidden_profile": False,
            "statistics": {"pvp": {
                "battles": 100, "wins": 50, "losses": 50, "frags": 80,
                "survived_battles": 60,
            }},
        }
        ship_data = [{"ship_id": 99, "pvp": {"battles": 100}}]
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            return_value=player_data,
        ), mock.patch(
            "warships.api.ships._fetch_ship_stats_for_player",
            return_value=ship_data,
        ):
            result = record_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["events_created"], 0)
        self.assertEqual(result["reason"], "baseline")

    def test_player_not_found_short_circuits(self):
        result = record_observation_and_diff(player_id=999999, realm="na")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "player-not-found")

    def test_wg_failure_short_circuits(self):
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            side_effect=RuntimeError("wg flaked"),
        ):
            result = record_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "wg-fetch-failed-or-hidden")


class UpdateBattleDataCaptureHookTests(TestCase):
    """Phase 2: visit-driven refresh writes a BattleObservation iff the
    BATTLE_HISTORY_CAPTURE_ENABLED env flag is set.

    Mocks the WG ship-stats fetch so no network calls fire.
    """

    def setUp(self):
        self.player = Player.objects.create(
            name="capture_hook_player", player_id=8675309, realm="na",
            pvp_battles=200, pvp_wins=110, pvp_losses=90, pvp_frags=160,
            pvp_survived_battles=120,
            battles_json=None,
        )
        self.ship = Ship.objects.create(
            ship_id=4179870672, name="Dalian", nation="pan_asia",
            ship_type="Destroyer", tier=9,
        )
        # Minimal ship payload that update_battle_data needs to materialize
        # battles_json + that the capture hook consumes for the observation.
        self.ship_payload = [{
            "ship_id": 4179870672,
            "battles": 200,
            "distance": 12345,
            "pvp": {
                "battles": 200, "wins": 110, "losses": 90, "frags": 160,
                "damage_dealt": 8_000_000, "xp": 240_000, "planes_killed": 4,
                "survived_battles": 120,
            },
        }]

    def _run_update_battle_data(self):
        from warships.data import update_battle_data
        with mock.patch(
            "warships.data._fetch_ship_stats_for_player",
            return_value=self.ship_payload,
        ), mock.patch(
            "warships.data.update_tiers_data",
        ), mock.patch(
            "warships.data.update_type_data",
        ), mock.patch(
            "warships.data.update_randoms_data",
        ), mock.patch(
            "warships.data.refresh_player_explorer_summary",
        ), mock.patch(
            "warships.data._fetch_ship_info",
            return_value=self.ship,
        ):
            update_battle_data(player_id=self.player.player_id, realm="na")

    def test_hook_is_noop_when_flag_is_off(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_CAPTURE_ENABLED": "0"},
            clear=False,
        ):
            self._run_update_battle_data()
        self.assertEqual(
            BattleObservation.objects.filter(player=self.player).count(),
            0,
            "no observation should be written with the flag off",
        )

    def test_hook_writes_observation_when_flag_is_on(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_CAPTURE_ENABLED": "1"},
            clear=False,
        ):
            self._run_update_battle_data()
        observations = BattleObservation.objects.filter(player=self.player)
        self.assertEqual(observations.count(), 1)
        observation = observations.get()
        self.assertEqual(observation.pvp_battles, 200)
        self.assertEqual(observation.pvp_frags, 160)
        # ships_stats_json is the wider Phase-1 shape: damage/xp/planes/survived.
        self.assertEqual(len(observation.ships_stats_json), 1)
        ship_row = observation.ships_stats_json[0]
        self.assertEqual(ship_row["damage_dealt"], 8_000_000)
        self.assertEqual(ship_row["planes_killed"], 4)

    def test_hook_failure_does_not_raise_into_refresh_path(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_CAPTURE_ENABLED": "1"},
            clear=False,
        ), mock.patch(
            "warships.incremental_battles.record_observation_from_payloads",
            side_effect=RuntimeError("simulated capture bug"),
        ):
            # Must not raise — refresh path stays whole.
            self._run_update_battle_data()
        # And no observation rows because the capture function itself failed.
        self.assertEqual(
            BattleObservation.objects.filter(player=self.player).count(), 0,
        )

    def test_hook_emits_event_on_advance_across_two_visits(self):
        """End-to-end Phase 2 capture: two refreshes with pvp_battles
        advancing between them produces one BattleEvent row."""
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_CAPTURE_ENABLED": "1"},
            clear=False,
        ):
            self._run_update_battle_data()  # baseline
            # Player row reflects post-match state — update_player_data
            # would have refreshed these before update_battle_data fires.
            self.player.pvp_battles = 201
            self.player.pvp_wins = 111
            self.player.pvp_frags = 162
            self.player.pvp_survived_battles = 121
            self.player.save()
            # Updated ship payload: same ship, +1 battle.
            self.ship_payload[0]["pvp"].update({
                "battles": 201, "wins": 111, "losses": 90, "frags": 162,
                "damage_dealt": 8_048_000, "xp": 241_500, "planes_killed": 4,
                "survived_battles": 121,
            })
            self._run_update_battle_data()

        events = BattleEvent.objects.filter(player=self.player)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.battles_delta, 1)
        self.assertEqual(event.wins_delta, 1)
        self.assertEqual(event.frags_delta, 2)
        self.assertEqual(event.damage_delta, 48_000)
        self.assertTrue(event.survived)


class ApplyEventToDailySummaryTests(TestCase):
    """Phase 3 on-write incremental writer."""

    def setUp(self):
        self.player = Player.objects.create(
            name="rollup_test", player_id=11111, realm="na",
            pvp_battles=100,
        )
        self.ship = Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )
        # Two observations to satisfy BattleEvent FKs.
        self.from_obs = BattleObservation.objects.create(
            player=self.player, pvp_battles=100,
        )
        self.to_obs = BattleObservation.objects.create(
            player=self.player, pvp_battles=101,
        )

    def _make_event(self, **overrides):
        defaults = dict(
            player=self.player,
            ship_id=42,
            ship_name="Yamato",
            battles_delta=1,
            wins_delta=1,
            losses_delta=0,
            frags_delta=2,
            damage_delta=48_000,
            xp_delta=1_500,
            planes_killed_delta=0,
            survived=True,
            from_observation=self.from_obs,
            to_observation=self.to_obs,
        )
        defaults.update(overrides)
        return BattleEvent.objects.create(**defaults)

    def test_noop_when_rollup_flag_off(self):
        event = self._make_event()
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_ROLLUP_ENABLED": "0"},
            clear=False,
        ):
            _apply_event_to_daily_summary(event)
        self.assertEqual(PlayerDailyShipStats.objects.count(), 0)

    def test_creates_row_on_first_event_with_flag_on(self):
        event = self._make_event()
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_ROLLUP_ENABLED": "1"},
            clear=False,
        ):
            _apply_event_to_daily_summary(event)
        row = PlayerDailyShipStats.objects.get()
        self.assertEqual(row.battles, 1)
        self.assertEqual(row.wins, 1)
        self.assertEqual(row.frags, 2)
        self.assertEqual(row.damage, 48_000)
        self.assertEqual(row.survived_battles, 1)

    def test_increments_on_second_event_same_day_same_ship(self):
        # Distinct observation pairs to satisfy BattleEvent's unique key.
        third_obs = BattleObservation.objects.create(
            player=self.player, pvp_battles=103,
        )
        first = self._make_event()
        second = self._make_event(
            battles_delta=2, wins_delta=1, losses_delta=1, frags_delta=3,
            damage_delta=92_000, xp_delta=2_700, survived=False,
            from_observation=self.to_obs, to_observation=third_obs,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_ROLLUP_ENABLED": "1"},
            clear=False,
        ):
            _apply_event_to_daily_summary(first)
            _apply_event_to_daily_summary(second)
        row = PlayerDailyShipStats.objects.get()
        self.assertEqual(row.battles, 3)
        self.assertEqual(row.wins, 2)
        self.assertEqual(row.losses, 1)
        self.assertEqual(row.frags, 5)
        self.assertEqual(row.damage, 140_000)
        self.assertEqual(row.survived_battles, 1)


class RebuildDailyShipStatsTests(TestCase):
    """Phase 3 sweeper: cross-validation, idempotency."""

    def setUp(self):
        self.player = Player.objects.create(
            name="sweeper_test", player_id=22222, realm="na",
            pvp_battles=100,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )
        self.target_date = date(2026, 4, 28)
        # Two observations + three events on the target date.
        self.obs_a = BattleObservation.objects.create(
            player=self.player, pvp_battles=100,
        )
        self.obs_b = BattleObservation.objects.create(
            player=self.player, pvp_battles=101,
        )
        self.obs_c = BattleObservation.objects.create(
            player=self.player, pvp_battles=102,
        )
        midday = django_timezone.make_aware(
            datetime(2026, 4, 28, 12, 0, 0),
        )
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            battles_delta=1, wins_delta=1, frags_delta=2,
            damage_delta=48_000, xp_delta=1_500, survived=True,
            from_observation=self.obs_a, to_observation=self.obs_b,
        )
        # Emulate the date by overriding detected_at via update.
        BattleEvent.objects.filter().update(detected_at=midday)
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            battles_delta=1, wins_delta=0, losses_delta=1,
            frags_delta=1, damage_delta=22_000, xp_delta=900,
            survived=False,
            from_observation=self.obs_b, to_observation=self.obs_c,
        )
        BattleEvent.objects.filter(detected_at__gt=midday).update(
            detected_at=midday + timedelta(hours=1),
        )

    def test_rebuild_aggregates_match_event_sums(self):
        result = rebuild_daily_ship_stats_for_date(self.target_date)
        self.assertEqual(result["status"], "completed")
        rows = PlayerDailyShipStats.objects.filter(date=self.target_date)
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.battles, 2)
        self.assertEqual(row.wins, 1)
        self.assertEqual(row.losses, 1)
        self.assertEqual(row.frags, 3)
        self.assertEqual(row.damage, 70_000)
        self.assertEqual(row.xp, 2_400)
        self.assertEqual(row.survived_battles, 1)

    def test_rebuild_is_idempotent(self):
        rebuild_daily_ship_stats_for_date(self.target_date)
        first_count = PlayerDailyShipStats.objects.count()
        first_battles = PlayerDailyShipStats.objects.get(date=self.target_date).battles

        rebuild_daily_ship_stats_for_date(self.target_date)
        second_count = PlayerDailyShipStats.objects.count()
        second_battles = PlayerDailyShipStats.objects.get(date=self.target_date).battles

        self.assertEqual(first_count, second_count)
        self.assertEqual(first_battles, second_battles)

    def test_rebuild_does_not_touch_other_dates(self):
        # Insert an existing row for a different date that should not move.
        canary = PlayerDailyShipStats.objects.create(
            player=self.player, date=date(2026, 4, 27),
            ship_id=42, ship_name="Yamato",
            battles=99, wins=99, frags=99,
        )
        rebuild_daily_ship_stats_for_date(self.target_date)
        canary.refresh_from_db()
        self.assertEqual(canary.battles, 99)


class RebuildManagementCommandTests(TestCase):
    """`python manage.py rebuild_player_daily_ship_stats --since ...`."""

    def setUp(self):
        self.player = Player.objects.create(
            name="cmd_test", player_id=33333, realm="na",
            pvp_battles=100,
        )

    def test_dry_run_does_not_write(self):
        out = StringIO()
        call_command(
            "rebuild_player_daily_ship_stats",
            "--since", "2026-04-28", "--dry-run",
            stdout=out,
        )
        self.assertIn("[dry-run]", out.getvalue())
        self.assertEqual(PlayerDailyShipStats.objects.count(), 0)

    def test_runs_for_single_date_when_until_omitted(self):
        out = StringIO()
        call_command(
            "rebuild_player_daily_ship_stats",
            "--since", "2026-04-28",
            stdout=out,
        )
        self.assertIn("2026-04-28", out.getvalue())


class BattleHistoryEndpointTests(TestCase):
    """Phase 4 read-side API."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.player = Player.objects.create(
            name="api_test", player_id=44444, realm="na",
            pvp_battles=200,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )
        Ship.objects.create(
            ship_id=43, name="Dalian", nation="pan_asia",
            ship_type="Destroyer", tier=9,
        )

    def _seed_daily_rows(self, ships_payload):
        today = django_timezone.now().date()
        rows = []
        for ship_id, payload in ships_payload.items():
            rows.append(PlayerDailyShipStats.objects.create(
                player=self.player,
                date=payload.get("date", today),
                ship_id=ship_id,
                ship_name=payload.get("ship_name", ""),
                battles=payload.get("battles", 0),
                wins=payload.get("wins", 0),
                losses=payload.get("losses", 0),
                frags=payload.get("frags", 0),
                damage=payload.get("damage", 0),
                xp=payload.get("xp", 0),
                planes_killed=payload.get("planes_killed", 0),
                survived_battles=payload.get("survived_battles", 0),
            ))
        return rows

    def test_returns_404_when_api_flag_off(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "0"},
            clear=False,
        ):
            response = self.client.get("/api/player/api_test/battle-history/")
        self.assertEqual(response.status_code, 404)

    def test_returns_payload_when_flag_on_with_data(self):
        self._seed_daily_rows({
            42: {"battles": 6, "wins": 4, "losses": 2, "frags": 12,
                 "damage": 287_400, "survived_battles": 3,
                 "ship_name": "Yamato"},
            43: {"battles": 2, "wins": 1, "losses": 1, "frags": 3,
                 "damage": 95_000, "survived_battles": 1,
                 "ship_name": "Dalian"},
        })
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get("/api/player/api_test/battle-history/?days=7")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["window_days"], 7)
        self.assertEqual(body["totals"]["battles"], 8)
        self.assertEqual(body["totals"]["wins"], 5)
        self.assertEqual(body["totals"]["damage"], 382_400)
        self.assertEqual(body["totals"]["win_rate"], 62.5)

        by_ship = {s["ship_id"]: s for s in body["by_ship"]}
        self.assertEqual(by_ship[42]["battles"], 6)
        self.assertEqual(by_ship[42]["ship_tier"], 10)
        self.assertEqual(by_ship[42]["ship_type"], "Battleship")
        self.assertEqual(by_ship[42]["avg_damage"], 47_900)
        # Sorted by battles desc: Yamato (6) before Dalian (2).
        self.assertEqual(body["by_ship"][0]["ship_id"], 42)
        # Both ships' rows are on the same day → one entry in by_day.
        self.assertEqual(len(body["by_day"]), 1)
        self.assertEqual(body["by_day"][0]["battles"], 8)

    def test_returns_zero_totals_when_player_has_no_events(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get("/api/player/api_test/battle-history/?days=7")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["totals"]["battles"], 0)
        self.assertEqual(body["totals"]["win_rate"], 0.0)
        self.assertEqual(body["by_ship"], [])
        self.assertEqual(body["by_day"], [])

    def test_returns_404_when_player_unknown(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get("/api/player/no_such_player/battle-history/")
        self.assertEqual(response.status_code, 404)

    def test_clamps_days_to_max(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get("/api/player/api_test/battle-history/?days=999")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["window_days"], 30)

    def test_lifetime_delta_per_ship_uses_battles_json(self):
        """Phase 4.6: per-ship lifetime + delta come from Player.battles_json."""
        self.player.pvp_battles = 1000
        self.player.pvp_wins = 530
        self.player.battles_json = [
            {
                "ship_id": 42, "ship_name": "Yamato", "ship_tier": 10,
                "ship_type": "Battleship",
                "pvp_battles": 100, "wins": 56, "losses": 44, "win_ratio": 0.56,
                "kdr": 1.2, "all_battles": 100,
            },
        ]
        self.player.save()
        self._seed_daily_rows({
            42: {"battles": 4, "wins": 1, "losses": 3, "frags": 5,
                 "damage": 180_000, "ship_name": "Yamato"},
        })
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/api_test/battle-history/?days=7",
            )
        self.assertEqual(response.status_code, 200)
        ship = response.json()["by_ship"][0]
        self.assertEqual(ship["lifetime_battles"], 100)
        self.assertEqual(ship["lifetime_win_rate"], 56.0)
        # Period: 1 win in 4 battles. Prior: 55/96 = 57.3%. Delta: 56 - 57.3 = -1.3.
        self.assertEqual(ship["delta_win_rate"], -1.3)

    def test_overall_lifetime_delta_uses_player_aggregates(self):
        """Phase 4.6: totals tile lifetime delta uses player's PvP aggregate."""
        self.player.pvp_battles = 1000
        self.player.pvp_wins = 530
        self.player.battles_json = []
        self.player.save()
        self._seed_daily_rows({
            42: {"battles": 4, "wins": 1, "losses": 3, "ship_name": "Yamato"},
        })
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/api_test/battle-history/?days=7",
            )
        body = response.json()
        # Overall lifetime now: 530/1000 = 53.0%. Prior: 529/996 = 53.1%. Delta: -0.1.
        self.assertEqual(body["totals"]["lifetime_win_rate"], 53.0)
        self.assertEqual(body["totals"]["delta_win_rate"], -0.1)

    def test_lifetime_null_when_battles_json_missing_ship(self):
        self.player.pvp_battles = 0
        self.player.battles_json = None
        self.player.save()
        self._seed_daily_rows({
            42: {"battles": 1, "wins": 1, "ship_name": "Yamato"},
        })
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/api_test/battle-history/?days=7",
            )
        ship = response.json()["by_ship"][0]
        self.assertIsNone(ship["lifetime_win_rate"])
        self.assertIsNone(ship["delta_win_rate"])

    def test_caches_payload(self):
        from django.core.cache import cache
        self._seed_daily_rows({
            42: {"battles": 1, "wins": 1, "ship_name": "Yamato"},
        })
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r1 = self.client.get("/api/player/api_test/battle-history/?days=7")
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r1.json()["totals"]["battles"], 1)

            # Mutate the underlying row; cached response should still reflect old value.
            PlayerDailyShipStats.objects.update(battles=99)
            r2 = self.client.get("/api/player/api_test/battle-history/?days=7")
            self.assertEqual(r2.json()["totals"]["battles"], 1, "cache hit")

            # After cache clear we see the new value.
            cache.clear()
            r3 = self.client.get("/api/player/api_test/battle-history/?days=7")
            self.assertEqual(r3.json()["totals"]["battles"], 99)


class PeriodRollupsTests(TestCase):
    """Phase 6: weekly / monthly / yearly rollups derived from
    PlayerDailyShipStats."""

    def setUp(self):
        self.player = Player.objects.create(
            name="period_test", player_id=55555, realm="na",
            pvp_battles=200,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )
        # Two daily rows on the same week / month / year so we can verify
        # the rollup sums them.
        self.day_a = date(2026, 4, 27)  # Monday
        self.day_b = date(2026, 4, 30)  # Thursday — same ISO week, same month/year
        PlayerDailyShipStats.objects.create(
            player=self.player, date=self.day_a, ship_id=42,
            ship_name="Yamato",
            battles=3, wins=2, losses=1, frags=5,
            damage=180_000, xp=4_500, planes_killed=0,
            survived_battles=1,
            first_event_at=django_timezone.make_aware(datetime(2026, 4, 27, 12, 0)),
            last_event_at=django_timezone.make_aware(datetime(2026, 4, 27, 18, 0)),
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=self.day_b, ship_id=42,
            ship_name="Yamato",
            battles=4, wins=3, losses=1, frags=7,
            damage=240_000, xp=6_200, planes_killed=0,
            survived_battles=2,
            first_event_at=django_timezone.make_aware(datetime(2026, 4, 30, 9, 0)),
            last_event_at=django_timezone.make_aware(datetime(2026, 4, 30, 21, 0)),
        )

    def test_rebuild_period_rollups_writes_weekly_monthly_yearly(self):
        result = rebuild_period_rollups_for_date(self.day_b)
        self.assertEqual(result["status"], "completed")

        # Week: Monday 2026-04-27 sums both days.
        weekly = PlayerWeeklyShipStats.objects.get(
            player=self.player, period_start=date(2026, 4, 27), ship_id=42,
        )
        self.assertEqual(weekly.battles, 7)
        self.assertEqual(weekly.wins, 5)
        self.assertEqual(weekly.frags, 12)
        self.assertEqual(weekly.damage, 420_000)

        # Month: 2026-04-01 sums both days.
        monthly = PlayerMonthlyShipStats.objects.get(
            player=self.player, period_start=date(2026, 4, 1), ship_id=42,
        )
        self.assertEqual(monthly.battles, 7)
        self.assertEqual(monthly.damage, 420_000)

        # Year: 2026-01-01 sums both days.
        yearly = PlayerYearlyShipStats.objects.get(
            player=self.player, period_start=date(2026, 1, 1), ship_id=42,
        )
        self.assertEqual(yearly.battles, 7)
        self.assertEqual(yearly.damage, 420_000)

    def test_rebuild_is_idempotent_at_period_grain(self):
        rebuild_period_rollups_for_date(self.day_b)
        rebuild_period_rollups_for_date(self.day_b)
        # Still exactly one row per (player, period_start, ship_id).
        self.assertEqual(PlayerWeeklyShipStats.objects.count(), 1)
        self.assertEqual(PlayerMonthlyShipStats.objects.count(), 1)
        self.assertEqual(PlayerYearlyShipStats.objects.count(), 1)

    def test_rebuild_does_not_touch_other_periods(self):
        # Insert a canary in a different week.
        canary = PlayerWeeklyShipStats.objects.create(
            player=self.player, period_start=date(2026, 1, 5), ship_id=99,
            ship_name="Other ship", battles=42,
        )
        rebuild_period_rollups_for_date(self.day_b)
        canary.refresh_from_db()
        self.assertEqual(canary.battles, 42)


class BattleHistoryPeriodApiTests(TestCase):
    """Phase 6: API exposes period=daily|weekly|monthly|yearly."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.player = Player.objects.create(
            name="period_api", player_id=66666, realm="na",
            pvp_battles=200,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )

    def test_weekly_period_reads_weekly_table(self):
        # Two weeks ago + this week, both with data.
        today = django_timezone.now().date()
        from warships.incremental_battles import _week_start
        this_week = _week_start(today)
        two_weeks_ago = this_week - timedelta(days=14)
        PlayerWeeklyShipStats.objects.create(
            player=self.player, period_start=this_week, ship_id=42,
            ship_name="Yamato", battles=10, wins=6,
        )
        PlayerWeeklyShipStats.objects.create(
            player=self.player, period_start=two_weeks_ago, ship_id=42,
            ship_name="Yamato", battles=4, wins=2,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/period_api/battle-history/?period=weekly&windows=4",
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["period"], "weekly")
        self.assertEqual(body["windows"], 4)
        # Both weekly rows fall inside a 4-week window.
        self.assertEqual(body["totals"]["battles"], 14)
        # by_day still carries one entry per period bucket.
        self.assertEqual(len(body["by_day"]), 2)

    def test_monthly_period_reads_monthly_table(self):
        today = django_timezone.now().date()
        this_month = today.replace(day=1)
        PlayerMonthlyShipStats.objects.create(
            player=self.player, period_start=this_month, ship_id=42,
            ship_name="Yamato", battles=20, wins=11,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/period_api/battle-history/?period=monthly&windows=3",
            )
        body = response.json()
        self.assertEqual(body["period"], "monthly")
        self.assertEqual(body["totals"]["battles"], 20)

    def test_invalid_period_falls_back_to_daily(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/period_api/battle-history/?period=hourly",
            )
        self.assertEqual(response.json()["period"], "daily")

    def test_legacy_days_param_still_works_for_daily(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/period_api/battle-history/?days=14",
            )
        body = response.json()
        self.assertEqual(body["period"], "daily")
        self.assertEqual(body["windows"], 14)
        self.assertEqual(body["window_days"], 14)

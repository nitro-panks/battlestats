"""Tests for the incremental-battle orchestrator.

Covers two layers:

* Pure functions (`compute_battle_events`, `coerce_observation_payload`,
  `_snapshot_from_player_row`) — exercised without the DB.
* DB-touching orchestrator (`record_observation_from_payloads`,
  `record_observation_and_diff`) — exercised against the test DB with a
  fake `Ship` row + a synthesized `Player` row, no WG calls.
"""

from datetime import datetime, timezone
from unittest import mock

from django.test import TestCase

from warships.incremental_battles import (
    PlayerSnapshot,
    ShipSnapshot,
    _apply_event_to_daily_summary,
    _snapshot_from_player_row,
    coerce_observation_payload,
    compute_battle_events,
    record_observation_and_diff,
    record_observation_from_payloads,
)
from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
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

    def test_stub_apply_is_a_noop(self):
        # Phase 1 contract: the stub does not raise and does not write
        # anywhere. Phase 3 fills it in.
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

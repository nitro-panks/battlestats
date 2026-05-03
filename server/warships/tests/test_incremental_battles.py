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
    RankedShipSeasonSnapshot,
    ShipSnapshot,
    _apply_event_to_daily_summary,
    _coerce_ship_snapshot,
    _hydrate_previous_ranked_snapshot,
    _hydrate_previous_snapshot,
    _ranked_ships_from_iterable,
    _serialize_ranked_ships_payload,
    _serialize_ships_payload,
    _snapshot_from_player_row,
    coerce_observation_payload,
    compute_battle_events,
    compute_ranked_battle_events,
    rebuild_daily_ship_stats_for_date,
    rebuild_period_rollups_for_date,
    record_observation_and_diff,
    record_observation_from_payloads,
    record_ranked_observation_and_diff,
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

    def test_emits_event_when_ships_advance_but_account_pvp_battles_does_not(self):
        """Regression: WG's account/info and ships/stats endpoints don't
        update in lockstep. ships/stats can advance while pvp_battles
        hasn't caught up yet — a per-ship advance must still produce an
        event regardless of the player-aggregate count."""
        before = _make_snapshot(pvp_battles=100, ships={
            42: {"battles": 50, "wins": 25, "frags": 40,
                 "damage_dealt": 1_000_000, "xp": 50_000,
                 "survived_battles": 30},
        })
        # account/info pvp_battles stayed at 100 — pipeline lag — but the
        # per-ship row already shows the next match.
        after = _make_snapshot(pvp_battles=100, pvp_wins=50, ships={
            42: {"battles": 51, "wins": 26, "frags": 42,
                 "damage_dealt": 1_048_000, "xp": 51_500,
                 "survived_battles": 31},
        })
        events = compute_battle_events(before, after)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["battles_delta"], 1)
        self.assertEqual(events[0]["wins_delta"], 1)

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


class RankedDiffTests(TestCase):
    """Pure ranked diff: keyed on (ship_id, season_id), no DB."""

    def test_empty_payloads_produce_no_events(self):
        self.assertEqual(
            compute_ranked_battle_events({}, {}),
            [],
        )

    def test_no_advance_produces_no_events(self):
        snap = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=5, wins=3, losses=2,
                frags=4, damage_dealt=400_000, xp=20_000, survived_battles=3,
            ),
        }
        self.assertEqual(compute_ranked_battle_events(snap, snap), [])

    def test_single_match_advance_emits_event_with_season(self):
        prev = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=5, wins=3, losses=2,
                frags=4, damage_dealt=400_000, xp=20_000, survived_battles=3,
            ),
        }
        curr = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=6, wins=4, losses=2,
                frags=5, damage_dealt=448_000, xp=21_500, survived_battles=4,
            ),
        }
        events = compute_ranked_battle_events(prev, curr)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["ship_id"], 42)
        self.assertEqual(e["season_id"], 22)
        self.assertEqual(e["battles_delta"], 1)
        self.assertEqual(e["wins_delta"], 1)
        self.assertEqual(e["frags_delta"], 1)
        self.assertEqual(e["damage_delta"], 48_000)
        self.assertEqual(e["xp_delta"], 1_500)
        self.assertTrue(e["survived"])

    def test_new_season_ship_attributes_full_battles_as_baseline(self):
        # Player wasn't in season 22 before; new (42, 22) row appears in
        # current. Treated as baseline: delta = current.
        prev = {}
        curr = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=3, wins=2, losses=1,
                frags=4, damage_dealt=200_000, xp=15_000, survived_battles=2,
            ),
        }
        events = compute_ranked_battle_events(prev, curr)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["battles_delta"], 3)
        self.assertEqual(events[0]["season_id"], 22)
        self.assertIsNone(
            events[0]["survived"],
            'multi-match deltas (>1) leave survived attribution NULL',
        )

    def test_off_season_empty_current_yields_no_events(self):
        # Operational watchpoint #1 in the runbook: off-season weeks
        # produce sparse / empty payloads. The diff lane must not crash.
        prev = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=5, wins=3, losses=2,
                frags=4, damage_dealt=400_000, xp=20_000, survived_battles=3,
            ),
        }
        curr = {}
        # No (42, 22) in curr — the diff walks curr keys, so it produces
        # no events. Pre-season ranked stats are not "lost" — they live
        # in the prior observation row and could resurface if the player
        # plays that season-ship pair again later.
        self.assertEqual(compute_ranked_battle_events(prev, curr), [])

    def test_multi_season_concurrent_ships_emit_separate_events(self):
        # Single observation can span multiple active seasons (sprint
        # series, season transitions). Each (ship_id, season_id) pair
        # diffed independently.
        prev = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=5, wins=3, losses=2,
                frags=4, damage_dealt=400_000, xp=20_000, survived_battles=3,
            ),
            (42, 23): RankedShipSeasonSnapshot(
                ship_id=42, season_id=23, battles=2, wins=1, losses=1,
                frags=1, damage_dealt=100_000, xp=8_000, survived_battles=1,
            ),
        }
        curr = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=6, wins=4, losses=2,
                frags=5, damage_dealt=448_000, xp=21_500, survived_battles=4,
            ),
            (42, 23): RankedShipSeasonSnapshot(
                ship_id=42, season_id=23, battles=2, wins=1, losses=1,
                frags=1, damage_dealt=100_000, xp=8_000, survived_battles=1,
            ),
        }
        events = compute_ranked_battle_events(prev, curr)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["season_id"], 22)


class RankedIterableCoercionTests(TestCase):
    """_ranked_ships_from_iterable handles raw WG seasons/shipstats rows."""

    def test_single_ship_multi_season_round_trip(self):
        rows = [{
            "ship_id": 42,
            "seasons": {
                "22": {
                    "battles": 5, "wins": 3, "losses": 2, "frags": 4,
                    "damage_dealt": 400_000, "xp": 20_000,
                    "survived_battles": 3,
                },
                "23": {
                    "battles": 2, "wins": 1, "losses": 1, "frags": 1,
                    "damage_dealt": 100_000, "xp": 8_000,
                    "survived_battles": 1,
                },
            },
        }]
        out = _ranked_ships_from_iterable(rows)
        self.assertEqual(set(out.keys()), {(42, 22), (42, 23)})
        self.assertEqual(out[(42, 22)].battles, 5)
        self.assertEqual(out[(42, 23)].xp, 8_000)

    def test_drops_malformed_rows(self):
        rows = [
            {"ship_id": "not-a-number"},
            {"missing_ship_id": True},
            "not-a-dict",
            {"ship_id": 42, "seasons": "not-a-dict"},
            {"ship_id": 42, "seasons": {"22": "not-a-dict"}},
        ]
        self.assertEqual(_ranked_ships_from_iterable(rows), {})

    def test_empty_input_returns_empty(self):
        self.assertEqual(_ranked_ships_from_iterable([]), {})
        self.assertEqual(_ranked_ships_from_iterable(None), {})

    def test_serialize_round_trip(self):
        snapshot = {
            (42, 22): RankedShipSeasonSnapshot(
                ship_id=42, season_id=22, battles=5, wins=3, losses=2,
                frags=4, damage_dealt=400_000, xp=20_000, survived_battles=3,
            ),
        }
        rows = _serialize_ranked_ships_payload(snapshot)
        rebuilt = _ranked_ships_from_iterable(rows)
        self.assertEqual(rebuilt, snapshot)

    def test_real_wg_payload_shape_aggregates_across_rank_and_div_modes(self):
        """Real WG `seasons/shipstats/` nests stats as
        seasons[id][rank_tier][rank_solo|rank_div2|rank_div3]. The Phase-1
        parser silently read 0 from this shape; the fix aggregates across
        all rank tiers and div modes."""
        rows = [{
            "ship_id": 42,
            "seasons": {
                "1020": {
                    "0": {
                        "rank_solo": {
                            "battles": 3, "wins": 2, "losses": 1,
                            "frags": 2, "damage_dealt": 158_014,
                            "xp": 5_990, "survived_battles": 3,
                        },
                        "rank_div2": None,
                        "rank_div3": None,
                    },
                    "1": {
                        "rank_solo": {
                            "battles": 2, "wins": 1, "losses": 1,
                            "frags": 1, "damage_dealt": 80_000,
                            "xp": 3_000, "survived_battles": 1,
                        },
                        "rank_div2": {
                            "battles": 1, "wins": 1, "losses": 0,
                            "frags": 0, "damage_dealt": 30_000,
                            "xp": 2_000, "survived_battles": 1,
                        },
                        "rank_div3": None,
                    },
                },
            },
        }]
        out = _ranked_ships_from_iterable(rows)
        snap = out[(42, 1020)]
        # Sums across rank_tier 0 + 1 and across rank_solo + rank_div2.
        self.assertEqual(snap.battles, 6)
        self.assertEqual(snap.wins, 4)
        self.assertEqual(snap.losses, 2)
        self.assertEqual(snap.frags, 3)
        self.assertEqual(snap.damage_dealt, 268_014)
        self.assertEqual(snap.xp, 10_990)
        self.assertEqual(snap.survived_battles, 5)


class RankedRecordObservationTests(TestCase):
    """End-to-end: ranked diff lane through record_observation_from_payloads."""

    def setUp(self):
        self.player = Player.objects.create(
            name="ranked_bench", player_id=4242424244, realm="na",
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )
        Ship.objects.create(
            ship_id=99, name="Petropavlovsk", nation="ussr",
            ship_type="Cruiser", tier=10,
        )

    def _baseline_random(self):
        # Establish a baseline so subsequent observations exercise the diff
        # rather than the baseline-skip path.
        record_observation_from_payloads(
            self.player,
            ship_data=[{"ship_id": 99, "pvp": {"battles": 100}}],
        )

    def test_ranked_payload_persists_to_observation(self):
        self._baseline_random()
        ranked_rows = [{
            "ship_id": 99,
            "seasons": {"22": {"battles": 1, "wins": 1, "frags": 1,
                                "damage_dealt": 80_000, "xp": 4_000,
                                "survived_battles": 1}},
        }]
        result = record_observation_from_payloads(
            self.player,
            ship_data=[{"ship_id": 99, "pvp": {"battles": 100}}],
            ranked_ship_data=ranked_rows,
        )
        self.assertEqual(result["status"], "completed")
        # Ranked payload persists to the observation column.
        latest_obs = BattleObservation.objects.filter(
            player=self.player).latest("observed_at")
        self.assertIsNotNone(latest_obs.ranked_ships_stats_json)
        # Broken-prior guard: prior observation had no ranked data
        # (only the random baseline). The current observation is treated
        # as the ranked baseline — no events emitted. The next observation
        # will diff cleanly against this one.
        ranked_evts = BattleEvent.objects.filter(
            player=self.player, mode=BattleEvent.MODE_RANKED)
        self.assertEqual(ranked_evts.count(), 0)
        self.assertEqual(result["random_events_created"], 0)
        self.assertEqual(result["ranked_events_created"], 0)

    def test_ranked_no_advance_produces_no_event_but_persists_payload(self):
        self._baseline_random()
        ranked_rows = [{
            "ship_id": 99,
            "seasons": {"22": {"battles": 5, "wins": 3, "frags": 4,
                                "damage_dealt": 400_000, "xp": 20_000,
                                "survived_battles": 3}},
        }]
        # First observation with ranked: creates baseline event.
        record_observation_from_payloads(
            self.player,
            ship_data=[{"ship_id": 99, "pvp": {"battles": 100}}],
            ranked_ship_data=ranked_rows,
        )
        BattleEvent.objects.filter(
            player=self.player, mode=BattleEvent.MODE_RANKED).delete()
        # Second with identical ranked totals — no advance, no event.
        result = record_observation_from_payloads(
            self.player,
            ship_data=[{"ship_id": 99, "pvp": {"battles": 100}}],
            ranked_ship_data=ranked_rows,
        )
        self.assertEqual(result["ranked_events_created"], 0)
        # Ranked payload must still be persisted on the new observation
        # so it can serve as the prior for the next diff.
        latest_obs = BattleObservation.objects.filter(
            player=self.player).latest("observed_at")
        self.assertIsNotNone(latest_obs.ranked_ships_stats_json)

    def test_ranked_capture_omitted_leaves_column_null(self):
        self._baseline_random()
        # Caller passes nothing for ranked — must NOT default to []; column
        # stays NULL so we can distinguish "ranked capture off" from
        # "ranked capture on but player has no ranked rows".
        record_observation_from_payloads(
            self.player,
            ship_data=[{"ship_id": 99, "pvp": {"battles": 101}}],
        )
        self.player.pvp_battles = 101
        self.player.save()
        latest_obs = BattleObservation.objects.filter(
            player=self.player).latest("observed_at")
        self.assertIsNone(latest_obs.ranked_ships_stats_json)

    def test_ranked_event_does_not_advance_last_random_battle_at(self):
        # Operational watchpoint #4: Active landing pill stays randoms-only.
        self._baseline_random()
        self.player.refresh_from_db()
        prior_lrb = self.player.last_random_battle_at
        ranked_rows = [{
            "ship_id": 99,
            "seasons": {"22": {"battles": 5, "wins": 3,
                                "damage_dealt": 400_000,
                                "survived_battles": 3}},
        }]
        record_observation_from_payloads(
            self.player,
            ship_data=[{"ship_id": 99, "pvp": {"battles": 100}}],
            ranked_ship_data=ranked_rows,
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.last_random_battle_at, prior_lrb,
                         'ranked-only events must not bump '
                         'last_random_battle_at')


class LastRandomBattleAtTests(TestCase):
    """Player.last_random_battle_at is the timestamp driving the landing
    'Active' sub-sort. It must:
      - start NULL on a fresh player,
      - advance to the latest event's detected_at when events are written,
      - stay untouched when an observation produces zero events,
      - never regress (Greatest() guard against concurrent writers).
    """

    def setUp(self):
        self.player = Player.objects.create(
            name="active_bench", player_id=4242424243, realm="na",
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )
        Ship.objects.create(
            ship_id=43, name="Iowa", nation="usa", ship_type="Battleship",
            tier=9,
        )

    def _ship_payload(self, *, battles, wins=0, frags=0, damage=0, xp=0,
                      planes=0, survived=0, ship_id=43):
        return [{"ship_id": ship_id, "pvp": {
            "battles": battles, "wins": wins, "losses": 0, "frags": frags,
            "damage_dealt": damage, "xp": xp, "planes_killed": planes,
            "survived_battles": survived,
        }}]

    def test_column_starts_null_on_fresh_player(self):
        self.player.refresh_from_db()
        self.assertIsNone(self.player.last_random_battle_at)

    def test_baseline_observation_does_not_set_column(self):
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.refresh_from_db()
        self.assertIsNone(self.player.last_random_battle_at)

    def test_first_event_observation_sets_column_to_event_time(self):
        # Baseline.
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 101
        self.player.pvp_wins = 51
        self.player.pvp_frags = 82
        self.player.pvp_survived_battles = 61
        self.player.save()
        # Advance.
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, frags=2,
                                         damage=48_000, xp=1_500, survived=1),
        )
        self.player.refresh_from_db()
        self.assertIsNotNone(self.player.last_random_battle_at)

        event = BattleEvent.objects.get(player=self.player)
        self.assertEqual(self.player.last_random_battle_at, event.detected_at)

    def test_zero_event_observation_leaves_column_untouched(self):
        # Baseline + first advance to populate the column.
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 101
        self.player.save()
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, survived=1),
        )
        self.player.refresh_from_db()
        first_value = self.player.last_random_battle_at
        self.assertIsNotNone(first_value)

        # Identical totals — no positive delta, no event written.
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, survived=1),
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.last_random_battle_at, first_value)

    def test_column_never_regresses(self):
        # Set the column to a future time (simulating a concurrent writer
        # that observed a later event first); the conditional UPDATE must
        # keep the later value when our update tries to write an earlier
        # one. Note: refresh_from_db before save() is required because the
        # in-memory model copy from setUp doesn't have the QuerySet.update
        # value, and a full save() would overwrite it.
        future = django_timezone.now() + timedelta(hours=1)
        Player.objects.filter(pk=self.player.pk).update(
            last_random_battle_at=future,
        )
        # Baseline (no events).
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.refresh_from_db()
        self.player.pvp_battles = 101
        self.player.save()
        # Advance — the new event's detected_at is ~now, strictly less
        # than `future`. Our conditional UPDATE should not fire.
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, survived=1),
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.last_random_battle_at, future)


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


class QueueRankedObservationRefreshTests(TestCase):
    """Lock-aware-gate dispatcher for the on-render ranked refresh."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.player = Player.objects.create(
            name="dispatcher_test", player_id=30003000, realm="na",
        )

    def test_queues_when_not_pending(self):
        from warships.tasks import (
            is_ranked_observation_refresh_pending,
            queue_ranked_observation_refresh,
        )
        with mock.patch(
            "warships.tasks.refresh_ranked_observation_task.delay",
        ) as fake_delay:
            result = queue_ranked_observation_refresh(
                self.player.player_id, realm="na")
        self.assertEqual(result["status"], "queued")
        fake_delay.assert_called_once_with(
            player_id=self.player.player_id, realm="na")
        self.assertTrue(is_ranked_observation_refresh_pending(
            self.player.player_id, realm="na"))

    def test_dedup_short_circuits_subsequent_dispatches(self):
        from warships.tasks import queue_ranked_observation_refresh
        with mock.patch(
            "warships.tasks.refresh_ranked_observation_task.delay",
        ) as fake_delay:
            first = queue_ranked_observation_refresh(
                self.player.player_id, realm="na")
            second = queue_ranked_observation_refresh(
                self.player.player_id, realm="na")
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["reason"], "already-queued")
        # Only the first call enqueued.
        self.assertEqual(fake_delay.call_count, 1)

    def test_broker_failure_puts_dispatch_in_cooldown_and_clears_dedup(self):
        from django.core.cache import cache
        from warships.tasks import (
            _ranked_observation_refresh_dispatch_key,
            _ranked_observation_refresh_failure_key,
            queue_ranked_observation_refresh,
        )
        with mock.patch(
            "warships.tasks.refresh_ranked_observation_task.delay",
            side_effect=RuntimeError("broker down"),
        ):
            result = queue_ranked_observation_refresh(
                self.player.player_id, realm="na")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "enqueue-failed")
        # Dedup cleared so a future dispatch (post-cooldown) can retry.
        self.assertIsNone(cache.get(
            _ranked_observation_refresh_dispatch_key(
                self.player.player_id, realm="na"),
        ))
        # Cooldown set so subsequent dispatchers short-circuit fast.
        self.assertTrue(cache.get(
            _ranked_observation_refresh_failure_key(realm="na"),
        ))

    def test_skipped_during_broker_cooldown(self):
        from django.core.cache import cache
        from warships.tasks import (
            _ranked_observation_refresh_failure_key,
            queue_ranked_observation_refresh,
        )
        cache.set(
            _ranked_observation_refresh_failure_key(realm="na"),
            True, timeout=60,
        )
        with mock.patch(
            "warships.tasks.refresh_ranked_observation_task.delay",
        ) as fake_delay:
            result = queue_ranked_observation_refresh(
                self.player.player_id, realm="na")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "broker-unavailable")
        fake_delay.assert_not_called()


class RecordRankedObservationAndDiffTests(TestCase):
    """Phase 6: 3-call wrapper used by `establish_ranked_baseline`."""

    def setUp(self):
        self.player = Player.objects.create(
            name="ranked_seed_player", player_id=20002000, realm="na",
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )
        self.player_data = {
            "hidden_profile": False,
            "statistics": {"pvp": {
                "battles": 100, "wins": 50, "losses": 50, "frags": 80,
                "survived_battles": 60,
            }},
        }
        self.ship_data = [{"ship_id": 99, "pvp": {"battles": 100}}]

    def test_writes_observation_with_both_random_and_ranked_payloads(self):
        ranked_data = [{
            "ship_id": 4_182_799_888,
            "seasons": {
                "21": {"battles": 12, "wins": 7, "losses": 5,
                       "damage_dealt": 360_000, "frags": 14, "xp": 8_400,
                       "survived_battles": 6},
            },
        }]
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            return_value=self.player_data,
        ), mock.patch(
            "warships.api.ships._fetch_ship_stats_for_player",
            return_value=self.ship_data,
        ), mock.patch(
            "warships.api.ships._fetch_ranked_ship_stats_for_player",
            return_value=ranked_data,
        ):
            result = record_ranked_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reason"], "baseline")
        obs = BattleObservation.objects.get(player=self.player)
        self.assertTrue(obs.ships_stats_json)
        self.assertTrue(obs.ranked_ships_stats_json)
        self.assertEqual(len(obs.ranked_ships_stats_json), 1)

    def test_writes_observation_when_seasons_shipstats_returns_empty(self):
        # Off-season case: WG returns []. The observation still writes
        # so a future visit can diff against a known-empty prior.
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            return_value=self.player_data,
        ), mock.patch(
            "warships.api.ships._fetch_ship_stats_for_player",
            return_value=self.ship_data,
        ), mock.patch(
            "warships.api.ships._fetch_ranked_ship_stats_for_player",
            return_value=[],
        ):
            result = record_ranked_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "completed")
        obs = BattleObservation.objects.get(player=self.player)
        # Empty (not None) ranked payload — so the next diff knows
        # "we asked WG and they had nothing" vs "we never asked".
        self.assertEqual(obs.ranked_ships_stats_json, [])

    def test_seasons_shipstats_failure_writes_null_not_empty(self):
        # When seasons/shipstats fetch raises (e.g. WG 407), the random
        # baseline still writes but ranked is recorded as NULL — distinct
        # from `[]` (a legitimate "fetched, player has no ranked play"
        # baseline). NULL drives the diff lane's walk-back behavior.
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            return_value=self.player_data,
        ), mock.patch(
            "warships.api.ships._fetch_ship_stats_for_player",
            return_value=self.ship_data,
        ), mock.patch(
            "warships.api.ships._fetch_ranked_ship_stats_for_player",
            side_effect=RuntimeError("wg ranked flaked"),
        ):
            result = record_ranked_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "completed")
        obs = BattleObservation.objects.get(player=self.player)
        self.assertIsNone(obs.ranked_ships_stats_json)

    def test_broken_ranked_prior_treats_current_as_baseline_no_events(self):
        # Player has only NULL ranked observations in their history.
        # Current observation has substantial ranked data. Without the
        # broken-prior guard, the diff lane would attribute the entire
        # ranked career as today's events. With the guard, current obs
        # is treated as a baseline and no ranked events emit.
        from warships.models import BattleObservation, BattleEvent
        BattleObservation.objects.filter(player=self.player).delete()
        # Pre-existing observation with NULL ranked.
        BattleObservation.objects.create(
            player=self.player, pvp_battles=99,
            ships_stats_json=[], ranked_ships_stats_json=None,
        )
        ranked_data = [{
            "ship_id": 4001,
            "seasons": {
                "21": {"battles": 50, "wins": 30, "losses": 20,
                       "damage_dealt": 1_000_000, "frags": 70, "xp": 15_000,
                       "survived_battles": 30},
            },
        }]
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            return_value=self.player_data,
        ), mock.patch(
            "warships.api.ships._fetch_ship_stats_for_player",
            return_value=self.ship_data,
        ), mock.patch(
            "warships.api.ships._fetch_ranked_ship_stats_for_player",
            return_value=ranked_data,
        ):
            result = record_ranked_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "completed")
        # Critical: 0 events even though current ranked has 50 battles —
        # broken prior guard kicks in.
        self.assertEqual(result.get("ranked_events_created"), 0)
        self.assertEqual(
            BattleEvent.objects.filter(
                player=self.player, mode="ranked").count(), 0)

    def test_broken_random_prior_treats_current_as_baseline_no_events(self):
        # Player has a previous observation with pvp_battles>0 but
        # ships_stats_json=[] (e.g. a flaked ships/stats fetch). Current
        # observation has full per-ship data. Without the guard, the diff
        # lane would attribute every ship's lifetime battles as today's
        # events. With the guard, treated as baseline; no events.
        from warships.models import BattleObservation, BattleEvent
        BattleObservation.objects.filter(player=self.player).delete()
        BattleObservation.objects.create(
            player=self.player, pvp_battles=100, pvp_wins=50, pvp_losses=50,
            pvp_frags=80, pvp_survived_battles=60,
            ships_stats_json=[],  # broken — empty per-ship snapshot
            ranked_ships_stats_json=None,
        )
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            return_value=self.player_data,
        ), mock.patch(
            "warships.api.ships._fetch_ship_stats_for_player",
            return_value=[{"ship_id": 99, "pvp": {"battles": 100, "wins": 50,
                                                  "frags": 80,
                                                  "survived_battles": 60}}],
        ), mock.patch(
            "warships.api.ships._fetch_ranked_ship_stats_for_player",
            return_value=[],
        ):
            result = record_ranked_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "completed")
        # Random side suppressed by broken-prior guard.
        self.assertEqual(result.get("random_events_created"), 0)
        self.assertEqual(
            BattleEvent.objects.filter(
                player=self.player, mode="random").count(), 0)

    def test_walk_back_uses_last_nonnull_when_latest_obs_is_null(self):
        # Seed three observations:
        #   obs1 (oldest): non-empty ranked payload  → diff lane should
        #                 land on this as the "previous"
        #   obs2:          NULL ranked (fetch failed)
        #   obs3:          NULL ranked (fetch failed)
        # Then run a fourth ingestion with non-empty ranked payload that
        # SHOULD diff against obs1 (most recent non-NULL), not obs3.
        from warships.models import BattleObservation, BattleEvent
        BattleObservation.objects.filter(player=self.player).delete()
        # obs1 — earliest, with a baseline ranked payload (5 battles).
        obs1 = BattleObservation.objects.create(
            player=self.player, pvp_battles=99,
            ships_stats_json=[],
            ranked_ships_stats_json=[{
                "ship_id": 4001,
                "seasons": {
                    "21": {"battles": 5, "wins": 3, "losses": 2,
                           "damage_dealt": 100_000, "frags": 7, "xp": 1_500,
                           "survived_battles": 3},
                },
            }],
        )
        # obs2 + obs3 — NULL ranked (failed fetches).
        obs2 = BattleObservation.objects.create(
            player=self.player, pvp_battles=100,
            ships_stats_json=[], ranked_ships_stats_json=None,
        )
        obs3 = BattleObservation.objects.create(
            player=self.player, pvp_battles=100,
            ships_stats_json=[], ranked_ships_stats_json=None,
        )
        # Backdate observed_at so obs1 < obs2 < obs3 chronologically.
        from datetime import timedelta
        from django.utils import timezone as dj_tz
        BattleObservation.objects.filter(pk=obs1.pk).update(
            observed_at=dj_tz.now() - timedelta(hours=3))
        BattleObservation.objects.filter(pk=obs2.pk).update(
            observed_at=dj_tz.now() - timedelta(hours=2))
        BattleObservation.objects.filter(pk=obs3.pk).update(
            observed_at=dj_tz.now() - timedelta(hours=1))

        # obs4: WG returns non-empty ranked with battles=8 — that's +3
        # battles since the obs1 baseline (5).
        ranked_data = [{
            "ship_id": 4001,
            "seasons": {
                "21": {"battles": 8, "wins": 5, "losses": 3,
                       "damage_dealt": 200_000, "frags": 11, "xp": 2_400,
                       "survived_battles": 5},
            },
        }]
        with mock.patch(
            "warships.api.players._fetch_player_personal_data",
            return_value=self.player_data,
        ), mock.patch(
            "warships.api.ships._fetch_ship_stats_for_player",
            return_value=self.ship_data,
        ), mock.patch(
            "warships.api.ships._fetch_ranked_ship_stats_for_player",
            return_value=ranked_data,
        ):
            result = record_ranked_observation_and_diff(
                player_id=self.player.player_id, realm="na",
            )
        self.assertEqual(result["status"], "completed")
        # Critical: the diff lane should attribute +3 battles, not +8.
        # +8 would be the bug where NULL was treated as "no prior data".
        self.assertEqual(result.get("ranked_events_created"), 1)
        ev = BattleEvent.objects.filter(
            player=self.player, mode="ranked",
        ).order_by("-detected_at").first()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.battles_delta, 3)
        self.assertEqual(ev.season_id, 21)


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
        # USE_TZ=False project — pass naive datetimes; the SQLite adapter
        # rejects tz-aware values (which a stray make_aware() wrapper used
        # to silently produce, breaking the test under SQLite).
        midday = datetime(2026, 4, 28, 12, 0, 0)
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


class RankedRollupWriteTests(TestCase):
    """Phase 3 ranked rollout: random + ranked partitions in `PlayerDailyShipStats`.

    Covers the on-write incremental writer, the rebuild sweeper, and the
    period-table aggregator under the new mode + season_id partitioning.
    """

    def setUp(self):
        self.player = Player.objects.create(
            name="ranked_rollup_test", player_id=55555, realm="na",
            pvp_battles=100,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )
        # Use today's date so `BattleEvent.detected_at` (auto_now_add)
        # naturally lands on the target without a manual update — sidesteps
        # the SQLite USE_TZ=False adaptation gap that the existing
        # `RebuildDailyShipStatsTests` runs into.
        self.target_date = django_timezone.now().date()

    def _make_obs_pair(self, *, base_battles):
        a = BattleObservation.objects.create(
            player=self.player, pvp_battles=base_battles,
        )
        b = BattleObservation.objects.create(
            player=self.player, pvp_battles=base_battles + 1,
        )
        return a, b

    def test_random_and_ranked_events_same_day_same_ship_write_separate_rows(self):
        obs_r1, obs_r2 = self._make_obs_pair(base_battles=100)
        obs_k1, obs_k2 = self._make_obs_pair(base_battles=200)
        random_event = BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANDOM,
            battles_delta=1, wins_delta=1, frags_delta=2,
            damage_delta=48_000, xp_delta=1_500, survived=True,
            from_observation=obs_r1, to_observation=obs_r2,
        )
        ranked_event = BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANKED, season_id=21,
            battles_delta=2, wins_delta=2, frags_delta=3,
            damage_delta=80_000, xp_delta=2_400, survived=False,
            from_observation=obs_k1, to_observation=obs_k2,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_ROLLUP_ENABLED": "1"},
            clear=False,
        ):
            _apply_event_to_daily_summary(random_event)
            _apply_event_to_daily_summary(ranked_event)

        rows = PlayerDailyShipStats.objects.filter(
            player=self.player, ship_id=42,
        )
        self.assertEqual(rows.count(), 2)
        random_row = rows.get(mode=PlayerDailyShipStats.MODE_RANDOM)
        ranked_row = rows.get(mode=PlayerDailyShipStats.MODE_RANKED)
        self.assertIsNone(random_row.season_id)
        self.assertEqual(random_row.battles, 1)
        self.assertEqual(random_row.frags, 2)
        self.assertEqual(random_row.damage, 48_000)
        self.assertEqual(random_row.survived_battles, 1)
        self.assertEqual(ranked_row.season_id, 21)
        self.assertEqual(ranked_row.battles, 2)
        self.assertEqual(ranked_row.frags, 3)
        self.assertEqual(ranked_row.damage, 80_000)
        self.assertEqual(ranked_row.survived_battles, 0)

    def test_multi_season_ranked_writes_separate_rows_per_season(self):
        obs_a, obs_b = self._make_obs_pair(base_battles=300)
        obs_c, obs_d = self._make_obs_pair(base_battles=400)
        season21 = BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANKED, season_id=21,
            battles_delta=1, wins_delta=1, damage_delta=20_000,
            xp_delta=600, survived=True,
            from_observation=obs_a, to_observation=obs_b,
        )
        season22 = BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANKED, season_id=22,
            battles_delta=3, wins_delta=2, damage_delta=72_000,
            xp_delta=2_100, survived=False,
            from_observation=obs_c, to_observation=obs_d,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_ROLLUP_ENABLED": "1"},
            clear=False,
        ):
            _apply_event_to_daily_summary(season21)
            _apply_event_to_daily_summary(season22)

        rows = PlayerDailyShipStats.objects.filter(
            player=self.player, ship_id=42,
            mode=PlayerDailyShipStats.MODE_RANKED,
        ).order_by("season_id")
        self.assertEqual(rows.count(), 2)
        self.assertEqual([r.season_id for r in rows], [21, 22])
        self.assertEqual([r.battles for r in rows], [1, 3])
        self.assertEqual([r.damage for r in rows], [20_000, 72_000])

    def test_rebuild_preserves_random_and_ranked_partitions(self):
        obs_r1, obs_r2 = self._make_obs_pair(base_battles=100)
        obs_k1, obs_k2 = self._make_obs_pair(base_battles=200)
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANDOM,
            battles_delta=1, wins_delta=1, frags_delta=2,
            damage_delta=48_000, xp_delta=1_500, survived=True,
            from_observation=obs_r1, to_observation=obs_r2,
        )
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANKED, season_id=21,
            battles_delta=3, wins_delta=2, frags_delta=4,
            damage_delta=99_000, xp_delta=2_700, survived=False,
            from_observation=obs_k1, to_observation=obs_k2,
        )

        rebuild_daily_ship_stats_for_date(self.target_date)

        rows = PlayerDailyShipStats.objects.filter(
            player=self.player, date=self.target_date, ship_id=42,
        )
        self.assertEqual(rows.count(), 2)
        random_row = rows.get(mode=PlayerDailyShipStats.MODE_RANDOM)
        ranked_row = rows.get(mode=PlayerDailyShipStats.MODE_RANKED)
        self.assertIsNone(random_row.season_id)
        self.assertEqual(random_row.battles, 1)
        self.assertEqual(random_row.damage, 48_000)
        self.assertEqual(ranked_row.season_id, 21)
        self.assertEqual(ranked_row.battles, 3)
        self.assertEqual(ranked_row.damage, 99_000)

    def test_period_rollup_query_ignores_ranked_rows(self):
        # Pre-seed daily rows: one random + one ranked on the same date,
        # then rebuild the period tier and confirm only the random row
        # contributed to the weekly aggregate.
        PlayerDailyShipStats.objects.create(
            player=self.player, date=self.target_date,
            ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=5, wins=3, losses=2, frags=7,
            damage=200_000, xp=4_000, survived_battles=3,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=self.target_date,
            ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=10, wins=8, losses=2, frags=14,
            damage=400_000, xp=9_000, survived_battles=8,
        )

        rebuild_period_rollups_for_date(self.target_date)

        weekly = PlayerWeeklyShipStats.objects.filter(
            player=self.player, ship_id=42,
        )
        self.assertEqual(weekly.count(), 1)
        wk = weekly.get()
        self.assertEqual(wk.battles, 5)
        self.assertEqual(wk.wins, 3)
        self.assertEqual(wk.frags, 7)
        self.assertEqual(wk.damage, 200_000)
        self.assertEqual(wk.survived_battles, 3)


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


class EstablishBattleHistoryBaselineCommandTests(TestCase):
    """`python manage.py establish_battle_history_baseline`.

    Targets active visible players with no BattleObservation; bypasses the
    15-min staleness gate that update_battle_data normally enforces."""

    def setUp(self):
        self.today = django_timezone.now().date()

    def _make(self, name, *, days_idle, is_hidden=False, realm="na",
              has_observation=False, **kwargs):
        player = Player.objects.create(
            name=name,
            player_id=kwargs.pop("player_id", abs(hash(name)) % (10 ** 9)),
            realm=realm,
            is_hidden=is_hidden,
            last_battle_date=self.today - timedelta(days=days_idle),
            **kwargs,
        )
        if has_observation:
            BattleObservation.objects.create(
                player=player, pvp_battles=getattr(player, "pvp_battles", 0) or 0,
            )
        return player

    def test_dry_run_reports_count_without_calling_wg(self):
        self._make("ActiveNoBaseline", days_idle=2)
        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_battle_history_baseline",
                "--realm", "na", "--days", "7", "--dry-run",
                stdout=out,
            )
        self.assertIn("1 candidates", out.getvalue())
        wg_call.assert_not_called()

    def test_skips_players_with_existing_baseline(self):
        self._make("AlreadyBaselined", days_idle=2, has_observation=True)
        out = StringIO()
        call_command(
            "establish_battle_history_baseline",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_skips_hidden_players(self):
        self._make("HiddenActive", days_idle=2, is_hidden=True)
        out = StringIO()
        call_command(
            "establish_battle_history_baseline",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_skips_players_outside_activity_window(self):
        self._make("Idle60d", days_idle=60)
        out = StringIO()
        call_command(
            "establish_battle_history_baseline",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_skips_other_realms(self):
        self._make("EuActive", days_idle=2, realm="eu")
        out = StringIO()
        call_command(
            "establish_battle_history_baseline",
            "--realm", "na", "--days", "7", "--dry-run",
            stdout=out,
        )
        self.assertIn("0 candidates", out.getvalue())

    def test_invokes_record_observation_and_diff_for_each_candidate(self):
        self._make("Active1", days_idle=1)
        self._make("Active2", days_idle=3)
        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
            return_value={"status": "completed", "events_created": 0,
                          "reason": "baseline"},
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_battle_history_baseline",
                "--realm", "na", "--days", "7", "--delay", "0",
                stdout=out,
            )
        self.assertEqual(wg_call.call_count, 2)
        # `realm=na` keyword is passed through.
        for call in wg_call.call_args_list:
            self.assertEqual(call.kwargs.get("realm"), "na")
        self.assertIn("baseline=2", out.getvalue())

    def test_limit_caps_processing(self):
        for i in range(5):
            self._make(f"ActivePlayer{i}", days_idle=i + 1)
        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
            return_value={"status": "completed", "reason": "baseline"},
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_battle_history_baseline",
                "--realm", "na", "--days", "7",
                "--limit", "2", "--delay", "0",
                stdout=out,
            )
        self.assertEqual(wg_call.call_count, 2)
        self.assertIn("limited to 2", out.getvalue())

    def test_handles_wg_fetch_failure_without_aborting(self):
        self._make("Active1", days_idle=1)
        self._make("Active2", days_idle=2)
        with mock.patch(
            "warships.incremental_battles.record_observation_and_diff",
            side_effect=[
                {"status": "skipped", "reason": "wg-fetch-failed-or-hidden"},
                {"status": "completed", "reason": "baseline"},
            ],
        ) as wg_call:
            out = StringIO()
            call_command(
                "establish_battle_history_baseline",
                "--realm", "na", "--days", "7", "--delay", "0",
                stdout=out,
            )
        self.assertEqual(wg_call.call_count, 2)
        self.assertIn("wg_failed=1", out.getvalue())
        self.assertIn("baseline=1", out.getvalue())


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
                mode=payload.get("mode", PlayerDailyShipStats.MODE_RANDOM),
                season_id=payload.get("season_id"),
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

    def test_default_mode_returns_only_random_rows(self):
        # One random row + one ranked row for the same ship on the same
        # day. Default mode (random) must hide the ranked row.
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=4, wins=3, damage=100_000,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=10, wins=7, damage=400_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get("/api/player/api_test/battle-history/?days=7")
        body = r.json()
        self.assertEqual(body["mode"], "random")
        self.assertEqual(body["totals"]["battles"], 4)
        self.assertEqual(body["totals"]["damage"], 100_000)

    def test_mode_ranked_returns_only_ranked_rows(self):
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=4, wins=3, damage=100_000,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=10, wins=7, damage=400_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=ranked",
            )
        body = r.json()
        self.assertEqual(body["mode"], "ranked")
        self.assertEqual(body["totals"]["battles"], 10)
        self.assertEqual(body["totals"]["damage"], 400_000)
        # Lifetime suppression: ranked baseline isn't randoms-derived.
        self.assertIsNone(body["totals"]["lifetime_battles"])
        self.assertIsNone(body["totals"]["lifetime_win_rate"])
        self.assertIsNone(body["totals"]["delta_win_rate"])

    def test_mode_ranked_sums_across_seasons(self):
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=3, wins=2, damage=60_000,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=22,
            battles=5, wins=4, damage=120_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=ranked",
            )
        body = r.json()
        self.assertEqual(body["totals"]["battles"], 8)
        self.assertEqual(body["totals"]["damage"], 180_000)

    def test_mode_combined_sums_random_and_ranked(self):
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=4, wins=3, damage=100_000,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=10, wins=7, damage=400_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=combined",
            )
        body = r.json()
        self.assertEqual(body["mode"], "combined")
        self.assertEqual(body["totals"]["battles"], 14)
        self.assertEqual(body["totals"]["damage"], 500_000)
        # Combined view also suppresses lifetime delta — randoms-only baseline.
        self.assertIsNone(body["totals"]["lifetime_win_rate"])

    def test_invalid_mode_falls_back_to_default(self):
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=4, wins=3,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=bogus",
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["mode"], "random")
        self.assertEqual(body["totals"]["battles"], 4)

    def test_cache_key_isolates_modes(self):
        from django.core.cache import cache
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=4, wins=3,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=10, wins=7,
        )
        cache.clear()
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            random_resp = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=random",
            )
            ranked_resp = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=ranked",
            )
        # Distinct cache keys → distinct totals served from cache.
        self.assertEqual(random_resp.json()["totals"]["battles"], 4)
        self.assertEqual(ranked_resp.json()["totals"]["battles"], 10)

    def test_pending_header_set_when_ranked_observation_refresh_in_flight(self):
        from django.core.cache import cache
        from warships.tasks import _ranked_observation_refresh_dispatch_key
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=10, wins=7,
        )
        # Simulate a refresh just dispatched for this player.
        cache.set(
            _ranked_observation_refresh_dispatch_key(
                self.player.player_id, realm="na",
            ),
            "queued", timeout=300,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=ranked",
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["X-Ranked-Observation-Pending"], "true")

    def test_pending_header_absent_for_random_mode(self):
        from django.core.cache import cache
        from warships.tasks import _ranked_observation_refresh_dispatch_key
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=4, wins=3,
        )
        cache.set(
            _ranked_observation_refresh_dispatch_key(
                self.player.player_id, realm="na",
            ),
            "queued", timeout=300,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=random",
            )
        # mode=random is randoms-only and unaffected by ranked refresh —
        # no pending header so the frontend doesn't poll unnecessarily.
        self.assertNotIn("X-Ranked-Observation-Pending", r)


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
            first_event_at=datetime(2026, 4, 27, 12, 0),
            last_event_at=datetime(2026, 4, 27, 18, 0),
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=self.day_b, ship_id=42,
            ship_name="Yamato",
            battles=4, wins=3, losses=1, frags=7,
            damage=240_000, xp=6_200, planes_killed=0,
            survived_battles=2,
            first_event_at=datetime(2026, 4, 30, 9, 0),
            last_event_at=datetime(2026, 4, 30, 21, 0),
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


# Phase 7 — gunnery / torpedoes / spotting / caps capture widening.
PHASE7_SHIP_PAYLOAD = {
    "main_battery": {"shots": 320, "hits": 90, "frags": 4},
    "second_battery": {"shots": 80, "hits": 20, "frags": 1},
    "torpedoes": {"shots": 18, "hits": 6, "frags": 2},
    "damage_scouting": 45_000,
    "ships_spotted": 7,
    "capture_points": 50,
    "dropped_capture_points": 30,
    "team_capture_points": 200,
}


class Phase7CoercionTests(TestCase):
    """`_coerce_ship_snapshot` reads the widened pvp block defensively."""

    def test_missing_nested_objects_default_to_zero(self):
        # Ship has no torpedoes and no secondary battery (e.g. some BBs).
        # `_coerce_ship_snapshot` must treat the missing nested objects as
        # zeros, not raise.
        ship = {"ship_id": 99, "pvp": {"battles": 10, "main_battery": None}}
        snap = _coerce_ship_snapshot(ship)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.main_shots, 0)
        self.assertEqual(snap.torpedo_hits, 0)
        self.assertEqual(snap.secondary_frags, 0)
        self.assertEqual(snap.damage_scouting, 0)
        self.assertEqual(snap.capture_points, 0)

    def test_full_nested_payload_extracted(self):
        ship = {"ship_id": 99, "pvp": {"battles": 10, **PHASE7_SHIP_PAYLOAD}}
        snap = _coerce_ship_snapshot(ship)
        self.assertEqual(snap.main_shots, 320)
        self.assertEqual(snap.main_hits, 90)
        self.assertEqual(snap.main_frags, 4)
        self.assertEqual(snap.secondary_shots, 80)
        self.assertEqual(snap.torpedo_shots, 18)
        self.assertEqual(snap.torpedo_hits, 6)
        self.assertEqual(snap.torpedo_frags, 2)
        self.assertEqual(snap.damage_scouting, 45_000)
        self.assertEqual(snap.ships_spotted, 7)
        self.assertEqual(snap.capture_points, 50)
        self.assertEqual(snap.dropped_capture_points, 30)
        self.assertEqual(snap.team_capture_points, 200)


class Phase7ComputeBattleEventsTests(TestCase):
    """`compute_battle_events` emits the 14 widened delta keys."""

    def _phase7_snapshot(self, *, battles, ship_id=42, **kwargs):
        ship = ShipSnapshot(
            ship_id=ship_id,
            battles=battles,
            wins=kwargs.pop("wins", 0),
            losses=kwargs.pop("losses", 0),
            frags=kwargs.pop("frags", 0),
            damage_dealt=kwargs.pop("damage_dealt", 0),
            xp=kwargs.pop("xp", 0),
            planes_killed=0,
            survived_battles=kwargs.pop("survived_battles", 0),
            **kwargs,
        )
        return PlayerSnapshot(
            pvp_battles=battles, pvp_wins=0, pvp_losses=0, pvp_frags=0,
            pvp_survived_battles=0,
            last_battle_time=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
            ships={ship_id: ship},
        )

    def test_diff_computes_all_phase7_deltas(self):
        before = self._phase7_snapshot(
            battles=10,
            main_shots=300, main_hits=80, main_frags=3,
            secondary_shots=70, secondary_hits=15, secondary_frags=1,
            torpedo_shots=12, torpedo_hits=4, torpedo_frags=1,
            damage_scouting=40_000, ships_spotted=5,
            capture_points=40, dropped_capture_points=20, team_capture_points=180,
        )
        after = self._phase7_snapshot(
            battles=11,
            main_shots=320, main_hits=90, main_frags=4,
            secondary_shots=80, secondary_hits=20, secondary_frags=1,
            torpedo_shots=18, torpedo_hits=6, torpedo_frags=2,
            damage_scouting=45_000, ships_spotted=7,
            capture_points=50, dropped_capture_points=30, team_capture_points=200,
        )
        events = compute_battle_events(before, after)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["main_shots_delta"], 20)
        self.assertEqual(e["main_hits_delta"], 10)
        self.assertEqual(e["main_frags_delta"], 1)
        self.assertEqual(e["secondary_shots_delta"], 10)
        self.assertEqual(e["secondary_hits_delta"], 5)
        self.assertEqual(e["secondary_frags_delta"], 0)
        self.assertEqual(e["torpedo_shots_delta"], 6)
        self.assertEqual(e["torpedo_hits_delta"], 2)
        self.assertEqual(e["torpedo_frags_delta"], 1)
        self.assertEqual(e["damage_scouting_delta"], 5_000)
        self.assertEqual(e["ships_spotted_delta"], 2)
        self.assertEqual(e["capture_points_delta"], 10)
        self.assertEqual(e["dropped_capture_points_delta"], 10)
        self.assertEqual(e["team_capture_points_delta"], 20)

    def test_no_battle_advance_emits_no_event_even_if_phase7_advances(self):
        # WG occasionally publishes shot counts a tick before battle counts;
        # we don't emit an event until `battles` actually advances. The
        # `delta_battles <= 0` continue at the top of the loop guards this.
        before = self._phase7_snapshot(battles=10, main_shots=300)
        after = self._phase7_snapshot(battles=10, main_shots=320)
        self.assertEqual(compute_battle_events(before, after), [])

    def test_first_battle_baseline_attributes_full_phase7_value(self):
        # New ship's first battle: previous=missing, deltas equal full current.
        before = self._phase7_snapshot(battles=10, ship_id=42)
        # Same ship 42 unchanged; new ship 999 makes its debut.
        after_ship_42 = ShipSnapshot(
            ship_id=42, battles=10, wins=0, losses=0, frags=0,
            damage_dealt=0, xp=0, planes_killed=0, survived_battles=0,
        )
        after_ship_999 = ShipSnapshot(
            ship_id=999, battles=1, wins=1, losses=0, frags=2, damage_dealt=30_000,
            xp=1_500, planes_killed=0, survived_battles=1,
            main_shots=12, main_hits=4, torpedo_shots=2, torpedo_hits=1,
            damage_scouting=2_000, capture_points=10,
        )
        after = PlayerSnapshot(
            pvp_battles=11, pvp_wins=0, pvp_losses=0, pvp_frags=0,
            pvp_survived_battles=0,
            last_battle_time=before.last_battle_time,
            ships={42: after_ship_42, 999: after_ship_999},
        )
        events = compute_battle_events(before, after)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e["ship_id"], 999)
        self.assertEqual(e["main_shots_delta"], 12)
        self.assertEqual(e["torpedo_hits_delta"], 1)
        self.assertEqual(e["damage_scouting_delta"], 2_000)
        self.assertEqual(e["capture_points_delta"], 10)


class Phase7SerializationTests(TestCase):
    """Phase-7 fields round-trip through `_serialize_ships_payload` →
    `_hydrate_previous_snapshot`. Historical observations written before the
    widening must hydrate as zeros (not raise) — covered in Coercion tests."""

    def test_round_trip_preserves_phase7_fields(self):
        snap = PlayerSnapshot(
            pvp_battles=10, pvp_wins=0, pvp_losses=0, pvp_frags=0,
            pvp_survived_battles=0,
            last_battle_time=datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
            ships={42: ShipSnapshot(
                ship_id=42, battles=10, wins=5, losses=5, frags=8,
                damage_dealt=1_000_000, xp=20_000, planes_killed=0,
                survived_battles=6,
                main_shots=320, main_hits=90, torpedo_shots=18, torpedo_hits=6,
                damage_scouting=45_000, ships_spotted=7,
                capture_points=50, dropped_capture_points=30,
                team_capture_points=200,
            )},
        )
        payload = _serialize_ships_payload(snap)
        # Build a fake "previous" object the hydrator expects.
        fake_prev = type("FakeObs", (), {"ships_stats_json": payload,
                                          "pvp_battles": 10, "pvp_wins": 0,
                                          "pvp_losses": 0, "pvp_frags": 0,
                                          "pvp_survived_battles": 0,
                                          "last_battle_time": snap.last_battle_time})()
        rebuilt = _hydrate_previous_snapshot(fake_prev)
        self.assertEqual(rebuilt.ships[42].main_shots, 320)
        self.assertEqual(rebuilt.ships[42].torpedo_hits, 6)
        self.assertEqual(rebuilt.ships[42].damage_scouting, 45_000)
        self.assertEqual(rebuilt.ships[42].capture_points, 50)
        self.assertEqual(rebuilt.ships[42].team_capture_points, 200)


class Phase7DailyAggregateTests(TestCase):
    """`_apply_event_to_daily_summary` accumulates Phase-7 deltas into
    `PlayerDailyShipStats` columns under the rollup flag."""

    def setUp(self):
        self.player = Player.objects.create(
            name="phase7_rollup", player_id=22222, realm="na", pvp_battles=10,
        )
        self.from_obs = BattleObservation.objects.create(
            player=self.player, pvp_battles=10,
        )
        self.to_obs = BattleObservation.objects.create(
            player=self.player, pvp_battles=11,
        )

    def _make_event(self, **overrides):
        defaults = dict(
            player=self.player,
            ship_id=42,
            ship_name="Yamato",
            battles_delta=1, wins_delta=1, losses_delta=0, frags_delta=2,
            damage_delta=48_000, xp_delta=1_500, planes_killed_delta=0,
            survived=True,
            main_shots_delta=20, main_hits_delta=10, main_frags_delta=1,
            secondary_shots_delta=10, secondary_hits_delta=5, secondary_frags_delta=0,
            torpedo_shots_delta=6, torpedo_hits_delta=2, torpedo_frags_delta=1,
            damage_scouting_delta=5_000, ships_spotted_delta=2,
            capture_points_delta=10, dropped_capture_points_delta=10,
            team_capture_points_delta=20,
            from_observation=self.from_obs,
            to_observation=self.to_obs,
        )
        defaults.update(overrides)
        return BattleEvent.objects.create(**defaults)

    def test_first_event_writes_phase7_columns(self):
        event = self._make_event()
        with mock.patch.dict("os.environ",
                             {"BATTLE_HISTORY_ROLLUP_ENABLED": "1"}, clear=False):
            _apply_event_to_daily_summary(event)
        row = PlayerDailyShipStats.objects.get()
        self.assertEqual(row.main_shots, 20)
        self.assertEqual(row.main_hits, 10)
        self.assertEqual(row.torpedo_hits, 2)
        self.assertEqual(row.damage_scouting, 5_000)
        self.assertEqual(row.ships_spotted, 2)
        self.assertEqual(row.capture_points, 10)
        self.assertEqual(row.dropped_capture_points, 10)
        self.assertEqual(row.team_capture_points, 20)

    def test_second_event_increments_phase7_columns(self):
        third_obs = BattleObservation.objects.create(
            player=self.player, pvp_battles=12,
        )
        first = self._make_event()
        second = self._make_event(
            battles_delta=1, wins_delta=0, losses_delta=1,
            main_shots_delta=15, main_hits_delta=4,
            torpedo_shots_delta=3, torpedo_hits_delta=1,
            damage_scouting_delta=2_500, ships_spotted_delta=1,
            capture_points_delta=5, dropped_capture_points_delta=0,
            team_capture_points_delta=15,
            survived=False,
            from_observation=self.to_obs, to_observation=third_obs,
        )
        with mock.patch.dict("os.environ",
                             {"BATTLE_HISTORY_ROLLUP_ENABLED": "1"}, clear=False):
            _apply_event_to_daily_summary(first)
            _apply_event_to_daily_summary(second)
        row = PlayerDailyShipStats.objects.get()
        self.assertEqual(row.main_shots, 35)
        self.assertEqual(row.main_hits, 14)
        self.assertEqual(row.torpedo_shots, 9)
        self.assertEqual(row.torpedo_hits, 3)
        self.assertEqual(row.damage_scouting, 7_500)
        self.assertEqual(row.ships_spotted, 3)
        self.assertEqual(row.capture_points, 15)
        self.assertEqual(row.dropped_capture_points, 10)
        self.assertEqual(row.team_capture_points, 35)

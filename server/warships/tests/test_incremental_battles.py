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
    reconcile_daily_rollup_coverage,
    record_observation_and_diff,
    record_observation_from_payloads,
    record_ranked_observation_and_diff,
)
from warships.models import (
    BattleEvent,
    BattleObservation,
    Player,
    PlayerDailyShipStats,
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

    def test_fidelity_gap_warns_when_account_delta_outruns_ship_deltas(self):
        # Reproduces the CaptCornholeo shape: account/info pvp advances a lot
        # but the per-ship ships/stats diff captures only a little (ships/stats
        # lagged account/info at fetch time). The diff-time instrument must warn.
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 160  # account-level +60
        self.player.save()
        with self.assertLogs(
            "warships.incremental_battles", level="WARNING",
        ) as cm:
            record_observation_from_payloads(
                self.player,
                ship_data=self._ship_payload(battles=102),  # ships +2 only
            )
        line = "\n".join(cm.output)
        self.assertIn("battle-event diff fidelity gap", line)
        self.assertIn("account_delta=60", line)
        self.assertIn("ship_delta_sum=2", line)
        self.assertIn("missed=58", line)

    def test_fidelity_gap_silent_on_clean_advance(self):
        # Account and ships move in lockstep — no warning.
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 110  # account-level +10
        self.player.save()
        with self.assertNoLogs(
            "warships.incremental_battles", level="WARNING",
        ):
            record_observation_from_payloads(
                self.player,
                ship_data=self._ship_payload(battles=110),  # ships +10 too
            )

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

    # ---- last_battle_date piggyback (runbook 2026-05-23) ----------------

    def _stale_player_date(self):
        # 10 days ago — matches the lil_boots bug report scenario.
        self.player.last_battle_date = (
            django_timezone.now().date() - timedelta(days=10))
        self.player.days_since_last_battle = 10
        self.player.save(
            update_fields=["last_battle_date", "days_since_last_battle"])

    def test_advance_bumps_player_last_battle_date_to_today(self):
        self._stale_player_date()
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 101
        self.player.save(update_fields=["pvp_battles"])
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, survived=1),
        )
        self.player.refresh_from_db()
        self.assertEqual(
            self.player.last_battle_date, django_timezone.now().date())
        self.assertEqual(self.player.days_since_last_battle, 0)

    def test_advance_invalidates_player_detail_cache_on_commit(self):
        self._stale_player_date()
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.pvp_battles = 101
        self.player.save(update_fields=["pvp_battles"])
        with mock.patch(
            "warships.data.invalidate_player_detail_cache",
        ) as invalidator:
            with self.captureOnCommitCallbacks(execute=True):
                record_observation_from_payloads(
                    self.player,
                    ship_data=self._ship_payload(
                        battles=101, wins=1, survived=1),
                )
        invalidator.assert_called_once_with(
            self.player.player_id, realm=self.player.realm)

    def test_no_event_does_not_bump_last_battle_date(self):
        self._stale_player_date()
        baseline_date = self.player.last_battle_date
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        # No advance — battles count unchanged. Should produce zero events
        # and leave Player.last_battle_date untouched.
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.last_battle_date, baseline_date)
        self.assertEqual(self.player.days_since_last_battle, 10)

    def test_baseline_observation_does_not_bump_last_battle_date(self):
        # The first observation for a player has no prior to diff against.
        # We do not have evidence of recent activity from the diff lane,
        # so last_battle_date must remain whatever account/info set.
        self._stale_player_date()
        baseline_date = self.player.last_battle_date
        record_observation_from_payloads(
            self.player, ship_data=self._ship_payload(battles=100),
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.last_battle_date, baseline_date)
        self.assertEqual(self.player.days_since_last_battle, 10)

    def test_random_prior_broken_does_not_bump_last_battle_date(self):
        # Construct a previous observation whose ships_stats_json is empty
        # but whose pvp_battles claims history existed. The orchestrator
        # treats this as a baseline (no events), so last_battle_date must
        # not be bumped from this artifact.
        self._stale_player_date()
        BattleObservation.objects.create(
            player=self.player, pvp_battles=100, pvp_wins=50, pvp_losses=50,
            pvp_frags=80, pvp_survived_battles=60, ships_stats_json=[],
        )
        baseline_date = self.player.last_battle_date
        record_observation_from_payloads(
            self.player,
            ship_data=self._ship_payload(battles=101, wins=1, survived=1),
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.last_battle_date, baseline_date)


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
            "warships.data._fetch_ship_stats_for_player_with_hidden",
            return_value=(self.ship_payload, False),
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

    def test_capture_runs_before_battles_updated_at_bump(self):
        """`X-Player-Refresh-Pending` is anchored on `battles_updated_at`
        (views._player_refresh_signals), so the bump must land only AFTER the
        capture has committed its events and invalidated the battle-history
        cache. If the bump lands first, the client's 2s poll can see "landed"
        in the gap, fire its single nonce-bumped battle-history refetch, and
        cache the pre-session payload — the charts then show stale data until
        a manual reload (2026-07-17 investigation)."""
        from warships.incremental_battles import (
            record_observation_from_payloads as real_capture,
        )
        seen = {}

        def spying_capture(player, **kwargs):
            row = Player.objects.filter(pk=player.pk).values(
                "battles_updated_at").first()
            seen["bump_at_capture_time"] = row["battles_updated_at"]
            return real_capture(player, **kwargs)

        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_CAPTURE_ENABLED": "1"},
            clear=False,
        ), mock.patch(
            "warships.incremental_battles.record_observation_from_payloads",
            side_effect=spying_capture,
        ):
            self._run_update_battle_data()
        self.assertIn("bump_at_capture_time", seen, "capture hook never ran")
        self.assertIsNone(
            seen["bump_at_capture_time"],
            "battles_updated_at was bumped before the capture ran — the "
            "pending header can clear before the battle-history payload "
            "is refetchable",
        )
        self.player.refresh_from_db()
        self.assertIsNotNone(
            self.player.battles_updated_at,
            "the bump must still land after the capture",
        )

    def test_capture_failure_still_bumps_battles_updated_at(self):
        """Ordering parity for the failure path: a capture bug must not leave
        battles_updated_at un-bumped (that would re-trigger the refresh on
        every poll)."""
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_CAPTURE_ENABLED": "1"},
            clear=False,
        ), mock.patch(
            "warships.incremental_battles.record_observation_from_payloads",
            side_effect=RuntimeError("simulated capture bug"),
        ):
            self._run_update_battle_data()
        self.player.refresh_from_db()
        self.assertIsNotNone(self.player.battles_updated_at)
        self.assertTrue(self.player.battles_json)

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

    def test_by_ship_ship_pop_avg_damage_warm_then_hydrate(self):
        """The damage-treemap baseline is never computed on the request
        thread: a cold cache serves None + `X-Ship-Pop-Pending`, the
        background warm fills the per-(realm, ship, day) cache, and the next
        request hydrates real values — None below the population floor, and
        other realms' rows never leak into the baseline."""
        from warships.tasks import warm_ship_pop_avg_damage_task

        today = django_timezone.now().date()
        self._seed_daily_rows({
            42: {"battles": 6, "wins": 4, "damage": 287_400,
                 "ship_name": "Yamato"},
            43: {"battles": 2, "wins": 1, "damage": 95_000,
                 "ship_name": "Dalian"},
        })
        # Same-realm population on ship 42: pushes it over the 20-battle
        # floor (6 + 20 = 26 total) with a known damage sum.
        pop_na = Player.objects.create(
            name="pop_na", player_id=44445, realm="na")
        PlayerDailyShipStats.objects.create(
            player=pop_na, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=20, wins=10, damage=1_000_000,
        )
        # Cross-realm rows must not contaminate the na baseline.
        pop_eu = Player.objects.create(
            name="pop_eu", player_id=44446, realm="eu")
        PlayerDailyShipStats.objects.create(
            player=pop_eu, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=50, wins=25, damage=50_000_000,
        )
        env = {"BATTLE_HISTORY_API_ENABLED": "1"}
        # Cold cache: baselines are None and the pending header is set (the
        # broker is unavailable in tests, so the enqueue itself no-ops — the
        # contract under test is "never compute inline").
        with mock.patch.dict("os.environ", env, clear=False):
            first = self.client.get(
                "/api/player/api_test/battle-history/?days=7")
        self.assertEqual(first.status_code, 200)
        by_ship = {s["ship_id"]: s for s in first.json()["by_ship"]}
        self.assertIsNone(by_ship[42]["ship_pop_avg_damage"])
        self.assertIsNone(by_ship[43]["ship_pop_avg_damage"])
        self.assertEqual(first.headers.get("X-Ship-Pop-Pending"), "true")

        # Run the warm inline (what the queued task does in production).
        warm_ship_pop_avg_damage_task.apply(
            kwargs={"realm": "na", "ship_ids": [42, 43]})

        # Warm cache: real values attach — even though the payload body was
        # cached by the first request (baselines attach per-request, after
        # the payload cache).
        with mock.patch.dict("os.environ", env, clear=False):
            second = self.client.get(
                "/api/player/api_test/battle-history/?days=7")
        self.assertEqual(second.status_code, 200)
        by_ship = {s["ship_id"]: s for s in second.json()["by_ship"]}
        # (287_400 + 1_000_000) / (6 + 20) = 49_515.38… → 49_515
        self.assertEqual(by_ship[42]["ship_pop_avg_damage"], 49_515)
        # Ship 43 has only the viewer's 2 battles — below the 20-battle
        # population floor, so no baseline is exposed (and the 0-sentinel
        # counts as computed: no pending header on a fully-probed set).
        self.assertIsNone(by_ship[43]["ship_pop_avg_damage"])
        self.assertIsNone(second.headers.get("X-Ship-Pop-Pending"))

    def test_bulk_warm_all_ship_pop_avg_damage(self):
        """The nightly bulk warmer (chained from the ship-standings snapshot)
        computes EVERY ship's baseline in one grouped scan: above-floor ships
        get real values, below-floor ships the 0 sentinel (attach → None, no
        re-queue), cross-realm rows never leak in, and afterward the read
        path reports zero misses for the whole set."""
        from warships.data import get_cached_ship_pop_avg_damage
        from warships.tasks import warm_all_ship_pop_avg_damage_task

        today = django_timezone.now().date()
        self._seed_daily_rows({
            42: {"battles": 6, "wins": 4, "damage": 287_400,
                 "ship_name": "Yamato"},
            43: {"battles": 2, "wins": 1, "damage": 95_000,
                 "ship_name": "Dalian"},
        })
        pop_na = Player.objects.create(
            name="bulk_pop_na", player_id=44447, realm="na")
        PlayerDailyShipStats.objects.create(
            player=pop_na, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=20, wins=10, damage=1_000_000,
        )
        # Cross-realm rows must not contaminate the na baselines.
        pop_eu = Player.objects.create(
            name="bulk_pop_eu", player_id=44448, realm="eu")
        PlayerDailyShipStats.objects.create(
            player=pop_eu, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=50, wins=25, damage=50_000_000,
        )

        result = warm_all_ship_pop_avg_damage_task.apply(
            kwargs={"realm": "na"}).get()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["ships"], 2)

        hits, missing = get_cached_ship_pop_avg_damage("na", [42, 43])
        self.assertEqual(missing, [])
        # (287_400 + 1_000_000) / (6 + 20) = 49_515.38… → 49_515
        self.assertEqual(hits[42], 49_515)
        # Below the 20-battle floor → 0 sentinel ("computed, no baseline").
        self.assertEqual(hits[43], 0)

    def test_returns_404_when_player_unknown(self):
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get("/api/player/no_such_player/battle-history/")
        self.assertEqual(response.status_code, 404)

    def test_clamps_days_to_max(self):
        # MAX_DAYS=365 since the year window picker shipped — was 30
        # pre-2026-05-06.
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get("/api/player/api_test/battle-history/?days=9999")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["window_days"], 365)

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

    def test_delta_suppressed_when_snapshot_skew_makes_prior_impossible(self):
        """Regression for the Bremen -83% delta: when the randoms-only
        lifetime snapshot (battles_json) lags the live rollup, subtracting
        the period from lifetime can yield an impossible prior (more wins
        than battles). Lifetime stays visible; the delta is suppressed.
        """
        # Lifetime snapshot: 8 battles / 4 wins (50%). Rollup period: 5
        # battles / 0 wins. Prior = 8-5 = 3 battles but 4-0 = 4 wins —
        # impossible (would compute a 133.3% prior WR → -83.3pp delta).
        self.player.pvp_battles = 1000
        self.player.pvp_wins = 530
        self.player.battles_json = [
            {"ship_id": 42, "ship_name": "Bremen", "ship_tier": 10,
             "ship_type": "Cruiser",
             "pvp_battles": 8, "wins": 4, "losses": 4},
        ]
        self.player.save()
        self._seed_daily_rows({
            42: {"battles": 5, "wins": 0, "losses": 5, "ship_name": "Bremen"},
        })
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=random",
            )
        ship = response.json()["by_ship"][0]
        self.assertEqual(ship["win_rate"], 0.0)
        # Valid career number is preserved.
        self.assertEqual(ship["lifetime_battles"], 8)
        self.assertEqual(ship["lifetime_win_rate"], 50.0)
        # Nonsense delta suppressed; not a new ship (real prior history).
        self.assertIsNone(ship["delta_win_rate"])
        self.assertFalse(ship["is_new_ship"])

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

    def test_capture_invalidates_empty_cached_window(self):
        # Read-before-write race: a page-load fetch for the week window
        # caches an empty payload, then the same visit's capture writes new
        # events. The empty cache entry must be dropped so the next fetch
        # surfaces the battles instead of waiting out the 5-min TTL.
        from django.core.cache import cache
        from warships.views import _battle_history_cache_key
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            # 1) Empty read for the default frontend window (?window=week).
            r1 = self.client.get(
                "/api/player/api_test/battle-history/?window=week")
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(r1.json()["totals"]["battles"], 0)
            week_key = _battle_history_cache_key(
                "na", "api_test", "daily", 7, "random")
            self.assertIsNotNone(
                cache.get(week_key), "empty week payload should be cached")

            # 2) A capture writes events for this player. The on_commit hook
            #    must invalidate the battle-history cache.
            self._seed_daily_rows({
                42: {"battles": 3, "wins": 2, "ship_name": "Yamato"},
            })
            with self.captureOnCommitCallbacks(execute=True):
                record_observation_from_payloads(
                    self.player,
                    ship_data=[{"ship_id": 42, "pvp": {"battles": 200}}],
                )
                self.player.pvp_battles = 203
                self.player.save(update_fields=["pvp_battles"])
                record_observation_from_payloads(
                    self.player,
                    ship_data=[{"ship_id": 42, "pvp": {
                        "battles": 203, "wins": 2, "survived_battles": 0}}],
                )

            # Guard against a tautology: the invalidation only fires when the
            # capture actually produced an event. Confirm it did.
            self.assertEqual(
                BattleEvent.objects.filter(player=self.player).count(), 1)
            self.assertIsNone(
                cache.get(week_key),
                "capture should have invalidated the stale empty payload")

            # 3) Next fetch now reflects the written battles.
            r2 = self.client.get(
                "/api/player/api_test/battle-history/?window=week")
            self.assertEqual(r2.json()["totals"]["battles"], 3)

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

    def test_mode_ranked_scopes_to_current_season(self):
        # Ranked is current-season-scoped: a window spanning a season boundary
        # surfaces ONLY the latest season's battles (not a cross-season sum),
        # so the bars/totals stay consistent with the current-season WR/O
        # baseline and the frontend walk-back can't go negative. With no
        # ranked_json, the current season falls back to the latest ranked
        # rollup season (22 here); the season-21 battles are excluded.
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
        # Only the current/latest season (22) counts.
        self.assertEqual(body["totals"]["battles"], 5)
        self.assertEqual(body["totals"]["damage"], 120_000)
        # No ranked_json → no WG cumulative → WR/O baseline stays empty.
        self.assertIsNone(body["totals"]["lifetime_win_rate"])

    def test_mode_ranked_current_season_overall_and_per_ship_wr(self):
        # The current ranked season (ranked_json[0]) anchors the WR/O: the
        # season's cumulative battles/wins drive the headline WR/O, and the
        # per-ship WR/O comes from the season's ships/stats snapshot. The 30d
        # window is a SUBSET of the season, so a real prior exists and both
        # the headline and per-ship deltas are meaningful.
        today = django_timezone.now().date()
        season = 22
        self.player.ranked_json = [
            {"season_id": season, "season_name": "Season 22",
             "total_battles": 20, "total_wins": 14, "win_rate": 0.7},
            {"season_id": 21, "season_name": "Season 21",
             "total_battles": 50, "total_wins": 25, "win_rate": 0.5},
        ]
        self.player.save(update_fields=["ranked_json"])
        # Per-ship season cumulative snapshot. ship 42: 20b/14w this season
        # (window is a subset → WR/O shows). ship 43: 3b/2w this season, all of
        # which sit in the window → prior==0 → WR/O is redundant with WR/S.
        BattleObservation.objects.create(
            player=self.player,
            ranked_ships_stats_json=[
                {"ship_id": 42, "seasons": {str(season): {"1": {"rank_solo": {
                    "battles": 20, "wins": 14, "losses": 6}}}}},
                {"ship_id": 43, "seasons": {str(season): {"1": {"rank_solo": {
                    "battles": 3, "wins": 2, "losses": 1}}}}},
            ],
        )
        # Window holds only 6 of the 20 season battles for ship 42 ...
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=season,
            battles=6, wins=3, damage=120_000,
        )
        # ... ship 43's entire season (3b/2w) sits in the window ...
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=43, ship_name="Dalian",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=season,
            battles=3, wins=2, damage=45_000,
        )
        # ... and a prior-season row that must be excluded by the scope.
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=9, wins=8, damage=90_000,
        )
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=ranked",
            )
        body = r.json()
        t = body["totals"]
        # Window scoped to season 22 → 6 (ship42) + 3 (ship43) = 9 battles;
        # the season-21 row (9 battles) is excluded.
        self.assertEqual(t["battles"], 9)
        self.assertEqual(body["ranked_season_name"], "Season 22")
        # Headline WR/O = current-season cumulative 14/20 = 70.0%.
        self.assertEqual(t["lifetime_battles"], 20)
        self.assertEqual(t["lifetime_win_rate"], 70.0)
        # Overall delta: window 9b/5w, prior 11b/9w = 81.8%, season 70.0% → -11.8.
        self.assertEqual(t["delta_win_rate"], -11.8)
        ships = {s["ship_id"]: s for s in body["by_ship"]}
        # Per-ship WR/O for ship 42 = season cumulative 14/20 = 70.0%.
        ship42 = ships[42]
        self.assertEqual(ship42["lifetime_battles"], 20)
        self.assertEqual(ship42["lifetime_win_rate"], 70.0)
        self.assertEqual(ship42["delta_win_rate"], -8.6)
        self.assertFalse(ship42["is_new_ship"])
        # ship 43's whole season is in-window (prior==0) → WR/O is redundant
        # with WR/S, so it's suppressed rather than showing two equal columns.
        ship43 = ships[43]
        self.assertIsNone(ship43["lifetime_win_rate"])
        self.assertIsNone(ship43["delta_win_rate"])
        self.assertFalse(ship43["is_new_ship"])

    def test_mode_ranked_baseline_skew_suppresses_overall_delta(self):
        # ranked_json cumulative can lag the live rollup (WG snapshot cadence):
        # the window's current-season battles exceed the reported season total.
        # The WR/O still surfaces the season cumulative, but the overall delta
        # is suppressed rather than computed from an impossible negative prior.
        today = django_timezone.now().date()
        season = 22
        self.player.ranked_json = [
            {"season_id": season, "season_name": "Season 22",
             "total_battles": 4, "total_wins": 3, "win_rate": 0.75},
        ]
        self.player.save(update_fields=["ranked_json"])
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=season,
            battles=6, wins=5, damage=120_000,
        )
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=ranked",
            )
        t = r.json()["totals"]
        # Season cumulative still shown (3/4 = 75.0%) ...
        self.assertEqual(t["lifetime_battles"], 4)
        self.assertEqual(t["lifetime_win_rate"], 75.0)
        # ... but the impossible prior (4 - 6 < 0) suppresses the delta.
        self.assertIsNone(t["delta_win_rate"])

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
        # Combined mode now anchors lifetime delta math on the period's
        # random subset (since the randoms-only Player.battles_json baseline
        # only supports apples-to-apples comparison against random battles).
        # The test player has pvp_battles=200 and pvp_wins=0, so lifetime
        # WR is 0.0 — the value is populated, not suppressed.
        self.assertEqual(body["totals"]["lifetime_battles"], 200)
        self.assertEqual(body["totals"]["lifetime_win_rate"], 0.0)

    def test_is_new_ship_flag_set_when_period_equals_lifetime(self):
        """When the player's entire random-battle history in a ship falls
        inside the lookback window, prior_battles=0 and the delta math is
        undefined. Surface that with `is_new_ship=True` and suppress the
        redundant lifetime cell (period_wr == lifetime_wr by construction).
        """
        self.player.battles_json = [
            {"ship_id": 42, "pvp_battles": 5, "wins": 3, "losses": 2},
        ]
        self.player.save()

        today = django_timezone.now().date()
        # Period: 5 random battles, 3 wins — exactly matches lifetime.
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=5, wins=3, damage=200_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=random",
            )
        body = r.json()
        ships = {s["ship_id"]: s for s in body["by_ship"]}
        yamato = ships[42]
        self.assertTrue(yamato["is_new_ship"])
        # Lifetime row is redundant (period == lifetime) — suppressed so
        # the cell collapses to <period%> / NEW.
        self.assertIsNone(yamato["lifetime_battles"])
        self.assertIsNone(yamato["lifetime_win_rate"])
        self.assertIsNone(yamato["delta_win_rate"])

    def test_ranked_only_in_period_row_suppresses_random_lifetime(self):
        """Regression for /player/lordPOWARFULL007: ships played only in
        ranked during the window had a structurally-zero Δ0.0% leak
        through, because prior_random_battles==lifetime_random_battles by
        construction. Now those rows null out lifetime/delta and surface
        only the RANKED badge.
        """
        # Lifetime row exists with real random history.
        self.player.battles_json = [
            {"ship_id": 42, "pvp_battles": 158, "wins": 65, "losses": 93},
        ]
        self.player.save()

        today = django_timezone.now().date()
        # Period: 2 RANKED battles, 0 random.
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=2, wins=1, damage=120_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=combined",
            )
        ships = {s["ship_id"]: s for s in r.json()["by_ship"]}
        yamato = ships[42]
        self.assertTrue(yamato["is_ranked_only_period"])
        # No leaky Δ0.0% — lifetime/delta suppressed entirely.
        self.assertIsNone(yamato["lifetime_battles"])
        self.assertIsNone(yamato["lifetime_win_rate"])
        self.assertIsNone(yamato["delta_win_rate"])
        # Not NEW — the player has random history for this ship; the
        # window just happened to be ranked-only.
        self.assertFalse(yamato["is_new_ship"])

    def test_is_new_ship_flag_set_when_lifetime_snapshot_lags(self):
        """Real-world case: a player plays a ship recently, the rollup
        records period activity, but battles_json (refreshed less often)
        hasn't caught up yet — `lifetime_by_ship.get(ship_id)` returns
        None. Pre-fix the row rendered as bare period-only with no
        explanation; post-fix it surfaces NEW so the user sees context.
        """
        # battles_json deliberately empty — simulates the snapshot lag.
        self.player.battles_json = []
        self.player.save()

        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=9, wins=5, damage=300_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=random",
            )
        ships = {s["ship_id"]: s for s in r.json()["by_ship"]}
        yamato = ships[42]
        self.assertTrue(yamato["is_new_ship"])
        self.assertIsNone(yamato["lifetime_battles"])
        self.assertIsNone(yamato["delta_win_rate"])

    def test_is_new_ship_flag_NOT_set_in_ranked_mode_with_missing_lifetime(self):
        """Mode=ranked has no lifetime baseline anywhere in the data model,
        so NEW would be misleading. Bare period-only is correct there.
        """
        self.player.battles_json = []
        self.player.save()

        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=5, wins=3,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=ranked",
            )
        ships = {s["ship_id"]: s for s in r.json()["by_ship"]}
        self.assertFalse(ships[42]["is_new_ship"])

    def test_is_new_ship_flag_set_when_prior_sample_too_small(self):
        """A 1- or 2-battle prior sample makes the delta a coin-flip
        artifact (one win/loss swings it 50–100pp). Treat the row as NEW
        and hide the meaningless delta. Lifetime stays visible since it
        carries marginally more info than the period alone.
        """
        # 14 lifetime, 12 in the period → prior_battles=2 (< threshold 3).
        self.player.battles_json = [
            {"ship_id": 42, "pvp_battles": 14, "wins": 7, "losses": 7},
        ]
        self.player.save()

        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=12, wins=7, damage=400_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=random",
            )
        ships = {s["ship_id"]: s for s in r.json()["by_ship"]}
        yamato = ships[42]
        self.assertTrue(yamato["is_new_ship"])
        self.assertIsNone(yamato["delta_win_rate"])
        # Lifetime kept visible (slightly larger sample than period).
        self.assertEqual(yamato["lifetime_battles"], 14)
        self.assertEqual(yamato["lifetime_win_rate"], 50.0)

    def test_is_new_ship_flag_false_when_prior_history_exists(self):
        self.player.battles_json = [
            {"ship_id": 42, "pvp_battles": 50, "wins": 30, "losses": 20},
        ]
        self.player.save()

        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=5, wins=3,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=random",
            )
        ships = {s["ship_id"]: s for s in r.json()["by_ship"]}
        self.assertFalse(ships[42]["is_new_ship"])
        self.assertIsNotNone(ships[42]["delta_win_rate"])

    def test_mode_combined_handles_ranked_only_ship_with_zero_random_lifetime(self):
        """Regression for the GHOSTTUNDERBOLT 500 (Chung Mu / Mogador):
        a ship played only in ranked, with a battles_json entry carrying
        pvp_battles=0 (rented for ranked, never played random), used to
        ZeroDivisionError when computing combined-mode lifetime_wr.
        """
        # Lifetime row exists but with zero random battles — typical of
        # ranked rentals.
        self.player.battles_json = [
            {"ship_id": 42, "pvp_battles": 0, "wins": 0, "losses": 0},
        ]
        self.player.save()

        today = django_timezone.now().date()
        # Period has only ranked play of this ship.
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANKED, season_id=21,
            battles=1, wins=1, damage=80_000,
        )
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?days=7&mode=combined",
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ships = {s["ship_id"]: s for s in body["by_ship"]}
        yamato = ships[42]
        self.assertEqual(yamato["battles"], 1)
        # No random lifetime → lifetime fields stay null; frontend
        # collapses to period-only.
        self.assertIsNone(yamato["lifetime_battles"])
        self.assertIsNone(yamato["lifetime_win_rate"])
        self.assertIsNone(yamato["delta_win_rate"])

    def test_mode_combined_per_ship_lifetime_uses_random_period_subset(self):
        """Regression: ships with mixed random+ranked play in the period
        should still get a populated `lifetime_*` field set in combined
        mode, anchored on the random subset for the prior-state math.
        Pre-fix, combined mode left lifetime fields null on every ship row.
        """
        from warships.models import Player as _Player
        # Stamp battles_json with a per-ship lifetime baseline for the
        # ship Yamato (id=42): 13 random battles, 5 random wins → 38.5%.
        self.player.battles_json = [
            {"ship_id": 42, "pvp_battles": 13, "wins": 5, "losses": 8},
        ]
        self.player.pvp_battles = 13
        self.player.pvp_wins = 5
        self.player.save()

        today = django_timezone.now().date()
        # Period: 8 random battles + 0 ranked, mirroring the GHOSTTUNDERBOLT
        # Kléber case that surfaced this bug (random has lifetime/delta;
        # combined was incorrectly stripping it).
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM,
            battles=8, wins=2, damage=400_000,
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
        ships = {s["ship_id"]: s for s in body["by_ship"]}
        yamato = ships[42]
        self.assertEqual(yamato["battles"], 8)
        self.assertEqual(yamato["lifetime_battles"], 13)
        self.assertEqual(yamato["lifetime_win_rate"], 38.5)
        # prior_battles=5, prior_wins=3, prior_wr=60.0 → delta=-21.5.
        self.assertEqual(yamato["delta_win_rate"], -21.5)

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

    def test_has_recent_24h_activity_flag_reflects_battle_events(self):
        """Frontend grays out the Day pill when this flag is false. Backend
        sets it from a BattleEvent.detected_at >= now-24h existence probe.
        """
        from warships.models import BattleEvent, BattleObservation
        now = django_timezone.now()
        # Seed an old event (30h ago) and a daily rollup (so Week has data
        # to render). The flag should still be False because the BattleEvent
        # is outside the 24h window.
        oa = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=32))
        ob = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=30))
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANDOM, battles_delta=4, wins_delta=2,
            from_observation=oa, to_observation=ob,
        )
        BattleEvent.objects.filter(
            from_observation=oa, to_observation=ob,
        ).update(detected_at=now - timedelta(hours=30))
        PlayerDailyShipStats.objects.create(
            player=self.player,
            date=(now - timedelta(hours=30)).date(),
            ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=4, wins=2,
        )
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?window=week&mode=random",
            )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["has_recent_24h_activity"])

        # Add a fresh event (2h ago) → flag flips to True.
        oc = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=3))
        od = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=2))
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANDOM, battles_delta=2, wins_delta=2,
            from_observation=oc, to_observation=od,
        )
        BattleEvent.objects.filter(
            from_observation=oc, to_observation=od,
        ).update(detected_at=now - timedelta(hours=2))
        from django.core.cache import cache
        cache.clear()
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?window=week&mode=random",
            )
        self.assertTrue(r.json()["has_recent_24h_activity"])

    def test_window_day_aggregates_battle_events_in_last_24h(self):
        """The `day` window queries BattleEvent.detected_at directly (true
        rolling 24h, hour-precise) rather than the calendar-bucketed daily
        rollup. Events older than 24h must be excluded.
        """
        from warships.models import BattleEvent, BattleObservation
        now = django_timezone.now()
        # Two sentinel observations for the from/to FK requirement.
        obs_a = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=30))
        obs_b = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=23))
        obs_c = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=2))
        # Old event (30h ago) — outside window
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANDOM, battles_delta=10, wins_delta=3,
            damage_delta=100_000, frags_delta=2,
            from_observation=obs_a, to_observation=obs_b,
        )
        # Recent event (2h ago) — inside window
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANDOM, battles_delta=4, wins_delta=3,
            damage_delta=80_000, frags_delta=5,
            from_observation=obs_b, to_observation=obs_c,
        )
        # Override detected_at (auto_now_add otherwise pins them to now)
        BattleEvent.objects.filter(
            from_observation=obs_a, to_observation=obs_b,
        ).update(detected_at=now - timedelta(hours=30))
        BattleEvent.objects.filter(
            from_observation=obs_b, to_observation=obs_c,
        ).update(detected_at=now - timedelta(hours=2))

        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?window=day&mode=random",
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["period"], "day")
        # Only the 2h-old event counts: battles=4, wins=3, frags=5.
        self.assertEqual(body["totals"]["battles"], 4)
        self.assertEqual(body["totals"]["wins"], 3)
        self.assertEqual(body["totals"]["frags"], 5)
        self.assertEqual(len(body["by_ship"]), 1)
        self.assertEqual(body["by_ship"][0]["ship_id"], 42)
        self.assertEqual(body["by_ship"][0]["battles"], 4)

    def test_window_day_separates_random_and_ranked(self):
        """In the `day` window, mode=random/ranked filters by BattleEvent.mode
        and combined returns both summed.
        """
        from warships.models import BattleEvent, BattleObservation
        now = django_timezone.now()
        oa = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=3))
        ob = BattleObservation.objects.create(
            player=self.player, observed_at=now - timedelta(hours=2))
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANDOM, battles_delta=4, wins_delta=3,
            damage_delta=80_000, frags_delta=2,
            from_observation=oa, to_observation=ob,
        )
        BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            mode=BattleEvent.MODE_RANKED, season_id=21,
            battles_delta=10, wins_delta=7, damage_delta=400_000, frags_delta=8,
            from_observation=oa, to_observation=ob,
        )
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            for mode, expected in [("random", 4), ("ranked", 10), ("combined", 14)]:
                r = self.client.get(
                    f"/api/player/api_test/battle-history/?window=day&mode={mode}",
                )
                self.assertEqual(r.status_code, 200, f"mode={mode}")
                self.assertEqual(
                    r.json()["totals"]["battles"], expected,
                    f"mode={mode} battles mismatch",
                )

    def test_window_month_returns_30_days_of_daily_rollups(self):
        """The `month` window reads PlayerDailyShipStats with windows=30.
        Rows older than 30 days must be excluded.
        """
        today = django_timezone.now().date()
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=5, wins=3,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today - timedelta(days=29),
            ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=4, wins=2,
        )
        # 31 days old — outside the 30-day window
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today - timedelta(days=31),
            ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=99, wins=99,
        )
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?window=month&mode=random",
            )
        body = r.json()
        # Includes the 0d and 29d rows; excludes the 31d row.
        self.assertEqual(body["totals"]["battles"], 9)
        self.assertEqual(body["totals"]["wins"], 5)

    def test_window_year_does_not_trip_legacy_30d_cap(self):
        """Pre-fix `BATTLE_HISTORY_MAX_DAYS=30` would have capped a 365-day
        request at 30. The `year` window must request 365 windows fully.
        """
        today = django_timezone.now().date()
        # Row 364 days old — inside year window, outside any pre-fix cap.
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today - timedelta(days=364),
            ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=2, wins=1,
        )
        PlayerDailyShipStats.objects.create(
            player=self.player, date=today, ship_id=42, ship_name="Yamato",
            mode=PlayerDailyShipStats.MODE_RANDOM, battles=3, wins=2,
        )
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_API_ENABLED": "1"}, clear=False,
        ):
            r = self.client.get(
                "/api/player/api_test/battle-history/?window=year&mode=random",
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["totals"]["battles"], 5)
        self.assertEqual(body["totals"]["wins"], 3)

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


class BattleHistoryPeriodApiTests(TestCase):
    """Daily-only battle-history API. The weekly/monthly/yearly rollup
    tables were dropped 2026-06-15; `?period=weekly|monthly|yearly` now
    falls back to daily instead of reading a period tier."""

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

    def test_legacy_period_param_falls_back_to_daily(self):
        # An unsupported `?period=weekly` no longer reads a period tier; it
        # falls through BATTLE_HISTORY_PERIODS to daily without erroring.
        with mock.patch.dict(
            "os.environ",
            {"BATTLE_HISTORY_API_ENABLED": "1"},
            clear=False,
        ):
            response = self.client.get(
                "/api/player/period_api/battle-history/?period=weekly&windows=4",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["period"], "daily")

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


class CompactBattleObservationPayloadsTests(TestCase):
    """Disk-retention compaction of BattleObservation JSON payloads.

    Verifies the keep-set (latest N per player + latest non-NULL-ranked) is
    preserved, older payloads are NULLed, rows are never deleted (so
    BattleEvent CASCADE FKs stay intact), and dry-run writes nothing.
    Runbook: agents/runbooks/runbook-db-cpu-saturation-2026-05-24.md
    """

    def setUp(self):
        self.player = Player.objects.create(
            name="compact_test", player_id=77777, realm="na", pvp_battles=500,
        )

    def _obs(self, *, days_ago, ranked=False, ships=True):
        from warships.models import BattleObservation
        obs = BattleObservation.objects.create(
            player=self.player,
            pvp_battles=500,
            ships_stats_json=(
                [{"ship_id": 1, "pvp": {"battles": 1}}] if ships else None),
            ranked_ships_stats_json=(
                [{"ship_id": 1, "season_id": 1, "battles": 1}]
                if ranked else None),
        )
        # observed_at is auto_now_add; override via queryset update to place
        # the row at a controlled point in the timeline.
        ts = django_timezone.now() - timedelta(days=days_ago)
        BattleObservation.objects.filter(pk=obs.pk).update(observed_at=ts)
        obs.refresh_from_db()
        return obs

    def test_keeps_latest_n_clears_older(self):
        from warships.models import BattleObservation
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        obs = [self._obs(days_ago=d) for d in (5, 4, 3, 2, 1)]  # oldest→newest
        result = compact_battle_observation_payloads(keep_per_player=2)
        self.assertEqual(result["cleared"], 3)
        # No rows deleted.
        self.assertEqual(
            BattleObservation.objects.filter(player=self.player).count(), 5)
        for o in obs:
            o.refresh_from_db()
        # Two newest keep their JSON; three oldest are cleared.
        self.assertIsNotNone(obs[4].ships_stats_json)  # days_ago=1
        self.assertIsNotNone(obs[3].ships_stats_json)  # days_ago=2
        self.assertIsNone(obs[2].ships_stats_json)     # days_ago=3
        self.assertIsNone(obs[1].ships_stats_json)     # days_ago=4
        self.assertIsNone(obs[0].ships_stats_json)     # days_ago=5

    def test_preserves_latest_nonnull_ranked_even_if_old(self):
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        # day4 is the only ranked observation and sits outside keep=2.
        old_ranked = self._obs(days_ago=4, ranked=True)
        self._obs(days_ago=3)
        self._obs(days_ago=2)
        self._obs(days_ago=1)
        compact_battle_observation_payloads(keep_per_player=2)
        old_ranked.refresh_from_db()
        # The ranked walk-back baseline must survive (both columns kept, since
        # the keep-set excludes it from clearing entirely).
        self.assertIsNotNone(old_ranked.ranked_ships_stats_json)
        self.assertIsNotNone(old_ranked.ships_stats_json)

    def test_dry_run_writes_nothing(self):
        from warships.models import BattleObservation
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        for d in (5, 4, 3, 2, 1):
            self._obs(days_ago=d)
        result = compact_battle_observation_payloads(
            keep_per_player=2, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["candidates"], 3)
        self.assertEqual(result["players_affected"], 1)
        # Nothing cleared.
        self.assertEqual(
            BattleObservation.objects.filter(
                player=self.player,
                ships_stats_json__isnull=False,
            ).count(),
            5,
        )

    def test_does_not_delete_rows_or_cascade_events(self):
        from warships.models import BattleEvent, BattleObservation
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        old = self._obs(days_ago=5)
        mid = self._obs(days_ago=4)
        self._obs(days_ago=2)
        self._obs(days_ago=1)
        # Event diffed between two observations that will both be compacted.
        event = BattleEvent.objects.create(
            player=self.player,
            mode=BattleEvent.MODE_RANDOM,
            ship_id=1,
            from_observation=old,
            to_observation=mid,
            battles_delta=1,
        )
        compact_battle_observation_payloads(keep_per_player=2)
        # Rows survive (no CASCADE), so the event survives.
        self.assertTrue(
            BattleObservation.objects.filter(pk=old.pk).exists())
        self.assertTrue(
            BattleObservation.objects.filter(pk=mid.pk).exists())
        self.assertTrue(BattleEvent.objects.filter(pk=event.pk).exists())
        # But their payloads were cleared.
        old.refresh_from_db()
        self.assertIsNone(old.ships_stats_json)

    def test_min_age_hours_floor_protects_recent(self):
        from warships.models import BattleObservation
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        # Three observations all within the last few hours.
        from warships.models import BattleObservation as _BO
        for hours in (3, 2, 1):
            o = _BO.objects.create(
                player=self.player, pvp_battles=500,
                ships_stats_json=[{"ship_id": 1, "pvp": {"battles": 1}}],
            )
            _BO.objects.filter(pk=o.pk).update(
                observed_at=django_timezone.now() - timedelta(hours=hours))
        # keep=1 would normally clear 2, but min_age_hours=48 protects all.
        result = compact_battle_observation_payloads(
            keep_per_player=1, min_age_hours=48, dry_run=True)
        self.assertEqual(result["candidates"], 0)
        # With no floor, the 2 beyond keep=1 become candidates.
        result2 = compact_battle_observation_payloads(
            keep_per_player=1, min_age_hours=0, dry_run=True)
        self.assertEqual(result2["candidates"], 2)

    def test_management_command_dry_run_reports_and_writes_nothing(self):
        from io import StringIO
        from django.core.management import call_command
        from warships.models import BattleObservation
        for d in (5, 4, 3, 2, 1):
            self._obs(days_ago=d)
        out = StringIO()
        call_command(
            "prune_battle_observations",
            "--keep-per-player", "2", "--dry-run", stdout=out,
        )
        output = out.getvalue()
        self.assertIn("DRY-RUN", output)
        self.assertIn("3 observation payloads", output)
        self.assertIn("no rows written", output)
        # Confirm truly no writes.
        self.assertEqual(
            BattleObservation.objects.filter(
                player=self.player, ships_stats_json__isnull=False).count(),
            5,
        )

    def test_preserves_oldest_ranked_when_it_is_the_only_baseline(self):
        # Broadest walk-back case: the latest non-NULL-ranked observation is
        # the OLDEST row in the table, and every newer observation has NULL
        # ranked (repeated ranked-fetch failures). It must survive so
        # _hydrate_previous_ranked_snapshot's walk-back still finds a baseline.
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        oldest_ranked = self._obs(days_ago=5, ranked=True)
        for d in (4, 3, 2, 1):
            self._obs(days_ago=d)  # random-only, NULL ranked
        compact_battle_observation_payloads(keep_per_player=2)
        oldest_ranked.refresh_from_db()
        self.assertIsNotNone(oldest_ranked.ranked_ships_stats_json)

    def test_batched_loop_clears_across_multiple_batches(self):
        # batch_size=1 with 4 candidates exercises the loop's termination and
        # rowcount accounting across multiple batches (guards against an
        # affected=None / off-by-one regression on either DB backend).
        from warships.incremental_battles import (
            compact_battle_observation_payloads,
        )
        for d in (5, 4, 3, 2, 1):
            self._obs(days_ago=d)
        result = compact_battle_observation_payloads(
            keep_per_player=1, batch_size=1)
        self.assertEqual(result["cleared"], 4)
        self.assertGreaterEqual(result["batches"], 4)


class RollupDurabilityTests(TestCase):
    """Self-healing trailing window + dedup'd period rebuild for the sweeper.

    Runbook: agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md
    """

    def setUp(self):
        self.player = Player.objects.create(
            name="durability_test", player_id=77777, realm="na",
            pvp_battles=100,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan",
            ship_type="Battleship", tier=10,
        )
        self.today = django_timezone.now().date()
        self.yesterday = self.today - timedelta(days=1)

    def _event_on(self, target_date, *, battles=1, mode="random",
                  season_id=None, survived=True):
        a = BattleObservation.objects.create(player=self.player, pvp_battles=0)
        b = BattleObservation.objects.create(player=self.player, pvp_battles=0)
        ev = BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            battles_delta=battles, wins_delta=battles, frags_delta=1,
            damage_delta=10_000, xp_delta=500, survived=survived,
            mode=mode, season_id=season_id,
            from_observation=a, to_observation=b,
        )
        # detected_at is auto_now_add; override to land the event on the
        # intended capture day (naive UTC — USE_TZ=False project).
        noon = datetime(target_date.year, target_date.month, target_date.day, 12)
        BattleEvent.objects.filter(pk=ev.pk).update(detected_at=noon)
        return ev

    def _run_sweeper(self, lookback=3, target_date_iso=None):
        from warships.tasks import roll_up_player_daily_ship_stats_task
        with mock.patch.dict("os.environ", {
            "BATTLE_HISTORY_ROLLUP_ENABLED": "1",
            "BATTLE_HISTORY_ROLLUP_LOOKBACK_DAYS": str(lookback),
        }):
            kwargs = {"target_date_iso": target_date_iso} if target_date_iso else {}
            return roll_up_player_daily_ship_stats_task.apply(kwargs=kwargs).get()

    def test_sweeper_rebuilds_exactly_last_n_days(self):
        for d in range(0, 4):  # yesterday, -1, -2, -3
            self._event_on(self.yesterday - timedelta(days=d), battles=1)
        result = self._run_sweeper(lookback=3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["days_rebuilt"], 3)
        built = set(PlayerDailyShipStats.objects.values_list("date", flat=True))
        for d in range(0, 3):
            self.assertIn(self.yesterday - timedelta(days=d), built)
        # The 4th-oldest day is outside the lookback window — not built.
        self.assertNotIn(self.yesterday - timedelta(days=3), built)

    def test_sweeper_does_not_touch_days_outside_window(self):
        canary = PlayerDailyShipStats.objects.create(
            player=self.player, date=self.yesterday - timedelta(days=40),
            ship_id=42, ship_name="Yamato", battles=99, wins=99, frags=99,
        )
        self._event_on(self.yesterday, battles=1)
        self._run_sweeper(lookback=3)
        canary.refresh_from_db()
        self.assertEqual(canary.battles, 99)

    def test_self_heal_restores_in_window_hole(self):
        self._event_on(self.yesterday, battles=2)
        self._run_sweeper(lookback=3)
        # Simulate an outage that left a hole on a day now inside the window.
        PlayerDailyShipStats.objects.filter(date=self.yesterday).delete()
        self.assertFalse(
            PlayerDailyShipStats.objects.filter(date=self.yesterday).exists())
        # The next nightly run re-closes the trailing window.
        self._run_sweeper(lookback=3)
        row = PlayerDailyShipStats.objects.get(date=self.yesterday)
        self.assertEqual(row.battles, 2)

    def test_sweeper_idempotent_on_rerun(self):
        self._event_on(self.yesterday, battles=2)
        self._run_sweeper(lookback=3)
        first = PlayerDailyShipStats.objects.count()
        self._run_sweeper(lookback=3)
        self.assertEqual(PlayerDailyShipStats.objects.count(), first)

    def test_explicit_target_date_collapses_window_to_one_day(self):
        self._event_on(self.yesterday, battles=1)
        self._event_on(self.yesterday - timedelta(days=1), battles=1)
        # Even with a wide lookback env, an explicit date rebuilds only that day.
        result = self._run_sweeper(
            lookback=7, target_date_iso=self.yesterday.isoformat())
        self.assertEqual(result["days_rebuilt"], 1)
        built = set(PlayerDailyShipStats.objects.values_list("date", flat=True))
        self.assertEqual(built, {self.yesterday})

    def test_sweeper_skipped_when_gate_off(self):
        from warships.tasks import roll_up_player_daily_ship_stats_task
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_ROLLUP_ENABLED": "0"},
        ):
            result = roll_up_player_daily_ship_stats_task.apply().get()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "rollup-disabled")

    def test_sweeper_builds_daily_only(self):
        # The weekly/monthly/yearly rollup tier was removed 2026-06-15; the
        # nightly sweeper now rebuilds the daily layer only and the result
        # carries no "period" key.
        self._event_on(self.yesterday, battles=2)
        result = self._run_sweeper(lookback=3)
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("period", result)
        self.assertTrue(
            PlayerDailyShipStats.objects.filter(date=self.yesterday).exists())


class RollupReconciliationTests(TestCase):
    """Alert-only reconciliation of PlayerDailyShipStats vs BattleEvent.

    Runbook: agents/runbooks/runbook-battle-history-rollup-durability-2026-06-06.md
    """

    def setUp(self):
        self.player = Player.objects.create(
            name="recon_test", player_id=88888, realm="na", pvp_battles=100,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan",
            ship_type="Battleship", tier=10,
        )
        self.today = django_timezone.now().date()
        self.day = self.today - timedelta(days=2)

    def _event_on(self, target_date, *, battles=1, mode="random",
                  season_id=None):
        a = BattleObservation.objects.create(player=self.player, pvp_battles=0)
        b = BattleObservation.objects.create(player=self.player, pvp_battles=0)
        ev = BattleEvent.objects.create(
            player=self.player, ship_id=42, ship_name="Yamato",
            battles_delta=battles, wins_delta=battles, frags_delta=1,
            damage_delta=10_000, xp_delta=500, survived=True,
            mode=mode, season_id=season_id,
            from_observation=a, to_observation=b,
        )
        noon = datetime(target_date.year, target_date.month, target_date.day, 12)
        BattleEvent.objects.filter(pk=ev.pk).update(detected_at=noon)
        return ev

    def _pds(self, target_date, *, battles, mode="random", season_id=None):
        return PlayerDailyShipStats.objects.create(
            player=self.player, date=target_date, ship_id=42,
            ship_name="Yamato", battles=battles, wins=battles, frags=1,
            mode=mode, season_id=season_id,
        )

    def test_detects_missing_pds(self):
        self._event_on(self.day, battles=3)
        report = reconcile_daily_rollup_coverage(audit_days=30)
        self.assertEqual(len(report["discrepancies"]), 1)
        d = report["discrepancies"][0]
        self.assertEqual(d["date"], str(self.day))
        self.assertEqual(d["mode"], "random")
        self.assertEqual(d["be_battles"], 3)
        self.assertEqual(d["pds_battles"], 0)
        self.assertEqual(d["delta"], 3)

    def test_detects_undercount(self):
        self._event_on(self.day, battles=5)
        self._pds(self.day, battles=2)
        report = reconcile_daily_rollup_coverage(audit_days=30)
        self.assertEqual(len(report["discrepancies"]), 1)
        self.assertEqual(report["discrepancies"][0]["delta"], 3)

    def test_clean_when_consistent(self):
        self._event_on(self.day, battles=4)
        self._pds(self.day, battles=4)
        report = reconcile_daily_rollup_coverage(audit_days=30)
        self.assertEqual(report["discrepancies"], [])

    def test_ignores_zero_battle_days(self):
        self._event_on(self.day, battles=0)
        report = reconcile_daily_rollup_coverage(audit_days=30)
        self.assertEqual(report["discrepancies"], [])

    def test_mode_partitioned(self):
        # Random reconciles fine; ranked has a hole on the same day.
        self._event_on(self.day, battles=2, mode="random")
        self._pds(self.day, battles=2, mode="random")
        self._event_on(self.day, battles=3, mode="ranked", season_id=10)
        report = reconcile_daily_rollup_coverage(audit_days=30)
        self.assertEqual(len(report["discrepancies"]), 1)
        self.assertEqual(report["discrepancies"][0]["mode"], "ranked")
        self.assertEqual(report["discrepancies"][0]["delta"], 3)

    def test_performs_no_writes(self):
        self._event_on(self.day, battles=3)
        before = PlayerDailyShipStats.objects.count()
        reconcile_daily_rollup_coverage(audit_days=30)
        self.assertEqual(PlayerDailyShipStats.objects.count(), before)

    def test_excludes_today(self):
        # An event captured today is mid-window and must not be flagged.
        self._event_on(self.today, battles=3)
        report = reconcile_daily_rollup_coverage(audit_days=30)
        self.assertEqual(report["discrepancies"], [])

    def test_task_runs_independent_of_rollup_gate(self):
        from warships.tasks import reconcile_battle_history_rollup_task
        self._event_on(self.day, battles=3)
        # RECONCILE on, ROLLUP off — the task must still detect the hole.
        with mock.patch.dict("os.environ", {
            "BATTLE_HISTORY_RECONCILE_ENABLED": "1",
            "BATTLE_HISTORY_ROLLUP_ENABLED": "0",
        }):
            result = reconcile_battle_history_rollup_task.apply().get()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["discrepancies"]), 1)

    def test_task_skipped_when_reconcile_gate_off(self):
        from warships.tasks import reconcile_battle_history_rollup_task
        with mock.patch.dict(
            "os.environ", {"BATTLE_HISTORY_RECONCILE_ENABLED": "0"},
        ):
            result = reconcile_battle_history_rollup_task.apply().get()
        self.assertEqual(result["status"], "skipped")


class FloorBattlesJsonRefreshTests(TestCase):
    """The observation path can reuse its `ships/stats` payload to refresh the
    player's displayed `battles_json` (+ `battles_updated_at`) with no second WG
    call — opt-in via `refresh_battles_json=True`, kill-switchable, never allowed
    to break the observation write. See runbook-floor-battles-json-refresh."""

    def setUp(self):
        self.player = Player.objects.create(
            name="floorbench", player_id=555000555, realm="na",
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan",
            ship_type="Battleship", tier=10,
        )

    def _full_ship_payload(self, *, battles=100):
        # Full WG ships/stats shape: top-level `battles`/`distance` + `pvp` block.
        return [{
            "ship_id": 42, "battles": battles, "distance": 12345,
            "pvp": {"battles": battles, "wins": 50, "losses": 50, "frags": 80,
                    "damage_dealt": 1_000_000, "xp": 80_000,
                    "planes_killed": 0, "survived_battles": 60},
        }]

    @mock.patch("warships.data.apply_battles_json")
    def test_refresh_true_calls_apply(self, m_apply):
        record_observation_from_payloads(
            self.player, ship_data=self._full_ship_payload(),
            refresh_battles_json=True)
        m_apply.assert_called_once()

    @mock.patch("warships.data.apply_battles_json")
    def test_default_does_not_refresh(self, m_apply):
        record_observation_from_payloads(
            self.player, ship_data=self._full_ship_payload())
        m_apply.assert_not_called()

    @mock.patch("warships.data.apply_battles_json")
    def test_kill_switch_disables_refresh(self, m_apply):
        with mock.patch.dict(
            "os.environ", {"FLOOR_REFRESH_BATTLES_JSON_ENABLED": "0"},
        ):
            record_observation_from_payloads(
                self.player, ship_data=self._full_ship_payload(),
                refresh_battles_json=True)
        m_apply.assert_not_called()

    @mock.patch("warships.data.apply_battles_json")
    def test_empty_ship_data_skips_refresh(self, m_apply):
        # A transient empty fetch must never blank a player's battles_json.
        record_observation_from_payloads(
            self.player, ship_data=[], refresh_battles_json=True)
        m_apply.assert_not_called()

    @mock.patch("warships.data.apply_battles_json")
    def test_hidden_player_skips_refresh(self, m_apply):
        # player_data with hidden_profile → snapshot None → skipped before apply.
        record_observation_from_payloads(
            self.player, player_data={"hidden_profile": True},
            ship_data=self._full_ship_payload(), refresh_battles_json=True)
        m_apply.assert_not_called()

    @mock.patch("warships.data.apply_battles_json")
    def test_refresh_runs_after_observation_is_written(self, m_apply):
        """Same race as the visit path: `apply_battles_json` bumps
        `battles_updated_at` (the X-Player-Refresh-Pending anchor), so it must
        run only after the observation/diff work — not before it."""
        seen = {}

        def record_state(*args, **kwargs):
            seen["obs_count_at_refresh"] = BattleObservation.objects.filter(
                player=self.player).count()

        m_apply.side_effect = record_state
        record_observation_from_payloads(
            self.player, ship_data=self._full_ship_payload(),
            refresh_battles_json=True)
        m_apply.assert_called_once()
        self.assertEqual(
            seen["obs_count_at_refresh"], 1,
            "battles_json refresh ran before the observation was written",
        )

    @mock.patch("warships.data.apply_battles_json")
    def test_refresh_runs_after_events_on_advance(self, m_apply):
        """On an advance, the refresh (and its battles_updated_at bump) must
        follow the BattleEvent writes so the pending header can only clear
        once the battle-history payload is rebuildable."""
        record_observation_from_payloads(
            self.player, ship_data=self._full_ship_payload())  # baseline
        self.player.pvp_battles = 101
        self.player.pvp_wins = 51
        self.player.save()
        payload = self._full_ship_payload(battles=101)
        payload[0]["pvp"]["wins"] = 51
        seen = {}

        def record_state(*args, **kwargs):
            seen["events_at_refresh"] = BattleEvent.objects.filter(
                player=self.player).count()

        m_apply.side_effect = record_state
        record_observation_from_payloads(
            self.player, ship_data=payload, refresh_battles_json=True)
        m_apply.assert_called_once()
        self.assertEqual(
            seen["events_at_refresh"], 1,
            "battles_json refresh ran before the BattleEvent write",
        )

    def test_apply_battles_json_builds_and_advances_timestamp(self):
        from warships.data import apply_battles_json
        self.assertIsNone(self.player.battles_updated_at)
        apply_battles_json(self.player, self._full_ship_payload(battles=123),
                           realm="na")
        self.player.refresh_from_db()
        self.assertTrue(self.player.battles_json)
        self.assertEqual(self.player.battles_json[0]["ship_name"], "Yamato")
        self.assertEqual(self.player.battles_json[0]["pvp_battles"], 123)
        self.assertIsNotNone(self.player.battles_updated_at)

    def test_floor_path_end_to_end_refreshes_battles_json(self):
        # Full path: record_observation_from_payloads(refresh_battles_json=True)
        # with the flag on actually populates battles_json (no mock).
        self.assertIsNone(self.player.battles_updated_at)
        record_observation_from_payloads(
            self.player, ship_data=self._full_ship_payload(battles=77),
            refresh_battles_json=True)
        self.player.refresh_from_db()
        self.assertTrue(self.player.battles_json)
        self.assertIsNotNone(self.player.battles_updated_at)


class PruneInactivePlayerBattlesJsonTests(TestCase):
    """Disk-reclaim prune of battles_json on long-inactive players.

    Verifies only inactive (> cutoff) + visible + non-PENDING rows are NULLed,
    derived chart columns survive, the boundary is exclusive, --dry-run writes
    nothing, --max-rows caps, and the ENRICH_MAX_INACTIVE_DAYS guard refuses an
    unsafe --inactive-days. Scope:
    agents/work-items/scope-battles-json-prune-rerun-2026-06-15.md
    """

    SAMPLE = [{"ship_id": 1, "pvp": {"battles": 1}}]

    def _player(self, *, pid, days_inactive=None, hidden=False,
                status=Player.ENRICHMENT_ENRICHED, with_blob=True,
                derived=True):
        kwargs = dict(
            name=f"prune_{pid}", player_id=pid, realm="na", pvp_battles=600,
            is_hidden=hidden, enrichment_status=status,
            battles_json=(self.SAMPLE if with_blob else None),
        )
        if derived:
            kwargs.update(
                tiers_json=[{"tier": 10}],
                type_json=[{"type": "bb"}],
                randoms_json=[{"r": 1}],
                activity_json=[{"a": 1}],
            )
        if days_inactive is not None:
            kwargs["last_battle_date"] = (
                date.today() - timedelta(days=days_inactive))
        return Player.objects.create(**kwargs)

    def _run(self, **kw):
        from warships.incremental_battles import (
            prune_inactive_player_battles_json,
        )
        kw.setdefault("inactive_days", 180)
        kw.setdefault("max_inactive_days", 7)
        return prune_inactive_player_battles_json(**kw)

    def test_nulls_only_inactive_visible_nonpending(self):
        inactive = self._player(pid=1, days_inactive=200)
        active = self._player(pid=2, days_inactive=3)
        hidden = self._player(pid=3, days_inactive=200, hidden=True)
        pending = self._player(
            pid=4, days_inactive=200, status=Player.ENRICHMENT_PENDING)
        never = self._player(pid=5, days_inactive=None)  # last_battle_date NULL

        result = self._run()
        self.assertEqual(result["cleared"], 1)

        inactive.refresh_from_db()
        self.assertIsNone(inactive.battles_json)
        # Derived chart columns survive on the pruned row.
        self.assertIsNotNone(inactive.tiers_json)
        self.assertIsNotNone(inactive.type_json)
        self.assertIsNotNone(inactive.randoms_json)
        self.assertIsNotNone(inactive.activity_json)

        for p in (active, hidden, pending, never):
            p.refresh_from_db()
            self.assertIsNotNone(p.battles_json, f"pid={p.player_id} pruned")

    def test_dry_run_writes_nothing_and_reports_pending_intersection(self):
        self._player(pid=1, days_inactive=200)
        self._player(pid=2, days_inactive=200)
        # A PENDING row in the same inactive band — excluded from candidates,
        # but reported as the gating intersection count.
        self._player(
            pid=3, days_inactive=200, status=Player.ENRICHMENT_PENDING)

        result = self._run(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["pending_intersection"], 1)
        # No writes.
        self.assertEqual(
            Player.objects.filter(battles_json__isnull=False).count(), 3)

    def test_max_rows_caps(self):
        for pid in range(1, 6):
            self._player(pid=pid, days_inactive=200)
        result = self._run(max_rows=2)
        self.assertEqual(result["cleared"], 2)
        self.assertEqual(
            Player.objects.filter(battles_json__isnull=False).count(), 3)

    def test_inactive_days_boundary_is_exclusive(self):
        # last_battle_date == cutoff (exactly inactive_days ago) is KEPT;
        # one day older is pruned.
        on_boundary = self._player(pid=1, days_inactive=180)
        past_boundary = self._player(pid=2, days_inactive=181)
        result = self._run()
        self.assertEqual(result["cleared"], 1)
        on_boundary.refresh_from_db()
        past_boundary.refresh_from_db()
        self.assertIsNotNone(on_boundary.battles_json)
        self.assertIsNone(past_boundary.battles_json)

    def test_guard_refuses_when_inactive_days_not_above_ceiling(self):
        self._player(pid=1, days_inactive=400)
        with self.assertRaises(ValueError):
            self._run(inactive_days=365, max_inactive_days=365)
        with self.assertRaises(ValueError):
            self._run(inactive_days=100, max_inactive_days=365)
        # Nothing written despite a clear candidate.
        self.assertEqual(
            Player.objects.filter(battles_json__isnull=False).count(), 1)

    def test_batched_loop_clears_across_multiple_batches(self):
        for pid in range(1, 6):
            self._player(pid=pid, days_inactive=200)
        result = self._run(batch_size=1)
        self.assertEqual(result["cleared"], 5)
        self.assertGreaterEqual(result["batches"], 5)

    def test_management_command_dry_run_reports_and_writes_nothing(self):
        self._player(pid=1, days_inactive=200)
        self._player(pid=2, days_inactive=200)
        # A PENDING in-band row exercises the excluded-by-guard count + label.
        self._player(
            pid=3, days_inactive=200, status=Player.ENRICHMENT_PENDING)
        out = StringIO()
        with mock.patch.dict(
                "os.environ", {"ENRICH_MAX_INACTIVE_DAYS": "7"}):
            call_command(
                "prune_inactive_player_battles_json",
                "--inactive-days", "180", "--dry-run", stdout=out,
            )
        output = out.getvalue()
        self.assertIn("DRY-RUN", output)
        self.assertIn("2 player battles_json", output)
        self.assertIn("no rows written", output)
        # The PENDING-in-band count is framed as excluded-by-guard, NOT as a
        # failure signal (operator go/no-go honesty).
        self.assertIn("EXCLUDED by guard", output)
        self.assertNotIn("(expect 0)", output)
        # 3 visible rows with battles_json (2 candidates + 1 excluded PENDING).
        self.assertEqual(
            Player.objects.filter(battles_json__isnull=False).count(), 3)

    def test_management_command_guard_refuses_at_default_env(self):
        # With the env at its 365 default, the 180d default refuses to run.
        from django.core.management.base import CommandError
        self._player(pid=1, days_inactive=400)
        out = StringIO()
        with mock.patch.dict(
                "os.environ", {"ENRICH_MAX_INACTIVE_DAYS": "365"}):
            with self.assertRaises(CommandError):
                call_command(
                    "prune_inactive_player_battles_json",
                    "--dry-run", stdout=out,
                )
        self.assertEqual(
            Player.objects.filter(battles_json__isnull=False).count(), 1)

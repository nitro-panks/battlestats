"""Tests for the bulk-batched battle-observation capture engine (R1).

Spec: agents/runbooks/runbook-bulk-battle-observation-capture-2026-06-06.md

The engine `record_observations_bulk()` feeds bulk WG slices into the same
zero-WG persistence core (`record_observation_from_payloads`) as the legacy
per-player path, so the central guarantee is **parity-by-construction**: given
identical payloads, the bulk path must write byte-identical observations and
events to the legacy `record_observation_and_diff` path. The parity test below
is the load-bearing assertion; the rest cover the bulk-only error taxonomy (D5)
and per-player slice handling (D4).
"""
import calendar
import io
import json
import os
from datetime import datetime, timedelta
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from warships.incremental_battles import (
    record_observation_and_diff,
    record_observation_from_payloads,
    record_observations_bulk,
)
from warships.models import BattleEvent, BattleObservation, Player, Ship

ACCT_PATH = "warships.api.players._bulk_fetch_account_info"
ACCT_FALLBACK_PATH = "warships.api.players._per_player_account_fallback"
SHIP_PATH = "warships.api.ships._bulk_fetch_ship_stats"
SHIP_FALLBACK_PATH = "warships.api.ships._per_player_ship_fallback"
SINGLE_ACCT_PATH = "warships.api.players._fetch_player_personal_data"
SINGLE_SHIP_PATH = "warships.api.ships._fetch_ship_stats_for_player"


def _account_payload(*, battles, wins=0, losses=0, frags=0, survived=0,
                     last_battle_time=None, hidden=False):
    """account/info per-player value, matching the WG `account/info/` shape.

    `last_battle_time` defaults to None: coerce_observation_payload turns a
    truthy timestamp into a tz-aware datetime, which the sqlite test backend
    rejects under USE_TZ=False (prod Postgres accepts it). It is irrelevant to
    ships_stats_json / event parity, so the tests leave it unset.
    """
    return {
        "hidden_profile": hidden,
        "last_battle_time": last_battle_time,
        "statistics": {"pvp": {
            "battles": battles, "wins": wins, "losses": losses,
            "frags": frags, "survived_battles": survived,
        }},
    }


def _ship_payload(*, battles, wins=0, losses=0, frags=0, damage=0, xp=0,
                  planes=0, survived=0, ship_id=42):
    """ships/stats per-player value (a list of per-ship dicts)."""
    return [{"ship_id": ship_id, "pvp": {
        "battles": battles, "wins": wins, "losses": losses, "frags": frags,
        "damage_dealt": damage, "xp": xp, "planes_killed": planes,
        "survived_battles": survived,
    }}]


class BulkObservationParityTests(TestCase):
    """The bulk path must be byte-identical to the legacy single-fetch path."""

    def setUp(self):
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )

    def _seed_player_with_baseline(self, *, player_id):
        player = Player.objects.create(
            name=f"p{player_id}", player_id=player_id, realm="na",
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )
        # Identical baseline observation for both arms of the parity test.
        record_observation_from_payloads(
            player,
            player_data=_account_payload(battles=100, wins=50, losses=50,
                                         frags=80, survived=60),
            ship_data=_ship_payload(battles=100, wins=50, frags=80,
                                    damage=1_000_000, xp=80_000, survived=60),
        )
        return player

    def test_bulk_matches_legacy_observation_and_events(self):
        legacy = self._seed_player_with_baseline(player_id=1111111111)
        bulk = self._seed_player_with_baseline(player_id=2222222222)

        after_acct = _account_payload(battles=101, wins=51, losses=50, frags=82,
                                      survived=61)
        after_ships = _ship_payload(battles=101, wins=51, frags=82,
                                    damage=1_048_000, xp=81_500, survived=61)

        # Legacy single-fetch path.
        with mock.patch(SINGLE_ACCT_PATH, return_value=after_acct), \
                mock.patch(SINGLE_SHIP_PATH, return_value=after_ships):
            legacy_result = record_observation_and_diff(
                player_id=legacy.player_id, realm="na")

        # Bulk path — same payloads, keyed by player_id.
        with mock.patch(
            ACCT_PATH, return_value=({str(bulk.player_id): after_acct}, None)
        ), mock.patch(
            SHIP_PATH, return_value=({str(bulk.player_id): after_ships}, None)
        ):
            bulk_tally = record_observations_bulk([bulk.player_id], realm="na")

        self.assertEqual(legacy_result["status"], "completed")
        self.assertEqual(bulk_tally["completed"], 1)
        self.assertEqual(bulk_tally["events"], 1)
        self.assertFalse(bulk_tally["aborted"])

        # Parity 1: the newly written observation payloads are identical.
        legacy_obs = (
            BattleObservation.objects.filter(player=legacy)
            .order_by("-observed_at").first()
        )
        bulk_obs = (
            BattleObservation.objects.filter(player=bulk)
            .order_by("-observed_at").first()
        )
        self.assertEqual(legacy_obs.ships_stats_json, bulk_obs.ships_stats_json)
        self.assertEqual(legacy_obs.pvp_battles, bulk_obs.pvp_battles)
        self.assertEqual(legacy_obs.pvp_wins, bulk_obs.pvp_wins)

        # Parity 2: identical BattleEvent rows (the diff result).
        def _event_tuple(p):
            e = BattleEvent.objects.get(player=p)
            return (e.ship_id, e.ship_name, e.battles_delta, e.wins_delta,
                    e.frags_delta, e.damage_delta, e.xp_delta, e.survived)

        self.assertEqual(_event_tuple(legacy), _event_tuple(bulk))

        # The bulk path tags its source so audits can isolate it (D9); the
        # legacy path uses the default 'poll'. (Not a parity field.)
        self.assertEqual(bulk_obs.source, BattleObservation.SOURCE_BULK_FLOOR)
        self.assertEqual(legacy_obs.source, BattleObservation.SOURCE_POLL)

    def test_parity_on_real_production_path_ships_fallback(self):
        # PRODUCTION REALITY: WG ships/stats cannot bulk — it always returns
        # INVALID_ACCOUNT_ID, so the engine falls back to per-player ships.
        # This proves parity holds on the ACTUAL path (account/info bulks,
        # ships arrives via _per_player_ship_fallback), not just the synthetic
        # multi-key-bulk path the other tests mock.
        legacy = self._seed_player_with_baseline(player_id=1212121212)
        bulk = self._seed_player_with_baseline(player_id=2121212121)

        after_acct = _account_payload(battles=101, wins=51, losses=50, frags=82,
                                      survived=61)
        after_ships = _ship_payload(battles=101, wins=51, frags=82,
                                    damage=1_048_000, xp=81_500, survived=61)

        with mock.patch(SINGLE_ACCT_PATH, return_value=after_acct), \
                mock.patch(SINGLE_SHIP_PATH, return_value=after_ships):
            record_observation_and_diff(player_id=legacy.player_id, realm="na")

        with mock.patch(
            ACCT_PATH, return_value=({str(bulk.player_id): after_acct}, None)
        ), mock.patch(
            SHIP_PATH, return_value=({}, "INVALID_ACCOUNT_ID")
        ), mock.patch(
            SHIP_FALLBACK_PATH,
            return_value={str(bulk.player_id): after_ships},
        ) as fb:
            record_observations_bulk([bulk.player_id], realm="na")

        fb.assert_called_once()  # the real path went through the fallback
        legacy_obs = (BattleObservation.objects.filter(player=legacy)
                      .order_by("-observed_at").first())
        bulk_obs = (BattleObservation.objects.filter(player=bulk)
                    .order_by("-observed_at").first())
        self.assertEqual(legacy_obs.ships_stats_json, bulk_obs.ships_stats_json)
        le = BattleEvent.objects.get(player=legacy)
        be = BattleEvent.objects.get(player=bulk)
        self.assertEqual(
            (le.battles_delta, le.damage_delta, le.xp_delta),
            (be.battles_delta, be.damage_delta, be.xp_delta),
        )


class BulkObservationEngineTests(TestCase):
    """Per-chunk behaviour: happy path, D4 slice handling, D5 taxonomy."""

    def setUp(self):
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )

    def _make_player(self, player_id, *, realm="na"):
        return Player.objects.create(
            name=f"p{player_id}", player_id=player_id, realm=realm,
            pvp_battles=100, pvp_wins=50, pvp_losses=50, pvp_frags=80,
            pvp_survived_battles=60,
        )

    def _baseline(self, player):
        record_observation_from_payloads(
            player,
            player_data=_account_payload(battles=100, wins=50, losses=50,
                                         frags=80, survived=60),
            ship_data=_ship_payload(battles=100, wins=50, frags=80,
                                    damage=1_000_000, xp=80_000, survived=60),
        )

    def test_production_path_bulk_ships_falls_back_per_player(self):
        # THE production path: account/info bulks, but ships/stats returns
        # INVALID_ACCOUNT_ID (it cannot bulk — verified on live WG), so the
        # whole chunk falls back to per-player ships. Assert both players are
        # still captured with the correct per-ship battle deltas.
        p1 = self._make_player(8001)
        p2 = self._make_player(8002)
        self._baseline(p1)
        self._baseline(p2)
        acct = {
            "8001": _account_payload(battles=102, wins=52, frags=84, survived=62),
            "8002": _account_payload(battles=101, wins=51, frags=82, survived=61),
        }
        fallback_ships = {
            "8001": _ship_payload(battles=102, wins=52, frags=84,
                                  damage=2_000_000, xp=160_000, survived=62),
            "8002": _ship_payload(battles=101, wins=51, frags=82,
                                  damage=1_048_000, xp=81_500, survived=61),
        }
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=({}, "INVALID_ACCOUNT_ID")), \
                mock.patch(SHIP_FALLBACK_PATH, return_value=fallback_ships) as fb:
            tally = record_observations_bulk([8001, 8002], realm="na")

        fb.assert_called_once_with([8001, 8002], "na")
        self.assertEqual(tally["completed"], 2)
        self.assertEqual(tally["events"], 2)
        self.assertEqual(
            BattleEvent.objects.filter(player=p1).get().battles_delta, 2)
        self.assertEqual(
            BattleEvent.objects.filter(player=p2).get().battles_delta, 1)

    def test_happy_two_player_chunk_emits_events(self):
        # NOTE: this mocks bulk ships returning a multi-key dict, which CANNOT
        # happen against live WG (ships/stats rejects >=2 ids). It is a valid
        # unit test of the per-player slice logic given such a response; the
        # real production flow is covered by
        # test_production_path_bulk_ships_falls_back_per_player above.
        p1 = self._make_player(3001)
        p2 = self._make_player(3002)
        self._baseline(p1)
        self._baseline(p2)

        acct = {
            "3001": _account_payload(battles=102, wins=52, frags=84, survived=62),
            "3002": _account_payload(battles=101, wins=51, frags=82, survived=61),
        }
        ships = {
            "3001": _ship_payload(battles=102, wins=52, frags=84,
                                  damage=2_000_000, xp=160_000, survived=62),
            "3002": _ship_payload(battles=101, wins=51, frags=82,
                                  damage=1_048_000, xp=81_500, survived=61),
        }
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)):
            tally = record_observations_bulk([3001, 3002], realm="na")

        self.assertEqual(tally["completed"], 2)
        self.assertEqual(tally["events"], 2)  # p1: +2 battles, p2: +1 -> 1 event each
        self.assertEqual(tally["baseline"], 0)
        self.assertEqual(BattleObservation.objects.filter(player=p1).count(), 2)
        self.assertEqual(BattleEvent.objects.filter(player=p1).get().battles_delta, 2)
        self.assertEqual(BattleEvent.objects.filter(player=p2).get().battles_delta, 1)

    def test_empty_ships_is_genuine_baseline(self):
        p = self._make_player(3010)
        acct = {"3010": _account_payload(battles=0)}
        ships = {"3010": []}  # present, no ships -> baseline
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)):
            tally = record_observations_bulk([3010], realm="na")

        self.assertEqual(tally["completed"], 1)
        self.assertEqual(tally["baseline"], 1)
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 1)

    def test_missing_ships_key_skips_without_breaking_prior(self):
        p = self._make_player(3020)
        self._baseline(p)  # so a prior exists
        acct = {"3020": _account_payload(battles=101, wins=51)}
        ships = {}  # pid absent from ships response -> skip this tick
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)):
            tally = record_observations_bulk([3020], realm="na")

        self.assertEqual(tally["skipped_missing"], 1)
        self.assertEqual(tally["completed"], 0)
        # No empty-ships observation was written (would trip random_prior_broken).
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 1)

    def test_skip_sentinel_from_fallback_skips_tick(self):
        p = self._make_player(3030)
        self._baseline(p)
        acct = {"3030": _account_payload(battles=101)}
        ships = {"3030": "SKIP"}  # transient per-player failure sentinel
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)):
            tally = record_observations_bulk([3030], realm="na")

        self.assertEqual(tally["skipped_missing"], 1)
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 1)

    def test_hidden_profile_is_skipped(self):
        p = self._make_player(3040)
        acct = {"3040": _account_payload(battles=100, hidden=True)}
        ships = {"3040": _ship_payload(battles=100)}
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)):
            tally = record_observations_bulk([3040], realm="na")

        self.assertEqual(tally["completed"], 0)
        self.assertEqual(tally["wg_failed"], 1)  # hidden bucketed like legacy
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 0)

    def test_missing_account_key_skips(self):
        p = self._make_player(3050)
        acct = {}  # pid absent from account response -> skip
        ships = {"3050": _ship_payload(battles=100)}
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)):
            tally = record_observations_bulk([3050], realm="na")

        self.assertEqual(tally["skipped_missing"], 1)
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 0)

    def test_unknown_player_id_counts_not_found(self):
        # 9999 has no Player row.
        acct = {"9999": _account_payload(battles=100)}
        ships = {"9999": _ship_payload(battles=100)}
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)):
            tally = record_observations_bulk([9999], realm="na")

        self.assertEqual(tally["not_found"], 1)
        self.assertEqual(tally["completed"], 0)

    def test_invalid_account_id_triggers_per_player_ship_fallback(self):
        p = self._make_player(3060)
        acct = {"3060": _account_payload(battles=100)}
        fallback_ships = {"3060": _ship_payload(battles=100)}
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=({}, "INVALID_ACCOUNT_ID")), \
                mock.patch(SHIP_FALLBACK_PATH, return_value=fallback_ships) as fb:
            tally = record_observations_bulk([3060], realm="na")

        fb.assert_called_once()
        self.assertEqual(tally["completed"], 1)
        self.assertEqual(tally["baseline"], 1)

    def test_407_aborts_sweep_and_persists_partial(self):
        p1 = self._make_player(3070)
        p2 = self._make_player(3071)
        self._baseline(p1)
        self._baseline(p2)

        # 200 ids -> two chunks of 100. Chunk 1 succeeds, chunk 2 returns 407.
        chunk1 = [3070] + list(range(4000, 4099))   # 100 ids
        chunk2 = [3071] + list(range(4100, 4199))   # 100 ids
        ids = chunk1 + chunk2

        def acct_side(chunk_ids, realm):
            if 3070 in chunk_ids:
                return {"3070": _account_payload(battles=101, wins=51)}, None
            return {}, "REQUEST_LIMIT_EXCEEDED"

        def ship_side(chunk_ids, realm):
            if 3070 in chunk_ids:
                return {"3070": _ship_payload(battles=101, wins=51, survived=1,
                                              damage=42_000)}, None
            return {}, "REQUEST_LIMIT_EXCEEDED"

        with mock.patch(ACCT_PATH, side_effect=acct_side), \
                mock.patch(SHIP_PATH, side_effect=ship_side):
            tally = record_observations_bulk(ids, realm="na")

        self.assertTrue(tally["aborted"])
        self.assertEqual(tally["status"], "aborted")
        # Chunk 1 persisted (p1 got its observation); chunk 2 never ran.
        self.assertEqual(tally["completed"], 1)
        self.assertEqual(BattleObservation.objects.filter(player=p1).count(), 2)
        self.assertEqual(BattleObservation.objects.filter(player=p2).count(), 1)

    def test_transient_error_skips_chunk(self):
        p = self._make_player(3080)
        self._baseline(p)
        with mock.patch(ACCT_PATH, return_value=({}, None)), \
                mock.patch(SHIP_PATH, return_value=({}, "TRANSPORT_ERROR")):
            tally = record_observations_bulk([3080], realm="na")

        self.assertFalse(tally["aborted"])
        self.assertEqual(tally["wg_failed"], 1)  # chunk of 1 skipped
        self.assertEqual(tally["completed"], 0)
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 1)

    def test_one_bad_player_does_not_roll_back_chunk(self):
        good = self._make_player(3090)
        bad = self._make_player(3091)
        self._baseline(good)
        self._baseline(bad)
        acct = {
            "3090": _account_payload(battles=101, wins=51),
            "3091": _account_payload(battles=101, wins=51),
        }
        ships = {
            "3090": _ship_payload(battles=101, wins=51, survived=1, damage=42_000),
            "3091": _ship_payload(battles=101, wins=51, survived=1, damage=42_000),
        }
        real = record_observation_from_payloads

        def flaky(player, **kwargs):
            if player.player_id == 3091:
                raise RuntimeError("persist boom")
            return real(player, **kwargs)

        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, return_value=(ships, None)), \
                mock.patch(
                    "warships.incremental_battles.record_observation_from_payloads",
                    side_effect=flaky):
            tally = record_observations_bulk([3090, 3091], realm="na")

        self.assertEqual(tally["completed"], 1)
        self.assertEqual(tally["other"], 1)
        # Good player committed; bad player rolled back its own txn only.
        self.assertEqual(BattleObservation.objects.filter(player=good).count(), 2)
        self.assertEqual(BattleObservation.objects.filter(player=bad).count(), 1)


CMD = "warships.management.commands.ensure_daily_battle_observations"
ENGINE_BULK = "warships.incremental_battles.record_observations_bulk"
ENGINE_RANKED = "warships.incremental_battles.record_ranked_observation_and_diff"


class BulkObservationChangeGateTests(TestCase):
    """Change-detector gate: fetch ships only for players who actually played."""

    def setUp(self):
        Ship.objects.create(
            ship_id=42, name="Yamato", nation="japan", ship_type="Battleship",
            tier=10,
        )

    def _player_with_prior(self, pid, prior_battles):
        p = Player.objects.create(
            name=f"p{pid}", player_id=pid, realm="na",
            pvp_battles=prior_battles, pvp_wins=50, pvp_losses=50,
            pvp_frags=80, pvp_survived_battles=60,
        )
        # Establish a prior observation at `prior_battles` (the gate compares
        # against the latest BattleObservation.pvp_battles, not the Player row).
        record_observation_from_payloads(
            p,
            player_data=_account_payload(battles=prior_battles, wins=50,
                                         losses=50, frags=80, survived=60),
            ship_data=_ship_payload(battles=prior_battles, wins=50, frags=80,
                                    damage=1_000_000, xp=80_000, survived=60),
        )
        return p

    def _player_no_prior(self, pid):
        return Player.objects.create(
            name=f"p{pid}", player_id=pid, realm="na",
            pvp_battles=10, pvp_wins=5, pvp_losses=5, pvp_frags=8,
            pvp_survived_battles=6,
        )

    def _ship_recorder(self):
        calls = []

        def side(ids, realm):
            calls.append(sorted(ids))
            return ({str(i): _ship_payload(battles=999, wins=1, frags=1,
                                           damage=1, xp=1, survived=1)
                     for i in ids}, None)
        return calls, side

    def test_gate_skips_unchanged_player(self):
        p = self._player_with_prior(9001, 100)
        acct = {"9001": _account_payload(battles=100)}  # no change vs prior
        calls, side = self._ship_recorder()
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, side_effect=side):
            tally = record_observations_bulk([9001], realm="na",
                                             change_gate=True)
        self.assertEqual(tally["gated_skipped"], 1)
        self.assertEqual(tally["completed"], 0)
        self.assertEqual(calls, [])  # ships endpoint never hit
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 1)

    def test_gate_fetches_for_played_player(self):
        p = self._player_with_prior(9002, 100)
        acct = {"9002": _account_payload(battles=105)}  # +5 battles
        calls, side = self._ship_recorder()
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, side_effect=side):
            tally = record_observations_bulk([9002], realm="na",
                                             change_gate=True)
        self.assertEqual(calls, [[9002]])  # ships fetched for the mover
        self.assertEqual(tally["completed"], 1)
        self.assertEqual(BattleObservation.objects.filter(player=p).count(), 2)

    def test_gate_baselines_player_with_no_prior(self):
        self._player_no_prior(9003)
        acct = {"9003": _account_payload(battles=50)}
        calls, side = self._ship_recorder()
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, side_effect=side):
            tally = record_observations_bulk([9003], realm="na",
                                             change_gate=True)
        self.assertEqual(calls, [[9003]])  # no prior → fetch a baseline
        self.assertEqual(tally["completed"], 1)
        self.assertEqual(tally["baseline"], 1)

    def test_gate_skips_hidden(self):
        self._player_with_prior(9004, 100)
        acct = {"9004": _account_payload(battles=100, hidden=True)}
        calls, side = self._ship_recorder()
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, side_effect=side):
            tally = record_observations_bulk([9004], realm="na",
                                             change_gate=True)
        self.assertEqual(calls, [])  # hidden → no ships call wasted
        self.assertEqual(tally["skipped_missing"], 1)

    def test_gate_mixed_chunk_only_fetches_movers(self):
        self._player_with_prior(9010, 100)   # will move
        self._player_with_prior(9011, 200)   # unchanged
        self._player_no_prior(9012)          # baseline
        acct = {
            "9010": _account_payload(battles=103),
            "9011": _account_payload(battles=200),
            "9012": _account_payload(battles=10),
        }
        calls, side = self._ship_recorder()
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, side_effect=side):
            tally = record_observations_bulk([9010, 9011, 9012], realm="na",
                                             change_gate=True)
        self.assertEqual(calls, [[9010, 9012]])  # mover + baseline, NOT 9011
        self.assertEqual(tally["gated_skipped"], 1)
        self.assertEqual(tally["completed"], 2)

    def test_gate_off_fetches_everyone(self):
        # Regression: with the gate off, ships are fetched even for a player
        # whose battle count did not move (preserves the pre-gate behaviour).
        self._player_with_prior(9020, 100)
        acct = {"9020": _account_payload(battles=100)}
        calls, side = self._ship_recorder()
        with mock.patch(ACCT_PATH, return_value=(acct, None)), \
                mock.patch(SHIP_PATH, side_effect=side):
            tally = record_observations_bulk([9020], realm="na",
                                             change_gate=False)
        self.assertEqual(calls, [[9020]])
        self.assertEqual(tally["gated_skipped"], 0)


CMD_MOD = "warships.management.commands.ensure_daily_battle_observations"
RANKED_ACCT = "warships.api.players._bulk_fetch_account_info"


class RankedSweepGateTests(TestCase):
    """Ranked-sweep gate: run the 3-call ranked worker only for movers."""

    def _ranked_player(self, pid, *, last_battle_time=None, observed_ago_h=10):
        p = Player.objects.create(
            name=f"p{pid}", player_id=pid, realm="na", is_hidden=False,
            last_battle_date=timezone.now().date(), pvp_battles=100,
            ranked_json=[{"season_id": 1}],
        )
        if last_battle_time is not None:
            obs = BattleObservation.objects.create(
                player=p, pvp_battles=100, last_battle_time=last_battle_time,
                ships_stats_json=[], source="poll",
            )
            # Backdate so the player is a stale candidate (auto_now_add blocks
            # setting observed_at at create time).
            BattleObservation.objects.filter(pk=obs.pk).update(
                observed_at=timezone.now() - timedelta(hours=observed_ago_h))
        return p

    def _acct(self, unix_lbt):
        return {"last_battle_time": unix_lbt,
                "statistics": {"pvp": {"battles": 1}}}

    def test_ranked_movers_keeps_movers_and_no_prior_skips_unchanged(self):
        from warships.management.commands.ensure_daily_battle_observations import (
            _ranked_movers,
        )
        lbt = datetime(2024, 1, 1, 12, 0, 0)
        base = calendar.timegm(lbt.timetuple())
        self._ranked_player(6001, last_battle_time=lbt)   # will move
        self._ranked_player(6002, last_battle_time=lbt)   # unchanged
        self._ranked_player(6003, last_battle_time=None)  # no prior obs
        acct = {
            "6001": self._acct(base + 3600),  # last_battle_time advanced
            "6002": self._acct(base),         # unchanged
            "6003": self._acct(base),         # no prior → baseline
        }
        with mock.patch(RANKED_ACCT, return_value=(acct, None)):
            movers = _ranked_movers("na", [6001, 6002, 6003])
        self.assertEqual(set(movers), {6001, 6003})

    def test_ranked_movers_skips_hidden(self):
        from warships.management.commands.ensure_daily_battle_observations import (
            _ranked_movers,
        )
        lbt = datetime(2024, 1, 1, 12, 0, 0)
        base = calendar.timegm(lbt.timetuple())
        self._ranked_player(6010, last_battle_time=lbt)
        acct = {"6010": {"hidden_profile": True,
                         "last_battle_time": base + 9999}}
        with mock.patch(RANKED_ACCT, return_value=(acct, None)):
            movers = _ranked_movers("na", [6010])
        self.assertEqual(movers, [])  # hidden → ranked worker would skip anyway

    def test_ranked_movers_fetches_all_on_bulk_error(self):
        from warships.management.commands.ensure_daily_battle_observations import (
            _ranked_movers,
        )
        self._ranked_player(6020, last_battle_time=datetime(2024, 1, 1))
        with mock.patch(RANKED_ACCT, return_value=({}, "TRANSPORT_ERROR")):
            movers = _ranked_movers("na", [6020])
        self.assertEqual(movers, [6020])  # can't read signal → sweep, never miss

    def test_command_ranked_gate_sweeps_only_movers(self):
        lbt = datetime(2024, 1, 1, 12, 0, 0)
        base = calendar.timegm(lbt.timetuple())
        self._ranked_player(6101, last_battle_time=lbt)   # mover
        self._ranked_player(6102, last_battle_time=lbt)   # unchanged
        acct = {"6101": self._acct(base + 3600), "6102": self._acct(base)}
        with mock.patch(f"{CMD_MOD}._ranked_capture_active_for_realm",
                        return_value=True), \
                mock.patch(ENGINE_BULK,
                           return_value={"completed": 0, "baseline": 0,
                                         "events": 0, "aborted": False}), \
                mock.patch(RANKED_ACCT, return_value=(acct, None)), \
                mock.patch(ENGINE_RANKED,
                           return_value={"status": "completed"}) as rw:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True, ranked_gate=True)
        swept = {c.args[0] for c in rw.call_args_list}
        self.assertEqual(swept, {6101})  # only the mover got the 3-call sweep

    def test_task_passes_random_first_when_flag_on(self):
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "1",
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS": "na",
            "BATTLE_OBSERVATION_FLOOR_RANDOM_FIRST_ENABLED": "1",
        }), mock.patch("django.core.management.call_command") as cc:
            from warships.tasks import ensure_daily_battle_observations_task
            ensure_daily_battle_observations_task.apply(args=["na"]).get()
        self.assertTrue(cc.call_args.kwargs.get("random_first"))
        self.assertEqual(cc.call_args.kwargs.get("ranked_sweep_limit"), 5000)

    def test_task_daily_ranked_skips_off_slot(self):
        # na's ranked daily slot is hour 1; at any other hour, skip_ranked=True.
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "1",
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS": "na",
            "BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED": "1",
        }), mock.patch("warships.tasks._is_ranked_daily_slot",
                       return_value=False), \
                mock.patch("django.core.management.call_command") as cc:
            from warships.tasks import ensure_daily_battle_observations_task
            ensure_daily_battle_observations_task.apply(args=["na"]).get()
        self.assertTrue(cc.call_args.kwargs.get("skip_ranked"))

    def test_task_daily_ranked_runs_on_slot(self):
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "1",
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS": "na",
            "BATTLE_OBSERVATION_FLOOR_RANKED_DAILY_ENABLED": "1",
        }), mock.patch("warships.tasks._is_ranked_daily_slot",
                       return_value=True), \
                mock.patch("django.core.management.call_command") as cc:
            from warships.tasks import ensure_daily_battle_observations_task
            ensure_daily_battle_observations_task.apply(args=["na"]).get()
        self.assertNotIn("skip_ranked", cc.call_args.kwargs)

    def test_task_passes_ranked_gate_when_flag_on(self):
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "1",
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS": "na",
            "BATTLE_OBSERVATION_FLOOR_RANKED_GATE_ENABLED": "1",
        }), mock.patch("django.core.management.call_command") as cc:
            from warships.tasks import ensure_daily_battle_observations_task
            ensure_daily_battle_observations_task.apply(args=["na"]).get()
        self.assertTrue(cc.call_args.kwargs.get("ranked_gate"))


class RandomFirstRoutingTests(TestCase):
    """Random-first routing: heavy ranked path only for current-season players.

    'Current season' = the highest Player.ranked_last_season_id in the DB (a
    2-element [max, max-1] window), so the routing tracks the live season from
    enrichment data, not seasons/info dates (which lag).
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # the current-season detector is cached 1h; isolate tests

    def _player(self, pid, *, last_season, ranked_history=False):
        return Player.objects.create(
            name=f"p{pid}", player_id=pid, realm="na", is_hidden=False,
            last_battle_date=timezone.now().date(), pvp_battles=100,
            ranked_json=([{"season_id": last_season}] if last_season
                         else ([{"season_id": 20}] if ranked_history else None)),
            ranked_last_season_id=last_season,
        )

    def test_current_ranked_season_ids(self):
        from warships.management.commands.ensure_daily_battle_observations import (
            _current_ranked_season_ids,
        )
        self.assertIsNone(_current_ranked_season_ids())  # no ranked data yet
        self._player(9001, last_season=29)
        self._player(9002, last_season=30)               # max
        self.assertEqual(_current_ranked_season_ids(), [30, 29])

    def test_ranked_active_ids_filters_to_current_season(self):
        from warships.management.commands.ensure_daily_battle_observations import (
            _ranked_active_ids,
        )
        self._player(8101, last_season=30)   # current
        self._player(8102, last_season=20)   # lapsed
        self._player(8103, last_season=None)  # never
        active = _ranked_active_ids("na", [8101, 8102, 8103], [30, 29])
        self.assertEqual(active, {8101})
        self.assertEqual(_ranked_active_ids("na", [8101], []), set())  # off-season

    def test_command_random_first_routes_only_current_season_to_ranked(self):
        self._player(8201, last_season=30)    # current (max) → ranked path
        self._player(8202, last_season=20)    # lapsed → random path
        self._player(8203, last_season=None)  # never → random path
        with mock.patch(f"{CMD}._ranked_capture_active_for_realm",
                        return_value=True), \
                mock.patch(ENGINE_BULK,
                           return_value={"completed": 0, "baseline": 0,
                                         "events": 0, "aborted": False}) as bulk_mock, \
                mock.patch(ENGINE_RANKED,
                           return_value={"status": "completed"}) as rw:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True, random_first=True)
        bulk_ids = set(bulk_mock.call_args.args[0])
        ranked_swept = {c.args[0] for c in rw.call_args_list}
        self.assertEqual(ranked_swept, {8201})            # only current-season
        self.assertEqual(bulk_ids, {8202, 8203})          # lapsed + never → random

    def test_command_random_first_falls_back_when_no_ranked_data(self):
        # No ranked_last_season_id populated yet (cold field) → can't tell the
        # current season → fall back to ever-ranked so ranked isn't dropped.
        self._player(8301, last_season=None, ranked_history=True)  # ranked_json, NULL field
        with mock.patch(f"{CMD}._ranked_capture_active_for_realm",
                        return_value=True), \
                mock.patch(ENGINE_BULK,
                           return_value={"completed": 0, "baseline": 0,
                                         "events": 0, "aborted": False}), \
                mock.patch(ENGINE_RANKED,
                           return_value={"status": "completed"}) as rw:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True, random_first=True)
        self.assertEqual({c.args[0] for c in rw.call_args_list}, {8301})

    def test_skip_ranked_runs_random_only(self):
        self._player(8401, last_season=30)  # would be ranked, but skipped
        with mock.patch(f"{CMD}._ranked_capture_active_for_realm",
                        return_value=True), \
                mock.patch(ENGINE_BULK,
                           return_value={"completed": 0, "baseline": 0,
                                         "events": 0, "aborted": False}) as bulk_mock, \
                mock.patch(ENGINE_RANKED) as rw:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True, random_first=True, skip_ranked=True)
        rw.assert_not_called()                       # no ranked sweep
        self.assertEqual(set(bulk_mock.call_args.args[0]), {8401})  # all → random


class RandomFirstPathSwitchTests(TestCase):
    """Load-bearing safety: a player moving between the ranked (combined) and
    random-only paths across cycles keeps both diffs correct."""

    def setUp(self):
        self.player = Player.objects.create(
            name="sw", player_id=7777, realm="na", pvp_battles=100,
            pvp_wins=50, pvp_losses=50, pvp_frags=80, pvp_survived_battles=60,
        )
        Ship.objects.create(ship_id=99, name="Petro", nation="ussr",
                            ship_type="Cruiser", tier=10)

    def _ranked_rows(self, battles):
        return [{"ship_id": 99, "seasons": {"30": {
            "battles": battles, "wins": battles, "frags": battles,
            "damage_dealt": battles * 1000, "xp": battles * 100,
            "survived_battles": battles}}}]

    def _capture(self, rand_battles, ranked_battles=None):
        return record_observation_from_payloads(
            self.player,
            player_data=_account_payload(battles=rand_battles),
            ship_data=_ship_payload(battles=rand_battles, wins=1, damage=1, xp=1),
            ranked_ship_data=(self._ranked_rows(ranked_battles)
                              if ranked_battles is not None else None),
        )

    def test_ranked_then_random_only_then_ranked(self):
        self._capture(100, ranked_battles=5)   # tick0: ranked baseline
        self._capture(101, ranked_battles=7)   # tick1: ranked path (+1 rand, +2 ranked)
        self._capture(103)                      # tick2: RANDOM-ONLY (demoted), ranked=NULL
        self._capture(104, ranked_battles=10)  # tick3: ranked path again (+1 rand, ranked vs tick1)

        rand = BattleEvent.objects.filter(
            player=self.player, mode=BattleEvent.MODE_RANDOM)
        ranked = BattleEvent.objects.filter(
            player=self.player, mode=BattleEvent.MODE_RANKED)
        # Random captured continuously across all three post-baseline ticks.
        self.assertEqual(rand.count(), 3)
        # deltas: tick1 101-100=1, tick2 103-101=2, tick3 104-103=1
        self.assertEqual(sorted(e.battles_delta for e in rand), [1, 1, 2])
        # Ranked: tick1 (+2) and tick3 — tick3 walks back PAST the random-only
        # tick2 to tick1's ranked snapshot (7), so delta = 10-7 = 3. No ranked
        # battles lost across the gap.
        self.assertEqual(ranked.count(), 2)
        self.assertEqual(sorted(e.battles_delta for e in ranked), [2, 3])
        # The random-only obs persisted ranked=NULL (so the walk-back skips it).
        obs = list(BattleObservation.objects.filter(
            player=self.player).order_by("observed_at"))
        self.assertIsNone(obs[2].ranked_ships_stats_json)  # tick2 random-only


class RankedLastSeasonBackfillTests(TestCase):
    """The DB-only backfill populates ranked_last_season_id from ranked_json."""

    def test_helper_picks_max_season_with_battles(self):
        from warships.data import ranked_last_season_from_json
        self.assertIsNone(ranked_last_season_from_json(None))
        self.assertIsNone(ranked_last_season_from_json([]))
        self.assertIsNone(ranked_last_season_from_json(
            [{"season_id": 20, "total_battles": 0}]))  # history but no battles
        self.assertEqual(ranked_last_season_from_json([
            {"season_id": 20, "total_battles": 5},
            {"season_id": 30, "total_battles": 3},
            {"season_id": 31, "total_battles": 0},  # latest season, no battles
        ]), 30)

    def test_backfill_populates_field(self):
        played = Player.objects.create(
            name="b1", player_id=9501, realm="na", is_hidden=False,
            last_battle_date=timezone.now().date(),
            ranked_json=[{"season_id": 29, "total_battles": 4},
                         {"season_id": 30, "total_battles": 2}],
            ranked_last_season_id=None)
        no_battles = Player.objects.create(
            name="b2", player_id=9502, realm="na", is_hidden=False,
            last_battle_date=timezone.now().date(),
            ranked_json=[{"season_id": 20, "total_battles": 0}],
            ranked_last_season_id=None)
        call_command("backfill_ranked_last_season", realm="na", active_days=0,
                     delay=0)
        played.refresh_from_db()
        no_battles.refresh_from_db()
        self.assertEqual(played.ranked_last_season_id, 30)
        self.assertIsNone(no_battles.ranked_last_season_id)


class BenchmarkCommandTests(TestCase):
    """The read-only benchmark command runs and emits the metric structure."""

    def test_benchmark_json_structure_and_coverage(self):
        p = Player.objects.create(
            name="bm1", player_id=4040, realm="na", is_hidden=False,
            last_battle_date=timezone.now().date(), pvp_battles=100,
        )
        Ship.objects.create(ship_id=42, name="Yamato", nation="japan",
                            ship_type="Battleship", tier=10)
        o1 = BattleObservation.objects.create(
            player=p, pvp_battles=100, ships_stats_json=[], source="bulk_floor")
        o2 = BattleObservation.objects.create(
            player=p, pvp_battles=101, ships_stats_json=[], source="bulk_floor")
        BattleEvent.objects.create(
            player=p, mode=BattleEvent.MODE_RANDOM, ship_id=42,
            ship_name="Yamato", battles_delta=1,
            from_observation=o1, to_observation=o2)

        out = io.StringIO()
        call_command("benchmark_observation_floor", json=True, stdout=out)
        data = json.loads(out.getvalue())

        self.assertIn("config", data)
        self.assertIn("totals", data)
        self.assertIn("na", data["realms"])
        na = data["realms"]["na"]
        self.assertEqual(na["active_7d"], 1)
        self.assertEqual(na["distinct_observed"], 1)
        self.assertEqual(na["distinct_productive"], 1)
        self.assertEqual(na["obs_bulk_floor"], 2)
        # 1 productive of 1 active-7d → coverage 1.0
        self.assertEqual(na["coverage_ratio_vs_7d"], 1.0)
        self.assertEqual(na["fresh_within_24h"], 1)


class BulkObservationCommandTests(TestCase):
    """`--bulk` candidate routing (D6): ranked split vs ranked-off."""

    def _make_active_player(self, player_id, *, ranked):
        return Player.objects.create(
            name=f"p{player_id}", player_id=player_id, realm="na",
            is_hidden=False, last_battle_date=timezone.now().date(),
            pvp_battles=100, pvp_wins=50,
            ranked_json=[{"season_id": 1}] if ranked else None,
        )

    def test_bulk_with_ranked_on_splits_candidates(self):
        rk1 = self._make_active_player(5001, ranked=True)
        rk2 = self._make_active_player(5002, ranked=True)
        rnd = self._make_active_player(5003, ranked=False)

        with mock.patch(
            f"{CMD}._ranked_capture_active_for_realm", return_value=True
        ), mock.patch(
            ENGINE_BULK, return_value={"completed": 0, "baseline": 0,
                                       "events": 0, "aborted": False}
        ) as bulk_mock, mock.patch(
            ENGINE_RANKED, return_value={"status": "completed"}
        ) as ranked_mock:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True)

        # Bulk engine got ONLY the non-ranked-known id.
        bulk_mock.assert_called_once()
        bulk_ids = bulk_mock.call_args.args[0]
        self.assertEqual(set(bulk_ids), {rnd.player_id})
        # Ranked-known players went per-player (never to bulk -> no double obs).
        ranked_called_ids = {c.args[0] for c in ranked_mock.call_args_list}
        self.assertEqual(ranked_called_ids, {rk1.player_id, rk2.player_id})

    def test_legacy_path_arg_wiring_survives_bulk_refactor(self):
        # The non-bulk branch of handle() must still parse args and run end to
        # end after the --bulk/--ranked-limit/--chunk-delay refactor. dry_run
        # makes zero WG calls; a clean return proves the legacy wiring intact.
        self._make_active_player(5201, ranked=False)
        with mock.patch(f"{CMD}._ranked_capture_active_for_realm",
                        return_value=False):
            call_command("ensure_daily_battle_observations", realm="na",
                         dry_run=True)

    def test_bulk_with_ranked_off_sends_all_to_bulk(self):
        a = self._make_active_player(5101, ranked=True)  # ranked_json ignored
        b = self._make_active_player(5102, ranked=False)

        with mock.patch(
            f"{CMD}._ranked_capture_active_for_realm", return_value=False
        ), mock.patch(
            ENGINE_BULK, return_value={"completed": 0, "baseline": 0,
                                       "events": 0, "aborted": False}
        ) as bulk_mock, mock.patch(ENGINE_RANKED) as ranked_mock:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True)

        bulk_mock.assert_called_once()
        self.assertEqual(set(bulk_mock.call_args.args[0]),
                         {a.player_id, b.player_id})
        ranked_mock.assert_not_called()  # no ranked sweep when capture off

    def test_change_gate_flag_passes_through(self):
        self._make_active_player(5301, ranked=False)
        with mock.patch(
            f"{CMD}._ranked_capture_active_for_realm", return_value=False
        ), mock.patch(
            ENGINE_BULK, return_value={"completed": 0, "baseline": 0,
                                       "events": 0, "aborted": False}
        ) as bulk_mock:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True, change_gate=True)
        self.assertTrue(bulk_mock.call_args.kwargs.get("change_gate"))

    def test_change_gate_defaults_off(self):
        self._make_active_player(5302, ranked=False)
        with mock.patch(
            f"{CMD}._ranked_capture_active_for_realm", return_value=False
        ), mock.patch(
            ENGINE_BULK, return_value={"completed": 0, "baseline": 0,
                                       "events": 0, "aborted": False}
        ) as bulk_mock:
            call_command("ensure_daily_battle_observations", realm="na",
                         bulk=True)
        self.assertFalse(bulk_mock.call_args.kwargs.get("change_gate"))


class BulkObservationTaskTests(TestCase):
    """Task-level flag plumbing for the bulk floor."""

    def test_flag_default_is_off(self):
        from warships.tasks import _bulk_floor_active_for_realm
        # No env set -> bulk path is OFF (instant-rollback default).
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BATTLE_OBSERVATION_FLOOR_BULK_ENABLED", None)
            self.assertFalse(_bulk_floor_active_for_realm("na"))

    def test_task_passes_bulk_when_flag_on_for_realm(self):
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "1",
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS": "na",
        }), mock.patch("django.core.management.call_command") as cc:
            from warships.tasks import ensure_daily_battle_observations_task
            result = ensure_daily_battle_observations_task.apply(
                args=["na"]).get()

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["bulk"])
        cc.assert_called_once()
        self.assertTrue(cc.call_args.kwargs.get("bulk"))
        self.assertIn("chunk_delay", cc.call_args.kwargs)

    def test_task_stays_legacy_when_flag_off(self):
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "0",
        }), mock.patch("django.core.management.call_command") as cc:
            from warships.tasks import ensure_daily_battle_observations_task
            result = ensure_daily_battle_observations_task.apply(
                args=["na"]).get()

        self.assertFalse(result["bulk"])
        cc.assert_called_once()
        self.assertNotIn("bulk", cc.call_args.kwargs)  # legacy call shape

    def test_task_passes_change_gate_when_flag_on(self):
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "1",
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS": "na",
            "BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED": "1",
        }), mock.patch("django.core.management.call_command") as cc:
            from warships.tasks import ensure_daily_battle_observations_task
            ensure_daily_battle_observations_task.apply(args=["na"]).get()
        self.assertTrue(cc.call_args.kwargs.get("change_gate"))

    def test_task_omits_change_gate_when_flag_off(self):
        with mock.patch.dict(os.environ, {
            "BATTLE_OBSERVATION_FLOOR_BULK_ENABLED": "1",
            "BATTLE_OBSERVATION_FLOOR_BULK_REALMS": "na",
        }, clear=False), mock.patch("django.core.management.call_command") as cc:
            os.environ.pop("BATTLE_OBSERVATION_FLOOR_CHANGE_GATE_ENABLED", None)
            from warships.tasks import ensure_daily_battle_observations_task
            ensure_daily_battle_observations_task.apply(args=["na"]).get()
        # bulk on, gate off → bulk passed, change_gate absent
        self.assertTrue(cc.call_args.kwargs.get("bulk"))
        self.assertNotIn("change_gate", cc.call_args.kwargs)


# The command imports the fetchers function-locally, so patch them at source.
SHADOW_ACCT = "warships.api.players._bulk_fetch_account_info"
SHADOW_SHIP = "warships.api.ships._bulk_fetch_ship_stats"
SHADOW_SINGLE_ACCT = "warships.api.players._fetch_player_personal_data"
SHADOW_SINGLE_SHIP = "warships.api.ships._fetch_ship_stats_for_player"


class ShadowParityCompareTests(SimpleTestCase):
    """Pure comparison logic for the phase-2 parity shadow (no DB)."""

    def _compare(self, acct_s, ships_s, acct_b, ships_b):
        from warships.management.commands.shadow_bulk_observation_parity import (
            compare_player,
        )
        return compare_player(acct_s, ships_s, acct_b, ships_b)

    def test_identical_payloads_match(self):
        acct = _account_payload(battles=101, wins=51)
        ships = _ship_payload(battles=101, wins=51, damage=42_000)
        verdict, _ = self._compare(acct, ships, dict(acct), list(ships))
        self.assertEqual(verdict, "match")

    def test_both_hidden_match(self):
        acct = _account_payload(battles=0, hidden=True)
        verdict, _ = self._compare(acct, [], dict(acct), [])
        self.assertEqual(verdict, "match")

    def test_bulk_absent_ships_flags_coverage_gap(self):
        acct = _account_payload(battles=101, wins=51)
        ships = _ship_payload(battles=101)
        # Bulk slice absent (None) -> bulk would skip a player legacy captures.
        verdict, _ = self._compare(acct, ships, dict(acct), None)
        self.assertEqual(verdict, "bulk_skips_capturable")

    def test_skip_sentinel_flags_coverage_gap(self):
        acct = _account_payload(battles=101)
        ships = _ship_payload(battles=101)
        verdict, _ = self._compare(acct, ships, dict(acct), "SKIP")
        self.assertEqual(verdict, "bulk_skips_capturable")

    def test_divergent_ship_stats_are_mismatch(self):
        acct = _account_payload(battles=101, wins=51)
        ships_s = _ship_payload(battles=101, wins=51, damage=42_000)
        ships_b = _ship_payload(battles=101, wins=51, damage=99_999)  # differs
        verdict, detail = self._compare(acct, ships_s, dict(acct), ships_b)
        self.assertEqual(verdict, "mismatch")
        self.assertIn("ships", detail["diffs"])

    def test_divergent_account_aggregate_is_mismatch(self):
        ships = _ship_payload(battles=101)
        acct_s = _account_payload(battles=101, wins=51)
        acct_b = _account_payload(battles=101, wins=50)  # wins differ
        verdict, detail = self._compare(acct_s, ships, acct_b, list(ships))
        self.assertEqual(verdict, "mismatch")
        self.assertIn("pvp_wins", detail["diffs"])


class ShadowParityCommandTests(TestCase):
    """Command-level: read-only, never writes observations."""

    def test_command_reports_match_and_writes_nothing(self):
        ids = [7001, 7002]
        acct = {str(i): _account_payload(battles=101, wins=51) for i in ids}
        ships = {str(i): _ship_payload(battles=101, wins=51) for i in ids}

        out = io.StringIO()
        with mock.patch(SHADOW_ACCT, return_value=(acct, None)), \
                mock.patch(SHADOW_SHIP, return_value=(ships, None)), \
                mock.patch(SHADOW_SINGLE_ACCT,
                           side_effect=lambda pid, realm: acct[str(pid)]), \
                mock.patch(SHADOW_SINGLE_SHIP,
                           side_effect=lambda pid, realm: ships[str(pid)]):
            call_command("shadow_bulk_observation_parity", realm="na",
                         player_ids="7001,7002", stdout=out)

        self.assertIn("match=2", out.getvalue())
        self.assertIn("no payload mismatches", out.getvalue())
        # Read-only: nothing persisted.
        self.assertEqual(BattleObservation.objects.count(), 0)

    def test_command_flags_mismatch(self):
        acct = {"7003": _account_payload(battles=101, wins=51)}
        ships_bulk = {"7003": _ship_payload(battles=101, wins=51, damage=1)}
        ships_single = _ship_payload(battles=101, wins=51, damage=2)

        out = io.StringIO()
        with mock.patch(SHADOW_ACCT, return_value=(acct, None)), \
                mock.patch(SHADOW_SHIP, return_value=(ships_bulk, None)), \
                mock.patch(SHADOW_SINGLE_ACCT,
                           side_effect=lambda pid, realm: acct[str(pid)]), \
                mock.patch(SHADOW_SINGLE_SHIP,
                           side_effect=lambda pid, realm: ships_single):
            call_command("shadow_bulk_observation_parity", realm="na",
                         player_ids="7003", stdout=out)

        self.assertIn("mismatch=1", out.getvalue())
        self.assertIn("PARITY MISMATCH", out.getvalue())

    def test_command_applies_poison_batch_fallback(self):
        # A poison id makes the bulk ships call return INVALID_ACCOUNT_ID. The
        # command must mirror the engine: per-player fallback isolates it and
        # the good player still reads as a match (not a false bulk-skip).
        acct = {"7004": _account_payload(battles=101, wins=51)}
        ship_list = _ship_payload(battles=101, wins=51)

        out = io.StringIO()
        with mock.patch(SHADOW_ACCT, return_value=(acct, None)), \
                mock.patch(SHADOW_SHIP,
                           return_value=({}, "INVALID_ACCOUNT_ID")), \
                mock.patch("warships.api.ships._per_player_ship_fallback",
                           return_value={"7004": ship_list}) as fb, \
                mock.patch(SHADOW_SINGLE_ACCT,
                           side_effect=lambda pid, realm: acct[str(pid)]), \
                mock.patch(SHADOW_SINGLE_SHIP,
                           side_effect=lambda pid, realm: ship_list):
            call_command("shadow_bulk_observation_parity", realm="na",
                         player_ids="7004", stdout=out)

        fb.assert_called_once()
        self.assertIn("match=1", out.getvalue())
        self.assertIn("poison_fallback_chunks=1", out.getvalue())

"""ShipPopDailyAgg rollup — DB-audit lever F9.2.

The nightly `compute_all_ship_pop_avg_damage` used to run one ~34s/realm
grouped scan of PlayerDailyShipStats (7M+ rows) over the trailing 30d window.
These tests pin the replacement: a small per-(realm, mode, ship, day) daily
aggregate table (`ShipPopDailyAgg`) maintained by `rollup_ship_pop_daily`,
so the nightly warm becomes a sum over ~30 tiny rows per ship.

Invariants proven here:
- rollup correctness for one realm-day (realm/mode partitioning, all carried
  sum columns) against a hand-seeded PDSS fixture;
- idempotency (re-rolling a day never duplicates or drifts);
- catch-up fills missing dates in the window, skips already-rolled frozen
  days, and always re-rolls the trailing refresh days (the current UTC day
  is still accruing PDSS writes);
- `compute_all_ship_pop_avg_damage` output is IDENTICAL to the legacy
  per-ship PDSS aggregation (`compute_ship_pop_avg_damage`, kept as the
  gap fallback) — same cache keys, same values, same 0 below-floor sentinel;
- retention prune (100 days) is self-bounding and scoped per realm.
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone as django_timezone

from warships.data import (
    SHIP_COMBAT_WINDOW_DAYS,
    SHIP_POP_ROLLUP_REFRESH_DAYS,
    SHIP_POP_ROLLUP_RETENTION_DAYS,
    compute_all_ship_pop_avg_damage,
    compute_ship_pop_avg_damage,
    get_cached_ship_pop_avg_damage,
    rollup_ship_pop_daily,
    rollup_ship_pop_daily_catchup,
)
from warships.models import Player, PlayerDailyShipStats, ShipPopDailyAgg


def _seed_pdss(player, date, ship_id, mode=PlayerDailyShipStats.MODE_RANDOM,
               season_id=None, **fields):
    defaults = dict(
        battles=1, wins=0, frags=0, damage=0, xp=0,
        main_shots=0, main_hits=0,
        secondary_shots=0, secondary_hits=0,
        torpedo_shots=0, torpedo_hits=0,
    )
    defaults.update(fields)
    return PlayerDailyShipStats.objects.create(
        player=player, date=date, ship_id=ship_id, mode=mode,
        season_id=season_id, ship_name=f"Ship {ship_id}", **defaults)


class RollupFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.today = django_timezone.now().date()
        cls.na_a = Player.objects.create(
            name="rollup_na_a", player_id=91001, realm="na")
        cls.na_b = Player.objects.create(
            name="rollup_na_b", player_id=91002, realm="na")
        cls.eu_a = Player.objects.create(
            name="rollup_eu_a", player_id=91003, realm="eu")


class TestRollupShipPopDaily(RollupFixtureMixin, TestCase):
    def test_rollup_day_correctness_realm_and_mode_partition(self):
        """One realm-day rollup sums every carried column per (ship, mode),
        never mixing realms or modes."""
        day = self.today - timedelta(days=3)
        _seed_pdss(self.na_a, day, 42, battles=6, wins=4, frags=5,
                   damage=287_400, xp=9_000, main_shots=100, main_hits=35,
                   torpedo_shots=10, torpedo_hits=3)
        _seed_pdss(self.na_b, day, 42, battles=20, wins=10, frags=12,
                   damage=1_000_000, xp=30_000, main_shots=400, main_hits=120,
                   secondary_shots=50, secondary_hits=5)
        # Ranked rows on the same ship/day roll into their own mode row.
        _seed_pdss(self.na_a, day, 42, mode=PlayerDailyShipStats.MODE_RANKED,
                   season_id=27, battles=3, wins=2, damage=90_000)
        # Second ship, single row.
        _seed_pdss(self.na_b, day, 43, battles=2, wins=1, damage=95_000)
        # Cross-realm must not leak in.
        _seed_pdss(self.eu_a, day, 42, battles=50, wins=25, damage=50_000_000)
        # A different day must not leak in.
        _seed_pdss(self.na_a, day - timedelta(days=1), 42, battles=9,
                   damage=999)

        rollup_ship_pop_daily("na", day)

        aggs = {(a.ship_id, a.mode): a
                for a in ShipPopDailyAgg.objects.filter(realm="na", date=day)}
        self.assertEqual(
            set(aggs), {(42, "random"), (42, "ranked"), (43, "random")})
        row = aggs[(42, "random")]
        self.assertEqual(row.battles, 26)
        self.assertEqual(row.wins, 14)
        self.assertEqual(row.frags, 17)
        self.assertEqual(row.damage_sum, 1_287_400)
        self.assertEqual(row.xp, 39_000)
        self.assertEqual(row.main_shots, 500)
        self.assertEqual(row.main_hits, 155)
        self.assertEqual(row.secondary_shots, 50)
        self.assertEqual(row.secondary_hits, 5)
        self.assertEqual(row.torpedo_shots, 10)
        self.assertEqual(row.torpedo_hits, 3)
        ranked = aggs[(42, "ranked")]
        self.assertEqual(ranked.battles, 3)
        self.assertEqual(ranked.damage_sum, 90_000)
        self.assertEqual(aggs[(43, "random")].damage_sum, 95_000)
        # The eu realm-day was not rolled up.
        self.assertFalse(
            ShipPopDailyAgg.objects.filter(realm="eu").exists())

    def test_rollup_idempotent(self):
        day = self.today - timedelta(days=2)
        _seed_pdss(self.na_a, day, 42, battles=6, damage=287_400)
        rollup_ship_pop_daily("na", day)
        rollup_ship_pop_daily("na", day)
        rows = ShipPopDailyAgg.objects.filter(realm="na", date=day)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().battles, 6)
        self.assertEqual(rows.get().damage_sum, 287_400)

    def test_rollup_picks_up_new_rows_on_reroll(self):
        """Re-rolling a day replaces the aggregate with current PDSS truth
        (delete+recreate upsert)."""
        day = self.today
        _seed_pdss(self.na_a, day, 42, battles=6, damage=287_400)
        rollup_ship_pop_daily("na", day)
        _seed_pdss(self.na_b, day, 42, battles=20, damage=1_000_000)
        rollup_ship_pop_daily("na", day)
        row = ShipPopDailyAgg.objects.get(realm="na", date=day, ship_id=42)
        self.assertEqual(row.battles, 26)
        self.assertEqual(row.damage_sum, 1_287_400)

    def test_retention_prune_scoped_to_realm(self):
        old_day = self.today - timedelta(
            days=SHIP_POP_ROLLUP_RETENTION_DAYS + 10)
        kept_day = self.today - timedelta(
            days=SHIP_POP_ROLLUP_RETENTION_DAYS - 1)
        ShipPopDailyAgg.objects.create(
            realm="na", ship_id=42, date=old_day, battles=1)
        ShipPopDailyAgg.objects.create(
            realm="na", ship_id=42, date=kept_day, battles=1)
        # Another realm's ancient row survives an na rollup (prune is
        # per-realm, owned by that realm's rollup).
        ShipPopDailyAgg.objects.create(
            realm="eu", ship_id=42, date=old_day, battles=1)

        rollup_ship_pop_daily("na", self.today)

        self.assertFalse(ShipPopDailyAgg.objects.filter(
            realm="na", date=old_day).exists())
        self.assertTrue(ShipPopDailyAgg.objects.filter(
            realm="na", date=kept_day).exists())
        self.assertTrue(ShipPopDailyAgg.objects.filter(
            realm="eu", date=old_day).exists())


class TestRollupCatchup(RollupFixtureMixin, TestCase):
    def test_catchup_fills_missing_days_and_refreshes_trailing(self):
        frozen_day = self.today - timedelta(days=10)
        gap_day = self.today - timedelta(days=5)
        _seed_pdss(self.na_a, frozen_day, 42, battles=6, damage=287_400)
        _seed_pdss(self.na_a, gap_day, 42, battles=4, damage=100_000)
        _seed_pdss(self.na_a, self.today, 42, battles=2, damage=50_000)

        rollup_ship_pop_daily_catchup("na")
        self.assertTrue(ShipPopDailyAgg.objects.filter(
            realm="na", date=frozen_day, ship_id=42).exists())
        self.assertTrue(ShipPopDailyAgg.objects.filter(
            realm="na", date=gap_day, ship_id=42).exists())
        self.assertTrue(ShipPopDailyAgg.objects.filter(
            realm="na", date=self.today, ship_id=42).exists())

        # A day that vanished (repair/manual delete) is refilled.
        ShipPopDailyAgg.objects.filter(realm="na", date=gap_day).delete()
        # Frozen days outside the refresh window are NOT recomputed: late
        # writes to a past date stay invisible until a manual re-roll…
        _seed_pdss(self.na_b, frozen_day, 42, battles=99, damage=1)
        # …but the trailing refresh days ARE re-rolled every catch-up
        # (the current UTC day keeps accruing).
        _seed_pdss(self.na_b, self.today, 42, battles=20, damage=1_000_000)

        rollup_ship_pop_daily_catchup("na")

        self.assertTrue(ShipPopDailyAgg.objects.filter(
            realm="na", date=gap_day, ship_id=42).exists())
        frozen = ShipPopDailyAgg.objects.get(
            realm="na", date=frozen_day, ship_id=42)
        self.assertEqual(frozen.battles, 6)  # skip-if-rolled: unchanged
        fresh = ShipPopDailyAgg.objects.get(
            realm="na", date=self.today, ship_id=42)
        self.assertEqual(fresh.battles, 22)
        self.assertEqual(fresh.damage_sum, 1_050_000)
        # Sanity: the refresh horizon covers at least the current day.
        self.assertGreaterEqual(SHIP_POP_ROLLUP_REFRESH_DAYS, 1)

    def test_catchup_covers_the_full_trailing_window(self):
        """The oldest date the legacy scan included (today - window) is
        rolled up too — the window sum must not lose its edge day."""
        edge_day = self.today - timedelta(days=SHIP_COMBAT_WINDOW_DAYS)
        _seed_pdss(self.na_a, edge_day, 42, battles=25, damage=1_250_000)
        rollup_ship_pop_daily_catchup("na")
        self.assertTrue(ShipPopDailyAgg.objects.filter(
            realm="na", date=edge_day, ship_id=42).exists())


class TestComputeAllFromRollup(RollupFixtureMixin, TestCase):
    def _seed_window_fixture(self):
        """Multi-day, multi-ship, multi-player, cross-realm, mixed-mode
        fixture spanning the window edge."""
        t = self.today
        # Ship 42: above floor, spread across the window (edge day included).
        _seed_pdss(self.na_a, t, 42, battles=6, damage=287_400)
        _seed_pdss(self.na_b, t - timedelta(days=15), 42, battles=20,
                   damage=1_000_000)
        _seed_pdss(self.na_a, t - timedelta(days=SHIP_COMBAT_WINDOW_DAYS),
                   42, battles=5, damage=200_000)
        # Outside the window: excluded by both paths.
        _seed_pdss(self.na_b,
                   t - timedelta(days=SHIP_COMBAT_WINDOW_DAYS + 1),
                   42, battles=40, damage=9_999_999)
        # Ship 43: below the 20-battle floor → 0 sentinel.
        _seed_pdss(self.na_a, t - timedelta(days=1), 43, battles=2,
                   damage=95_000)
        # Ship 44: exactly at the floor.
        _seed_pdss(self.na_b, t - timedelta(days=7), 44, battles=20,
                   damage=805_010)
        # Ranked rows never count toward the random baseline.
        _seed_pdss(self.na_a, t, 42, mode=PlayerDailyShipStats.MODE_RANKED,
                   season_id=27, battles=30, damage=5_000_000)
        # Cross-realm rows never leak in.
        _seed_pdss(self.eu_a, t, 42, battles=50, damage=50_000_000)

    def test_identical_output_vs_legacy_per_ship_computation(self):
        self._seed_window_fixture()
        # Legacy path (direct PDSS aggregate — kept as the gap fallback).
        legacy = {sid: compute_ship_pop_avg_damage("na", sid)
                  for sid in (42, 43, 44)}
        cache.clear()

        result = compute_all_ship_pop_avg_damage("na")
        self.assertEqual(result, {"ships": 3})
        hits, missing = get_cached_ship_pop_avg_damage("na", [42, 43, 44])
        self.assertEqual(missing, [])
        self.assertEqual(hits, legacy)
        # Pin the expected values so both paths can't drift together:
        # (287_400 + 1_000_000 + 200_000) / (6 + 20 + 5) = 47_980.6… → 47_981
        self.assertEqual(hits[42], 47_981)
        self.assertEqual(hits[43], 0)          # below floor → sentinel
        # 805_010 / 20 = 40_250.5 → 40_250 (Python banker's rounding, same
        # as the legacy int(round(...))).
        self.assertEqual(hits[44], 40_250)

    def test_compute_all_is_cheap_after_first_run(self):
        """Second nightly run only re-rolls the trailing refresh days —
        frozen window days are served from ShipPopDailyAgg untouched."""
        self._seed_window_fixture()
        compute_all_ship_pop_avg_damage("na")
        frozen = ShipPopDailyAgg.objects.get(
            realm="na", date=self.today - timedelta(days=15), ship_id=42)
        marker_pk = frozen.pk

        cache.clear()
        result = compute_all_ship_pop_avg_damage("na")
        self.assertEqual(result, {"ships": 3})
        # The frozen day's row was not deleted/recreated.
        self.assertTrue(ShipPopDailyAgg.objects.filter(pk=marker_pk).exists())
        hits, _ = get_cached_ship_pop_avg_damage("na", [42])
        self.assertEqual(hits[42], 47_981)

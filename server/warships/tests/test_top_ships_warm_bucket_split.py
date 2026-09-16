"""Tests for the top-ships warm split into per-bucket subtasks.

`warm_realm_top_ships_task` used to walk every tier×type bucket inline under one
540s soft limit. Measured on prod 2026-08-12: **12 dispatches in 24h, 12
SoftTimeLimitExceeded, zero completions** — it died after 2-5 buckets, so T9/T10
(including the landing page's default T10 view) never warmed on any realm, and the
durable `:published` fallback silently served weeks-old numbers.

Two secondary defects rode along and are pinned here too:
  * the dispatch debounce was cleared in `finally` even on failure, so every landing
    visitor immediately re-armed the doomed task (~1.8h/day of a -c 3 worker, wasted);
  * the task's lock was `timeout=300` against a 540s soft / 600s hard limit, so the
    duplicate guard went blind for the tail of every run.

Runbook: agents/runbooks/runbook-top-ships-warm-soft-limit-2026-08-12.md
"""

from datetime import date
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from warships.data import SHIP_LEADERBOARD_TYPES


class TopShipsWarmBudgetGuardTests(TestCase):
    """The constants that made the old failure modes possible."""

    def test_lock_outlives_the_hard_time_limit(self):
        # A lock that expires before the task is killed leaves the "already
        # running" guard blind for the tail of every run, so a second invocation
        # can start on top of a live one. Mirrors ENRICHMENT_RECLASSIFY_LOCK_TIMEOUT.
        from warships.tasks import REALM_TOP_SHIPS_WARM_LOCK_TIMEOUT, TASK_OPTS

        self.assertGreater(
            REALM_TOP_SHIPS_WARM_LOCK_TIMEOUT, TASK_OPTS["time_limit"],
            "lock must outlive the HARD time_limit, not merely the soft one")

    def test_bucket_lock_outlives_the_hard_time_limit(self):
        from warships.tasks import SHIPS_BUCKET_WARM_LOCK_TIMEOUT, TASK_OPTS

        self.assertGreater(
            SHIPS_BUCKET_WARM_LOCK_TIMEOUT, TASK_OPTS["time_limit"])


class TopShipsWarmDispatchDebounceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_debounce_is_cleared_after_a_successful_run(self):
        # Success must release the debounce so the next window rotation can warm.
        from warships.tasks import (
            _realm_top_ships_warm_dispatch_key, warm_realm_top_ships_task,
        )

        cache.add(_realm_top_ships_warm_dispatch_key("na"), "queued", timeout=900)
        with mock.patch("warships.data.compute_realm_top_ships",
                        return_value={"ships": []}), \
                mock.patch("warships.tasks.queue_realm_ships_pct_warm",
                           return_value={"status": "queued"}), \
                mock.patch("warships.tasks.warm_ships_bucket_task.apply_async"):
            warm_realm_top_ships_task(realm="na")
        self.assertIsNone(cache.get(_realm_top_ships_warm_dispatch_key("na")))

    def test_debounce_survives_a_failed_run(self):
        # THE STORM FIX. On failure the debounce must stand, or the next landing
        # visitor immediately re-arms a task that just proved it cannot finish.
        from warships.tasks import (
            _realm_top_ships_warm_dispatch_key, warm_realm_top_ships_task,
        )

        cache.add(_realm_top_ships_warm_dispatch_key("na"), "queued", timeout=900)
        with mock.patch("warships.tasks.warm_top_ships_treemap_task.apply_async",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                warm_realm_top_ships_task(realm="na")
        self.assertEqual(
            cache.get(_realm_top_ships_warm_dispatch_key("na")), "queued",
            "a failed warm must not release the debounce")

    def test_lock_is_released_on_both_outcomes(self):
        # The lock is a mutual-exclusion guard, not a cooldown — it must always
        # be released, or a single failure wedges the warm until it expires.
        from warships.tasks import (
            _realm_top_ships_warm_lock_key, warm_realm_top_ships_task,
        )

        with mock.patch("warships.tasks.warm_top_ships_treemap_task.apply_async",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                warm_realm_top_ships_task(realm="na")
        self.assertIsNone(cache.get(_realm_top_ships_warm_lock_key("na")))


class TopShipsWarmBucketSplitTests(TestCase):
    def setUp(self):
        cache.clear()

    # `_badge_tiers()` follows SHIP_BADGE_TIERS, which is '10' by default locally
    # and '8,9,10,11' in prod (tier 11 added 2026-09-15). Pin it so these assert
    # the production shape rather than whatever the test environment happens to
    # be configured for.
    PROD_TIERS = [8, 9, 10, 11]

    def test_orchestrator_computes_nothing_at_all(self):
        # THE LOAD-BEARING CONTRACT. Anything heavy left on the orchestrator's own
        # budget is a single point of failure for every subtask behind it: the
        # first pass of this fix left the two treemap recomputes and the default
        # pct bucket inline, and on EU (the largest realm) they exhausted the 540s
        # budget before a single bucket was dispatched — so EU warmed nothing
        # while NA and ASIA, small enough to squeak through, looked fixed.
        from warships.tasks import warm_realm_top_ships_task

        with mock.patch("warships.data._badge_tiers",
                        return_value=self.PROD_TIERS), \
                mock.patch("warships.tasks.queue_realm_ships_pct_warm",
                           return_value={"status": "queued"}), \
                mock.patch("warships.data.compute_realm_top_ships") as treemap, \
                mock.patch("warships.data.compute_realm_ships_by_tier_type") as buckets, \
                mock.patch("warships.tasks.warm_ships_bucket_task.apply_async"), \
                mock.patch("warships.tasks.warm_top_ships_treemap_task.apply_async"), \
                mock.patch("warships.tasks.warm_ships_by_pct_task.apply_async"):
            result = warm_realm_top_ships_task(realm="na")

        treemap.assert_not_called()
        buckets.assert_not_called()
        self.assertEqual(result["status"], "completed")

    def test_orchestrator_dispatches_one_subtask_per_bucket(self):
        # The whole point: buckets are no longer computed inline under one shared
        # budget, so one slow bucket cannot discard every bucket after it.
        from warships.tasks import warm_realm_top_ships_task

        with mock.patch("warships.data._badge_tiers",
                        return_value=self.PROD_TIERS), \
                mock.patch("warships.tasks.queue_realm_ships_pct_warm",
                           return_value={"status": "queued"}), \
                mock.patch("warships.tasks.warm_top_ships_treemap_task.apply_async") as tm, \
                mock.patch("warships.tasks.warm_ships_by_pct_task.apply_async") as pct, \
                mock.patch("warships.tasks.warm_ships_bucket_task.apply_async") as sub:
            result = warm_realm_top_ships_task(realm="na")

        # One subtask per tier x type, dispatched — not computed on this task's
        # budget. Derived from PROD_TIERS so adding a tier updates one constant.
        self.assertEqual(sub.call_count,
                         len(self.PROD_TIERS) * len(SHIP_LEADERBOARD_TYPES))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["results"]["tier_type_buckets_dispatched"],
                         len(self.PROD_TIERS) * len(SHIP_LEADERBOARD_TYPES))
        # Both treemap modes and the default pct bucket are dispatched too.
        self.assertEqual(tm.call_count, 2)
        self.assertEqual(
            sorted(c.kwargs["kwargs"]["mode"] for c in tm.call_args_list),
            ["random", "ranked"])
        self.assertEqual(pct.call_count, 1)

    def test_every_dispatch_is_staggered_across_all_three_families(self):
        # Treemaps, buckets and the pct bucket share one spacing sequence so the
        # every job (2 treemap modes + one per tier x type bucket) does not land
        # on the shared queue simultaneously.
        from warships.tasks import (
            SHIPS_BUCKET_WARM_SPACING_SECONDS, warm_realm_top_ships_task,
        )

        with mock.patch("warships.data._badge_tiers",
                        return_value=self.PROD_TIERS), \
                mock.patch("warships.tasks.queue_realm_ships_pct_warm",
                           return_value={"status": "queued"}), \
                mock.patch("warships.tasks.warm_ships_by_pct_task.apply_async"), \
                mock.patch("warships.tasks.warm_top_ships_treemap_task.apply_async") as tm, \
                mock.patch("warships.tasks.warm_ships_bucket_task.apply_async") as sub:
            warm_realm_top_ships_task(realm="na")

        seen = ([c.kwargs["countdown"] for c in tm.call_args_list]
                + [c.kwargs["countdown"] for c in sub.call_args_list])
        self.assertEqual(
            seen,
            [i * SHIPS_BUCKET_WARM_SPACING_SECONDS
             for i in range(2 + len(self.PROD_TIERS) * len(SHIP_LEADERBOARD_TYPES))])

    def test_every_tier_type_pair_is_covered_exactly_once(self):
        from warships.tasks import warm_realm_top_ships_task

        with mock.patch("warships.data.compute_realm_top_ships",
                        return_value={"ships": []}), \
                mock.patch("warships.data._badge_tiers",
                           return_value=self.PROD_TIERS), \
                mock.patch("warships.tasks.queue_realm_ships_pct_warm",
                           return_value={"status": "queued"}), \
                mock.patch("warships.tasks.warm_ships_bucket_task.apply_async") as sub:
            warm_realm_top_ships_task(realm="na")

        pairs = sorted(
            (c.kwargs["kwargs"]["tier"], c.kwargs["kwargs"]["ship_type"])
            for c in sub.call_args_list)
        expected = sorted(
            (t, st) for t in self.PROD_TIERS for st in SHIP_LEADERBOARD_TYPES)
        self.assertEqual(pairs, expected)

    def test_bucket_order_rotates_by_day(self):
        # Cheap insurance: if a bucket ever does exceed its own budget, a fixed
        # order would starve the same tail forever. Same remedy as the reclassify
        # bucket-family split.
        from warships.tasks import _rotated_ship_buckets

        day_a = _rotated_ship_buckets([8, 9, 10], date(2026, 8, 12))
        day_b = _rotated_ship_buckets([8, 9, 10], date(2026, 8, 13))
        self.assertNotEqual(day_a, day_b, "order must differ across days")
        self.assertEqual(sorted(day_a), sorted(day_b), "same set, rotated")
        self.assertEqual(len(day_a), 3 * len(SHIP_LEADERBOARD_TYPES))

    def test_rotation_is_a_pure_rotation_not_a_shuffle(self):
        # A rotation keeps adjacency, which keeps the per-tier locality that makes
        # partial progress interpretable in the journal.
        from warships.tasks import _rotated_ship_buckets

        base = _rotated_ship_buckets([8, 9, 10], date(2026, 8, 12))
        for offset in range(1, 16):
            rotated = _rotated_ship_buckets(
                [8, 9, 10], date.fromordinal(date(2026, 8, 12).toordinal() + offset))
            k = rotated.index(base[0])
            self.assertEqual(rotated[k:] + rotated[:k], base)


class ShipsBucketWarmTaskTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_bucket_task_computes_its_one_bucket(self):
        from warships.tasks import warm_ships_bucket_task

        with mock.patch("warships.data.compute_realm_ships_by_tier_type") as comp:
            result = warm_ships_bucket_task(
                realm="na", tier=10, ship_type="Destroyer")

        comp.assert_called_once()
        self.assertEqual(comp.call_args.kwargs["tier"], 10)
        self.assertEqual(comp.call_args.kwargs["ship_type"], "Destroyer")
        self.assertIs(comp.call_args.kwargs["use_cache"], False)
        self.assertEqual(result["status"], "completed")

    def test_concurrent_bucket_warm_is_skipped_by_the_lock(self):
        from warships.tasks import (
            _ships_bucket_warm_lock_key, warm_ships_bucket_task,
        )

        cache.add(_ships_bucket_warm_lock_key("na", 10, "Destroyer", "random"),
                  "held", timeout=900)
        with mock.patch("warships.data.compute_realm_ships_by_tier_type") as comp:
            result = warm_ships_bucket_task(
                realm="na", tier=10, ship_type="Destroyer")
        self.assertEqual(result["status"], "skipped")
        comp.assert_not_called()

    def test_bucket_lock_is_released_even_when_the_bucket_fails(self):
        # One bucket failing must not wedge that bucket for the lock's lifetime.
        from warships.tasks import (
            _ships_bucket_warm_lock_key, warm_ships_bucket_task,
        )

        with mock.patch("warships.data.compute_realm_ships_by_tier_type",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                warm_ships_bucket_task(realm="na", tier=10, ship_type="Destroyer")
        self.assertIsNone(cache.get(
            _ships_bucket_warm_lock_key("na", 10, "Destroyer", "random")))

    def test_one_failing_bucket_does_not_affect_the_others(self):
        # The property the split exists to create.
        from warships.tasks import warm_ships_bucket_task

        def flaky(*a, **kw):
            if kw.get("ship_type") == "Destroyer":
                raise RuntimeError("boom")
            return {"ships": []}

        with mock.patch("warships.data.compute_realm_ships_by_tier_type",
                        side_effect=flaky):
            with self.assertRaises(RuntimeError):
                warm_ships_bucket_task(realm="na", tier=10, ship_type="Destroyer")
            ok = warm_ships_bucket_task(realm="na", tier=10, ship_type="Cruiser")
        self.assertEqual(ok["status"], "completed")


class ShipsBucketTaskRoutingTests(TestCase):
    def test_bucket_task_runs_on_the_background_queue(self):
        from django.conf import settings

        self.assertEqual(
            settings.CELERY_TASK_ROUTES[
                "warships.tasks.warm_ships_bucket_task"]["queue"],
            "background")

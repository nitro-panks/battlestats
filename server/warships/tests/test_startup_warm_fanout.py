"""Tests for the startup cache warm split into per-realm subtasks.

`startup_warm_caches_task` used to run `startup_warm_all_caches` inline: three
realms x four warmers, twelve serial operations under one 540s soft limit.
Measured on prod 2026-08-26: **4 dispatches over the retained 7-day journal, 4
SoftTimeLimitExceeded, zero completions.** The best run reached `eu`'s first
warmer 13s before it was killed; `na` -- the default realm -- was never warmed
once. The 2026-08-26 run broke down as asia hot-entity 268s + bulk 5s +
distributions 58s, then killed 209s into asia's correlations.

Same defect and same fix as `warm_realm_top_ships_task` on 2026-08-12: the
orchestrator computes nothing and dispatches instead, so each warmer gets its
own budget and one slow warmer costs one warmer rather than every warmer behind
it. The four per-realm tasks already existed on the `background` queue with
their own locks; the inline path called the bare `warm_*` functions and so
bypassed those locks entirely, letting a startup warm run on top of a Beat warm
of the same realm.

Runbook: agents/runbooks/runbook-startup-warm-fanout-2026-08-26.md
"""

from unittest import mock

from django.test import TestCase

from warships.models import VALID_REALMS

WARMER_TASKS = (
    "warm_hot_entity_caches_task",
    "bulk_load_entity_caches_task",
    "warm_player_distributions_task",
    "warm_player_correlations_task",
)


class StartupWarmFanoutTests(TestCase):
    def _patched(self):
        """Patch every warmer's apply_async plus the enrichment kickstart."""
        patches = {
            name: mock.patch(f"warships.tasks.{name}.apply_async")
            for name in WARMER_TASKS
        }
        patches["enrich"] = mock.patch(
            "warships.tasks.enrich_player_data_task.apply_async")
        return patches

    def test_orchestrator_computes_nothing_at_all(self):
        # THE LOAD-BEARING CONTRACT. Anything heavy left on the orchestrator's
        # own budget is a single point of failure for every warmer behind it.
        # The inline version never got past the first realm, so this is the
        # assertion that would have caught it.
        from warships import tasks

        with mock.patch("warships.tasks.call_command") as call_command, \
                mock.patch("warships.data.warm_hot_entity_caches") as hot, \
                mock.patch("warships.data.bulk_load_entity_caches") as bulk, \
                mock.patch("warships.data.warm_player_distributions") as dist, \
                mock.patch("warships.data.warm_player_correlations") as corr, \
                mock.patch("warships.tasks.enrich_player_data_task.apply_async"), \
                mock.patch("warships.tasks.warm_hot_entity_caches_task.apply_async"), \
                mock.patch("warships.tasks.bulk_load_entity_caches_task.apply_async"), \
                mock.patch("warships.tasks.warm_player_distributions_task.apply_async"), \
                mock.patch("warships.tasks.warm_player_correlations_task.apply_async"):
            result = tasks.startup_warm_caches_task()

        call_command.assert_not_called()
        for computed in (hot, bulk, dist, corr):
            computed.assert_not_called()
        self.assertEqual(result["status"], "completed")

    def test_dispatches_one_subtask_per_realm_and_warmer(self):
        # The whole point: each (realm, warmer) pair gets its own 540s budget,
        # and each subtask honours the per-realm lock the inline path bypassed.
        from warships import tasks

        patches = self._patched()
        with patches["warm_hot_entity_caches_task"] as hot, \
                patches["bulk_load_entity_caches_task"] as bulk, \
                patches["warm_player_distributions_task"] as dist, \
                patches["warm_player_correlations_task"] as corr, \
                patches["enrich"]:
            result = tasks.startup_warm_caches_task()

        for warmer in (hot, bulk, dist, corr):
            dispatched = {
                call.kwargs["kwargs"]["realm"] for call in warmer.call_args_list
            }
            self.assertEqual(
                dispatched, set(VALID_REALMS),
                "every realm must be dispatched, not just the ones that fit")

        self.assertEqual(
            result["dispatched"], len(VALID_REALMS) * len(WARMER_TASKS))

    def test_na_is_dispatched_even_though_it_sorts_last(self):
        # The inline loop walked realms in sorted order and died in the first,
        # so `na` -- the DEFAULT realm, and the one most visitors land on --
        # was never warmed on any run in the journal. Pin it by name.
        from warships import tasks

        patches = self._patched()
        with patches["warm_player_correlations_task"] as corr, \
                patches["warm_hot_entity_caches_task"], \
                patches["bulk_load_entity_caches_task"], \
                patches["warm_player_distributions_task"], \
                patches["enrich"]:
            tasks.startup_warm_caches_task()

        realms = [c.kwargs["kwargs"]["realm"] for c in corr.call_args_list]
        self.assertIn("na", realms)

    def test_enrichment_kickstart_still_dispatched(self):
        # The kickstart sat AFTER the inline warm, so a soft-limit kill meant it
        # never ran. Beat's `player-enrichment-kickstart` covers the same ground,
        # which is why the loss was survivable -- but it is cheap to keep and the
        # dispatcher now always reaches it.
        from warships import tasks

        patches = self._patched()
        with patches["warm_hot_entity_caches_task"], \
                patches["bulk_load_entity_caches_task"], \
                patches["warm_player_distributions_task"], \
                patches["warm_player_correlations_task"], \
                patches["enrich"] as enrich:
            tasks.startup_warm_caches_task()

        enrich.assert_called_once()

    def test_a_failed_dispatch_does_not_strand_the_remaining_warmers(self):
        # One broker hiccup must not cost the other eleven warmers. The inline
        # version had the same shape of problem for a different reason.
        from warships import tasks

        patches = self._patched()
        with patches["warm_hot_entity_caches_task"] as hot, \
                patches["bulk_load_entity_caches_task"], \
                patches["warm_player_distributions_task"], \
                patches["warm_player_correlations_task"] as corr, \
                patches["enrich"] as enrich:
            hot.side_effect = RuntimeError("broker down")
            result = tasks.startup_warm_caches_task()

        self.assertEqual(
            {c.kwargs["kwargs"]["realm"] for c in corr.call_args_list},
            set(VALID_REALMS))
        enrich.assert_called_once()
        self.assertEqual(result["failed"], len(VALID_REALMS))

    def test_heavy_warmers_never_start_closer_than_the_heavy_spacing(self):
        # 2026-11-23: at one 20s spacing for all twelve, the three realms'
        # correlation warmers (239-381s each) ran together after a deploy and
        # held the 2-vCPU database at 0% idle for seven minutes. The minutes-long
        # warmers must follow each other, and must start after every light one.
        from warships import tasks

        patches = self._patched()
        with patches["warm_hot_entity_caches_task"] as hot, \
                patches["bulk_load_entity_caches_task"] as bulk, \
                patches["warm_player_distributions_task"] as dist, \
                patches["warm_player_correlations_task"] as corr, \
                patches["enrich"]:
            tasks.startup_warm_caches_task()

        def countdowns(*warmers):
            return sorted(
                c.kwargs["countdown"] for w in warmers for c in w.call_args_list)

        light = countdowns(hot, bulk)
        heavy = countdowns(dist, corr)
        self.assertEqual(len(heavy), 2 * len(VALID_REALMS))
        self.assertGreater(heavy[0], light[-1])
        gaps = [b - a for a, b in zip(heavy, heavy[1:])]
        self.assertEqual(
            gaps, [tasks.STARTUP_WARM_HEAVY_SPACING_SECONDS] * (len(heavy) - 1))
        self.assertGreaterEqual(tasks.STARTUP_WARM_HEAVY_SPACING_SECONDS, 400)

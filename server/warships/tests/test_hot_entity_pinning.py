"""Guards for hot-entity pinned-player warming.

Regression cover for 2026-05-28: the hot-entity warmer used to default to
perpetually warming a single personal account ('lil_boots'). Pinning is now
opt-in via HOT_ENTITY_PINNED_PLAYER_NAMES — no specific record is warmed unless
explicitly configured.

NOTE: this file is not part of the 4-file CI release gate (.github/workflows/
ci.yml); it runs in a full local `pytest` sweep. Kept here as the topical home
for hot-entity pin behavior rather than shoehorned into an unrelated gate file.
"""
from unittest import mock

from django.test import TestCase

from warships import data
from warships.data import HOT_ENTITY_PINNED_PLAYER_NAMES, _get_pinned_player_ids


class HotEntityPinDefaultTests(TestCase):
    def test_no_player_pinned_by_default(self):
        # With HOT_ENTITY_PINNED_PLAYER_NAMES unset (as in tests + default prod),
        # the pin list is empty so the warmer keeps no specific account hot.
        self.assertEqual(HOT_ENTITY_PINNED_PLAYER_NAMES, [])

    def test_get_pinned_player_ids_returns_empty_when_unconfigured(self):
        with mock.patch.object(data, 'HOT_ENTITY_PINNED_PLAYER_NAMES', []):
            self.assertEqual(_get_pinned_player_ids(realm='na'), [])

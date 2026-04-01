"""Realm isolation unit tests — verify that multi-realm support
correctly isolates data, cache keys, API routing, and view extraction."""

from django.core.cache import cache
from django.db import IntegrityError
from django.test import RequestFactory, TestCase

from warships.api.client import get_base_url
from warships.models import (
    DEFAULT_REALM, VALID_REALMS, Player, Clan,
    realm_cache_key,
)
from warships.tasks import _clan_crawl_lock_key
from warships.views import _get_realm


class RealmModelConstraintTests(TestCase):
    """Phase 5, Test 1: same entity_id can exist in different realms."""

    def test_same_player_id_different_realms(self):
        Player.objects.create(name='NA Player', player_id=9999, realm='na')
        Player.objects.create(name='EU Player', player_id=9999, realm='eu')
        self.assertEqual(Player.objects.filter(player_id=9999).count(), 2)

    def test_duplicate_player_id_same_realm_raises(self):
        Player.objects.create(name='P1', player_id=9999, realm='na')
        with self.assertRaises(IntegrityError):
            Player.objects.create(name='P2', player_id=9999, realm='na')

    def test_same_clan_id_different_realms(self):
        Clan.objects.create(name='NA Clan', clan_id=5555, realm='na')
        Clan.objects.create(name='EU Clan', clan_id=5555, realm='eu')
        self.assertEqual(Clan.objects.filter(clan_id=5555).count(), 2)

    def test_duplicate_clan_id_same_realm_raises(self):
        Clan.objects.create(name='C1', clan_id=5555, realm='na')
        with self.assertRaises(IntegrityError):
            Clan.objects.create(name='C2', clan_id=5555, realm='na')


class RealmAPIClientRoutingTests(TestCase):
    """Phase 5, Test 2: API base URL routes to correct regional endpoint."""

    def test_na_base_url(self):
        self.assertIn('api.worldofwarships.com', get_base_url('na'))

    def test_eu_base_url(self):
        self.assertIn('api.worldofwarships.eu', get_base_url('eu'))

    def test_unknown_realm_falls_back_to_na(self):
        self.assertEqual(get_base_url('na'), get_base_url('nonexistent'))


class RealmCacheKeyIsolationTests(TestCase):
    """Phase 5, Test 3: cache keys are scoped per realm."""

    def test_cache_keys_differ_by_realm(self):
        self.assertNotEqual(
            realm_cache_key('na', 'foo'),
            realm_cache_key('eu', 'foo'),
        )

    def test_cache_key_contains_realm(self):
        self.assertIn('na', realm_cache_key('na', 'bar'))
        self.assertIn('eu', realm_cache_key('eu', 'bar'))


class RealmViewExtractionTests(TestCase):
    """Phase 5, Test 4: _get_realm extracts and validates realm from query params."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_eu_realm_extracted(self):
        request = self.factory.get('/api/player/123/', {'realm': 'eu'})
        self.assertEqual(_get_realm(request), 'eu')

    def test_na_realm_extracted(self):
        request = self.factory.get('/api/player/123/', {'realm': 'na'})
        self.assertEqual(_get_realm(request), 'na')

    def test_invalid_realm_falls_back_to_na(self):
        request = self.factory.get('/api/player/123/', {'realm': 'invalid'})
        self.assertEqual(_get_realm(request), DEFAULT_REALM)

    def test_missing_realm_defaults_to_na(self):
        request = self.factory.get('/api/player/123/')
        self.assertEqual(_get_realm(request), DEFAULT_REALM)

    def test_uppercase_realm_normalised(self):
        request = self.factory.get('/api/player/123/', {'realm': 'EU'})
        self.assertEqual(_get_realm(request), 'eu')


class RealmLockKeyIsolationTests(TestCase):
    """Phase 5, Test 6: crawl lock keys are scoped per realm."""

    def test_lock_keys_differ_by_realm(self):
        self.assertNotEqual(
            _clan_crawl_lock_key('na'),
            _clan_crawl_lock_key('eu'),
        )

    def test_lock_key_contains_realm(self):
        self.assertIn('na', _clan_crawl_lock_key('na'))
        self.assertIn('eu', _clan_crawl_lock_key('eu'))


class RealmEntityVisitTests(TestCase):
    """Phase 5, Test for EntityVisit realm field."""

    def setUp(self):
        cache.clear()

    def test_entity_visit_records_realm_from_route_path(self):
        Player.objects.create(name='EU Player', player_id=2001, realm='eu')
        response = self.client.post(
            '/api/analytics/entity-view/',
            data={
                'event_uuid': 'aaaaaaaa-1111-2222-3333-444444444444',
                'occurred_at': '2026-03-31T12:00:00Z',
                'entity_type': 'player',
                'entity_id': 2001,
                'entity_name': 'EU Player',
                'route_path': '/player/EU%20Player?realm=eu',
                'visitor_key': 'v-eu-1',
                'session_key': 's-eu-1',
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)

        from warships.models import EntityVisitEvent, EntityVisitDaily
        event = EntityVisitEvent.objects.get(entity_id=2001)
        self.assertEqual(event.realm, 'eu')

        daily = EntityVisitDaily.objects.get(entity_id=2001)
        self.assertEqual(daily.realm, 'eu')

    def test_entity_visit_defaults_to_na_without_realm_param(self):
        Player.objects.create(name='NA Player', player_id=2002, realm='na')
        self.client.post(
            '/api/analytics/entity-view/',
            data={
                'event_uuid': 'bbbbbbbb-1111-2222-3333-444444444444',
                'occurred_at': '2026-03-31T12:00:00Z',
                'entity_type': 'player',
                'entity_id': 2002,
                'entity_name': 'NA Player',
                'route_path': '/player/NA%20Player',
                'visitor_key': 'v-na-1',
                'session_key': 's-na-1',
            },
            content_type='application/json',
        )
        from warships.models import EntityVisitEvent
        event = EntityVisitEvent.objects.get(entity_id=2002)
        self.assertEqual(event.realm, 'na')

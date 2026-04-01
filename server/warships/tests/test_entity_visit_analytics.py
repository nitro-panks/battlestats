import uuid
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from warships.data import get_recently_viewed_player_ids
from warships.landing import LANDING_RECENT_PLAYERS_DIRTY_KEY
from warships.models import Clan, EntityVisitDaily, EntityVisitEvent, Player, realm_cache_key


class EntityVisitAnalyticsTests(TestCase):
    def setUp(self):
        cache.clear()

    def _payload(self, **overrides):
        payload = {
            'event_uuid': str(uuid.uuid4()),
            'occurred_at': timezone.now().isoformat(),
            'entity_type': 'player',
            'entity_id': 1001,
            'entity_slug': 'player-one',
            'entity_name': 'Player One',
            'route_path': '/player/player-one',
            'referrer_path': '/',
            'source': 'web_first_party',
            'visitor_key': 'visitor-1',
            'session_key': 'session-1',
        }
        payload.update(overrides)
        return payload

    def test_entity_view_ingest_records_event_and_daily_aggregate(self):
        Player.objects.create(name='Player One', player_id=1001)

        response = self.client.post(
            '/api/analytics/entity-view/',
            data=self._payload(),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(EntityVisitEvent.objects.count(), 1)
        daily = EntityVisitDaily.objects.get(
            entity_type='player', entity_id=1001)
        self.assertEqual(daily.views_raw, 1)
        self.assertEqual(daily.views_deduped, 1)
        self.assertEqual(daily.unique_visitors, 1)
        self.assertEqual(daily.unique_sessions, 1)

        player = Player.objects.get(player_id=1001)
        self.assertIsNotNone(player.last_lookup)
        self.assertEqual(get_recently_viewed_player_ids(), [1001])
        self.assertIsNotNone(cache.get(realm_cache_key(
            'na', LANDING_RECENT_PLAYERS_DIRTY_KEY)))

    def test_entity_view_ingest_uses_realm_from_route_path_for_recent_player_updates(self):
        Player.objects.create(name='NA Player', player_id=1001, realm='na')
        Player.objects.create(name='EU Player', player_id=1001, realm='eu')

        response = self.client.post(
            '/api/analytics/entity-view/',
            data=self._payload(route_path='/player/player-one?realm=eu'),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(Player.objects.get(
            player_id=1001, realm='na').last_lookup)
        self.assertIsNotNone(Player.objects.get(
            player_id=1001, realm='eu').last_lookup)
        self.assertEqual(get_recently_viewed_player_ids(realm='eu'), [1001])
        self.assertEqual(get_recently_viewed_player_ids(realm='na'), [])

    def test_entity_view_ingest_accepts_no_trailing_slash(self):
        Player.objects.create(name='Player One', player_id=1001)

        response = self.client.post(
            '/api/analytics/entity-view',
            data=self._payload(),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(EntityVisitEvent.objects.count(), 1)
        daily = EntityVisitDaily.objects.get(
            entity_type='player', entity_id=1001)
        self.assertEqual(daily.views_raw, 1)

    def test_entity_view_ingest_applies_cooldown_dedupe(self):
        Player.objects.create(name='Player One', player_id=1001)
        now = timezone.now()

        first_response = self.client.post(
            '/api/analytics/entity-view/',
            data=self._payload(occurred_at=now.isoformat()),
            content_type='application/json',
        )
        second_response = self.client.post(
            '/api/analytics/entity-view/',
            data=self._payload(
                occurred_at=(now + timedelta(minutes=10)).isoformat(),
                event_uuid=str(uuid.uuid4()),
            ),
            content_type='application/json',
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(EntityVisitEvent.objects.count(), 2)
        self.assertEqual(EntityVisitEvent.objects.filter(
            counted_in_deduped_views=True).count(), 1)
        daily = EntityVisitDaily.objects.get(
            entity_type='player', entity_id=1001)
        self.assertEqual(daily.views_raw, 2)
        self.assertEqual(daily.views_deduped, 1)
        self.assertEqual(daily.unique_visitors, 1)
        self.assertEqual(daily.unique_sessions, 1)

    def test_entity_view_ingest_ignores_duplicate_event_uuid(self):
        Player.objects.create(name='Player One', player_id=1001)
        payload = self._payload()

        first_response = self.client.post(
            '/api/analytics/entity-view/',
            data=payload,
            content_type='application/json',
        )
        second_response = self.client.post(
            '/api/analytics/entity-view/',
            data=payload,
            content_type='application/json',
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(EntityVisitEvent.objects.count(), 1)
        daily = EntityVisitDaily.objects.get(
            entity_type='player', entity_id=1001)
        self.assertEqual(daily.views_raw, 1)
        self.assertEqual(daily.views_deduped, 1)

    def test_entity_view_ingest_ignores_bot_user_agents(self):
        Player.objects.create(name='Player One', player_id=1001)

        response = self.client.post(
            '/api/analytics/entity-view/',
            data=self._payload(),
            content_type='application/json',
            HTTP_USER_AGENT='Googlebot/2.1',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(EntityVisitEvent.objects.count(), 0)
        self.assertEqual(EntityVisitDaily.objects.count(), 0)

    def test_top_entities_returns_ranked_players(self):
        Player.objects.create(name='Player One', player_id=1001)
        Player.objects.create(name='Player Two', player_id=1002)
        today = timezone.now().date()
        EntityVisitDaily.objects.create(
            date=today,
            entity_type='player',
            entity_id=1001,
            entity_name_snapshot='Old Player One',
            views_raw=3,
            views_deduped=3,
            unique_visitors=3,
            unique_sessions=3,
            source_first_party_views=3,
        )
        EntityVisitDaily.objects.create(
            date=today,
            entity_type='player',
            entity_id=1002,
            entity_name_snapshot='Old Player Two',
            views_raw=5,
            views_deduped=5,
            unique_visitors=5,
            unique_sessions=5,
            source_first_party_views=5,
        )
        EntityVisitEvent.objects.create(
            event_uuid=uuid.uuid4(),
            occurred_at=timezone.now(),
            event_date=today,
            entity_type='player',
            entity_id=1001,
            entity_name_snapshot='Player One',
            entity_slug_snapshot='player-one',
            route_path='/player/player-one',
            referrer_path='/',
            visitor_key_hash='v1',
            session_key_hash='s1',
        )
        EntityVisitEvent.objects.create(
            event_uuid=uuid.uuid4(),
            occurred_at=timezone.now(),
            event_date=today,
            entity_type='player',
            entity_id=1002,
            entity_name_snapshot='Player Two',
            entity_slug_snapshot='player-two',
            route_path='/player/player-two',
            referrer_path='/',
            visitor_key_hash='v2',
            session_key_hash='s2',
        )

        response = self.client.get(
            '/api/analytics/top-entities/?entity_type=player&period=7d&metric=views_deduped&limit=2')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload[0]['entity_id'], 1002)
        self.assertEqual(payload[0]['entity_name'], 'Player Two')
        self.assertEqual(payload[1]['entity_id'], 1001)

    def test_top_entities_returns_ranked_clans(self):
        Clan.objects.create(name='Clan One', tag='ONE', clan_id=2001)
        Clan.objects.create(name='Clan Two', tag='TWO', clan_id=2002)
        today = timezone.now().date()
        EntityVisitDaily.objects.create(
            date=today,
            entity_type='clan',
            entity_id=2001,
            entity_name_snapshot='Clan One',
            views_raw=2,
            views_deduped=2,
            unique_visitors=2,
            unique_sessions=2,
            source_first_party_views=2,
        )
        EntityVisitDaily.objects.create(
            date=today,
            entity_type='clan',
            entity_id=2002,
            entity_name_snapshot='Clan Two',
            views_raw=4,
            views_deduped=4,
            unique_visitors=4,
            unique_sessions=4,
            source_first_party_views=4,
        )

        response = self.client.get(
            '/api/analytics/top-entities/?entity_type=clan&period=7d&metric=views_deduped&limit=2')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload[0]['entity_id'], 2002)
        self.assertEqual(payload[0]['entity_name'], 'Clan Two')
        self.assertEqual(payload[1]['entity_id'], 2001)

    def test_top_entities_accepts_no_trailing_slash(self):
        Player.objects.create(name='Player One', player_id=1001)
        today = timezone.now().date()
        EntityVisitDaily.objects.create(
            date=today,
            entity_type='player',
            entity_id=1001,
            entity_name_snapshot='Player One',
            views_raw=4,
            views_deduped=4,
            unique_visitors=4,
            unique_sessions=4,
            source_first_party_views=4,
        )

        response = self.client.get(
            '/api/analytics/top-entities?entity_type=player&period=7d&metric=views_deduped&limit=1')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]['entity_id'], 1001)

"""Operator/internal IP exclusion for first-party analytics + proxy-aware
client IP resolution (X-Forwarded-For) for the streamer submission audit trail.
"""

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from warships.client_ip import get_client_ip
from warships.models import EntityVisitEvent, Player, StreamerSubmission


class GetClientIpTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_prefers_left_most_x_forwarded_for_hop(self):
        request = self.factory.post(
            '/x', HTTP_X_FORWARDED_FOR='203.0.113.7, 10.0.0.1', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(get_client_ip(request), '203.0.113.7')

    def test_falls_back_to_remote_addr_without_forwarded_header(self):
        request = self.factory.post('/x', REMOTE_ADDR='198.51.100.4')
        self.assertEqual(get_client_ip(request), '198.51.100.4')


def _visit_payload(**overrides):
    payload = {
        'event_uuid': 'cccccccc-1111-2222-3333-444444444444',
        'occurred_at': '2026-03-31T12:00:00Z',
        'entity_type': 'player',
        'entity_id': 3001,
        'entity_name': 'Watched Player',
        'route_path': '/player/Watched%20Player?realm=na',
        'visitor_key': 'v-ip-1',
        'session_key': 's-ip-1',
    }
    payload.update(overrides)
    return payload


@override_settings(ANALYTICS_IGNORE_IPS={'203.0.113.7'})
class AnalyticsIgnoreIpTests(TestCase):
    def setUp(self):
        cache.clear()
        Player.objects.create(name='Watched Player', player_id=3001, realm='na')

    def test_ignored_ip_visit_is_dropped(self):
        response = self.client.post(
            '/api/analytics/entity-view/',
            data=_visit_payload(),
            content_type='application/json',
            HTTP_X_FORWARDED_FOR='203.0.113.7',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['accepted'])
        self.assertEqual(response.json()['reason'], 'ignored_ip')
        self.assertEqual(EntityVisitEvent.objects.filter(entity_id=3001).count(), 0)

    def test_non_ignored_ip_visit_is_recorded(self):
        response = self.client.post(
            '/api/analytics/entity-view/',
            data=_visit_payload(),
            content_type='application/json',
            HTTP_X_FORWARDED_FOR='198.51.100.9',
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(EntityVisitEvent.objects.filter(entity_id=3001).count(), 1)


class StreamerSubmitterIpTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_submitter_ip_uses_forwarded_client_not_proxy(self):
        response = self.client.post(
            '/api/streamer-submissions/',
            data={
                'ign': 'TestCaptain',
                'realm': 'na',
                'twitch_handle': 'testcaptain',
                'twitch_url': 'https://twitch.tv/testcaptain',
            },
            content_type='application/json',
            HTTP_X_FORWARDED_FOR='203.0.113.50, 127.0.0.1',
            REMOTE_ADDR='127.0.0.1',
        )
        self.assertEqual(response.status_code, 201)
        submission = StreamerSubmission.objects.get(ign='TestCaptain')
        self.assertEqual(submission.submitter_ip, '203.0.113.50')

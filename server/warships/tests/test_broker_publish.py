"""Guards on the request thread's only broker interaction.

The 2026-09-08 ops alert was two gunicorn workers killed mid-request while
blocked publishing a lazy refresh. Both halves of that fix are asserted here:
the publish is time-bounded, and the arbiter leaves no broker socket for the
forked workers to fight over.
"""
import importlib.util
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from warships import broker
from warships.tasks import update_clan_members_task
from warships.views import _delay_task_safely


class BoundedBrokerConnectionTests(TestCase):
    def test_publish_connection_carries_a_python_level_socket_timeout(self):
        """The bound must be settimeout, not py-amqp's read_timeout.

        read_timeout reaches only SO_RCVTIMEO, which CPython ignores on a
        blocking socket: a publish carrying it hung past 45s against a broker
        wedged mid-exchange.declare, where settimeout returned in ~3x the
        budget. Asserting the socket call is what keeps that distinction.
        """
        sock = MagicMock(spec=socket.socket)
        connection = MagicMock()
        connection.connection.transport.sock = sock

        with patch.object(broker.celery_app, 'connection_for_write',
                          return_value=connection) as mock_factory:
            self.assertIs(broker.bounded_broker_connection(), connection)

        connection.connect.assert_called_once()
        sock.settimeout.assert_called_once_with(
            broker.BROKER_PUBLISH_TIMEOUT_SECONDS)
        self.assertEqual(
            mock_factory.call_args.kwargs['connect_timeout'],
            broker.BROKER_PUBLISH_TIMEOUT_SECONDS,
        )

    def test_connection_is_released_when_the_socket_cannot_be_bounded(self):
        connection = MagicMock()
        connection.connect.side_effect = OSError('broker down')

        with patch.object(broker.celery_app, 'connection_for_write',
                          return_value=connection):
            with self.assertRaises(OSError):
                broker.bounded_broker_connection()

        connection.release.assert_called_once()


class PublishTaskTests(TestCase):
    def test_broker_failure_is_swallowed_and_reported(self):
        """A lazy refresh costs freshness, never the response."""
        with patch.object(broker, 'bounded_broker_connection',
                          side_effect=TimeoutError('timed out')):
            self.assertFalse(
                broker.publish_task(update_clan_members_task, clan_id=1))

    def test_successful_publish_reports_true(self):
        with patch.object(update_clan_members_task, 'apply_async') as dispatch:
            self.assertTrue(
                broker.publish_task(update_clan_members_task, clan_id=1,
                                    realm='na'))
        self.assertEqual(dispatch.call_args.kwargs['kwargs'],
                         {'clan_id': 1, 'realm': 'na'})
        self.assertFalse(dispatch.call_args.kwargs['retry'])


class LazyRefreshDedupTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_failed_dispatch_frees_the_dedup_key_for_the_next_request(self):
        """Otherwise a broker blip mutes the refresh for the whole window."""
        with patch('warships.views.publish_task', return_value=False):
            _delay_task_safely(update_clan_members_task, clan_id=42, realm='na')
        with patch('warships.views.publish_task', return_value=True) as retry:
            _delay_task_safely(update_clan_members_task, clan_id=42, realm='na')
        retry.assert_called_once()

    def test_successful_dispatch_holds_the_dedup_key(self):
        with patch('warships.views.publish_task', return_value=True):
            _delay_task_safely(update_clan_members_task, clan_id=43, realm='na')
        with patch('warships.views.publish_task', return_value=True) as second:
            _delay_task_safely(update_clan_members_task, clan_id=43, realm='na')
        second.assert_not_called()


class GunicornForkHygieneTests(TestCase):
    """when_ready runs in the ARBITER, before any worker is forked.

    A broker socket left open there is inherited by every worker, and several
    processes interleaving frames on one fd is what wedged two workers on
    2026-09-08. close() alone is not enough: it drops the app's reference to the
    pool without closing the socket.
    """

    @staticmethod
    def _load_gunicorn_conf():
        path = Path(__file__).resolve().parents[2] / 'gunicorn.conf.py'
        spec = importlib.util.spec_from_file_location('gunicorn_conf', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_when_ready_leaves_no_broker_connection_to_inherit(self):
        conf = self._load_gunicorn_conf()
        fake_app = MagicMock()
        with patch('warships.tasks.startup_warm_caches_task.apply_async'), \
                patch('battlestats.celery.app', fake_app):
            conf.when_ready(MagicMock())

        fake_app.amqp.producer_pool.force_close_all.assert_called_once()
        fake_app.pool.force_close_all.assert_called_once()
        fake_app.close.assert_called_once()

    def test_a_failed_warm_dispatch_still_closes_the_connection(self):
        conf = self._load_gunicorn_conf()
        fake_app = MagicMock()
        with patch('warships.tasks.startup_warm_caches_task.apply_async',
                   side_effect=OSError('broker down')), \
                patch('battlestats.celery.app', fake_app):
            conf.when_ready(MagicMock())

        fake_app.close.assert_called_once()
